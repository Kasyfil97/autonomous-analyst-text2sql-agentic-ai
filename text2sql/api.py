"""BRISA FastAPI backend (plan Unit 1).

Wraps the existing ``text2sql`` package behind two surfaces — a semantic table-search engine
and the draft-SQL agent — with shared resources built once at startup. Generated SQL is never
executed by this process.

Security posture = **locked-down demo host** (plan decision): a simple shared-token gate on the
API routes (network restriction is the primary control), an exact-single-origin CORS policy, a
64 KB body cap, and per-request audit correlation. Per-user rate limiting is intentionally
deferred (documented in the plan's Risks); full-catalog schema visibility is an accepted,
documented demo risk — not a production posture.

Run:  ``python -m text2sql.api``  (env: ``BRISA_API_TOKEN`` optional shared token,
``BRISA_FRONTEND_ORIGIN`` CORS origin, plus the usual ``PG_RO_*`` / Bedrock / embedding vars).
"""
from __future__ import annotations

import argparse
import contextlib
import os
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from text2sql import agent as _agent
from text2sql import search_service as _search
from text2sql.audit_log import get_logger, new_request
from text2sql.embedding_service import pg_config

_log = get_logger("api")

MAX_BODY_BYTES = 64_000
MAX_QUERY_CHARS = 2_000  # search query-length bound


def _frontend_origin() -> str:
    return os.getenv("BRISA_FRONTEND_ORIGIN", "http://localhost:3000")


# --------------------------------------------------------------------------
# Injectable resource builders (tests override these to avoid real OIDC / DB)
# --------------------------------------------------------------------------

def build_pool():
    """Read-only connection pool. Raises (via pg_config) if PG_RO_USER is unset —
    surfaced as a clear startup failure rather than a silent degrade."""
    from psycopg2.pool import ThreadedConnectionPool

    return ThreadedConnectionPool(minconn=1, maxconn=8, **pg_config(readonly=True))


def build_session():
    """Federated Bedrock session (OIDC is expensive — built once)."""
    return _agent.get_session()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pool is required for both surfaces — fail fast if it cannot be built.
    app.state.pool = build_pool()
    app.state.session = None
    app.state.session_lock = threading.Lock()

    # Warm caches once so the first request does not race the unlocked module globals.
    conn = app.state.pool.getconn()
    try:
        _agent.known_tables(conn)
        with contextlib.suppress(Exception):
            from text2sql.retrieval import _load_vocab

            _load_vocab(conn, "schema_tables")
    finally:
        app.state.pool.putconn(conn)

    # The Bedrock session is only needed by the agent surface. Attempt it at startup to avoid
    # per-request OIDC, but tolerate failure so the search surface still works without creds.
    try:
        app.state.session = build_session()
    except Exception as exc:  # noqa: BLE001 — search must not depend on Bedrock availability
        _log.warning("api lifespan | Bedrock session not built at startup (%s) — agent route "
                     "will build it lazily", type(exc).__name__)

    _log.info("api lifespan | ready (pool + warm caches; session=%s)",
              "eager" if app.state.session else "lazy")
    yield
    with contextlib.suppress(Exception):
        app.state.pool.closeall()


# --------------------------------------------------------------------------
# Shared dependencies
# --------------------------------------------------------------------------

def require_auth(request: Request) -> None:
    """Simple shared-token gate (locked-down-host posture).

    Token is read from ``BRISA_API_TOKEN`` at call time (so tests can set/unset it). When the
    token is unset the gate is open (the network-restricted host is the control); when set, a
    matching ``Authorization: Bearer <token>`` is required — fail-closed on mismatch.
    """
    token = os.getenv("BRISA_API_TOKEN")
    if not token:
        return
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or header[7:] != token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def get_conn(request: Request):
    """Hand out a pooled read-only connection; return it on every path (incl. errors)."""
    pool = request.app.state.pool
    conn = pool.getconn()
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            pool.putconn(conn)


def get_agent_session(request: Request):
    """Return the shared Bedrock session, building it lazily under a lock if startup skipped it."""
    app = request.app
    if app.state.session is None:
        with app.state.session_lock:
            if app.state.session is None:
                app.state.session = build_session()
    return app.state.session


# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies (carried from web.py's 64 KB cap) before routing."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"error": {"code": "body_too_large",
                                           "message": f"request body exceeds {MAX_BODY_BYTES} bytes"}},
                    )
            except ValueError:
                return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST,
                                    content={"error": {"code": "bad_request",
                                                       "message": "invalid Content-Length"}})
        return await call_next(request)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Assign a per-request audit id (new_request) and echo it as X-Correlation-Id."""

    async def dispatch(self, request: Request, call_next):
        rid = new_request()
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = rid
        return response


# --------------------------------------------------------------------------
# App assembly
# --------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="BRISA", version="0.1.0", lifespan=lifespan)

    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    # Exact single origin; bearer-token auth (not cookies) → credentials off, no wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_frontend_origin()],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN001
        # Generic envelope — never leak raw exception text/stack to the client.
        _log.exception("api | unhandled %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error",
                               "message": "an internal error occurred; see server logs"}},
        )

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    # Surface routers — bodies land in Units 2 (search) and 3 (agent). Auth + pooled conn are
    # wired now so the perimeter is testable and U2/U3 only fill in behavior.
    @app.get("/api/search", dependencies=[Depends(require_auth)])
    async def search(q: str = "", domain: str | None = None, limit: int = 10, conn=Depends(get_conn)):
        if len(q) > MAX_QUERY_CHARS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query too long")
        return _search.search_tables(conn, q, domain=domain, limit=max(1, min(limit, 50)))

    @app.get("/api/search/columns", dependencies=[Depends(require_auth)])
    async def search_columns(table: str, conn=Depends(get_conn)):
        if not table or len(table) > 256:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid table")
        return {"table": table, "columns": _search.table_columns(conn, table)}

    @app.get("/api/search/domains", dependencies=[Depends(require_auth)])
    async def search_domains(conn=Depends(get_conn)):
        return {"domains": _search.list_domains(conn)}

    @app.post("/api/agent/chat", dependencies=[Depends(require_auth)])
    async def agent_chat(conn=Depends(get_conn), session=Depends(get_agent_session)):
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                            detail="agent endpoint lands in Unit 3")

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the BRISA FastAPI backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run("text2sql.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
