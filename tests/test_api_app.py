"""Unit-tier tests for the BRISA FastAPI skeleton (plan Unit 1).

No live DB or Bedrock: the pool and session builders are monkeypatched to fakes, and
``known_tables`` (called during lifespan cache-warming) is stubbed. Mirrors the injected-fake
approach of ``tests/test_web.py``.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from text2sql import api


class FakeConn:
    pass


class FakePool:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def getconn(self):
        self.acquired += 1
        return FakeConn()

    def putconn(self, conn):
        self.released += 1

    def closeall(self):
        pass


@pytest.fixture
def fakes(monkeypatch):
    """Patch the resource builders + cache-warming so no real DB/OIDC is touched."""
    pool = FakePool()
    session_calls = {"n": 0}

    def fake_build_session():
        session_calls["n"] += 1
        return object()

    monkeypatch.setattr(api, "build_pool", lambda: pool)
    monkeypatch.setattr(api, "build_session", fake_build_session)
    monkeypatch.setattr(api._agent, "known_tables", lambda conn: set())
    # Stub the search service so perimeter tests exercise auth/pool, not the real DB handler.
    monkeypatch.setattr(api._search, "search_tables",
                        lambda conn, q, domain=None, limit=10: {"results": []})
    monkeypatch.delenv("BRISA_API_TOKEN", raising=False)
    return {"pool": pool, "session_calls": session_calls}


@pytest.fixture
def client(fakes):
    with TestClient(api.create_app()) as c:
        yield c, fakes


def test_healthz_ok_with_correlation_id(client):
    c, _ = client
    resp = c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert resp.headers.get("X-Correlation-Id")  # audit correlation echoed


def test_pooled_connection_acquired_and_released(client):
    c, fakes = client
    pool = fakes["pool"]
    before = pool.acquired
    # Resolves get_conn (acquire → yield → release) around the stubbed search handler.
    resp = c.get("/api/search", params={"q": "x"})
    assert resp.status_code == 200
    assert pool.acquired == before + 1
    assert pool.released == pool.acquired  # returned on every path


def test_body_over_cap_rejected_before_routing(client):
    c, _ = client
    big = b"x" * (api.MAX_BODY_BYTES + 1)
    resp = c.post("/api/agent/chat", content=big)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "body_too_large"


def test_auth_required_when_token_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("BRISA_API_TOKEN", "s3cret")
    # No / wrong bearer → 401 before the handler runs.
    assert c.get("/api/search", params={"q": "x"}).status_code == 401
    assert c.get("/api/search", params={"q": "x"},
                 headers={"Authorization": "Bearer nope"}).status_code == 401
    # Correct bearer passes auth → reaches the (stubbed) handler → 200, not 401.
    assert c.get("/api/search", params={"q": "x"},
                 headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_auth_open_when_token_unset(client):
    c, _ = client  # fixture deletes BRISA_API_TOKEN → gate open (locked-down-host posture)
    assert c.get("/api/search", params={"q": "x"}).status_code == 200


def test_session_built_once_and_reused(client):
    c, fakes = client
    # Startup built the session eagerly (1). Multiple agent requests must reuse it, not rebuild.
    for _ in range(3):
        c.post("/api/agent/chat")
    assert fakes["session_calls"]["n"] == 1


def test_session_lazy_build_is_single(fakes, monkeypatch):
    """If startup skips the session (build fails once), the first agent request builds it once."""
    # First build (startup) raises; subsequent builds succeed and are counted.
    state = {"first": True, "n": 0}

    def flaky_build_session():
        if state["first"]:
            state["first"] = False
            raise RuntimeError("no creds at startup")
        state["n"] += 1
        return object()

    monkeypatch.setattr(api, "build_session", flaky_build_session)
    with TestClient(api.create_app()) as c:
        for _ in range(3):
            c.post("/api/agent/chat")
    assert state["n"] == 1  # lazy-built exactly once, then reused
