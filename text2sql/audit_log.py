"""Structured per-request audit logger for the Text-to-SQL agent.

Every call to generate_sql() gets a unique 8-hex request_id assigned via
new_request(). That ID is propagated automatically through contextvars so
every log line — across agent, gates, tools, and retrieval — is correlated
without threading the ID through function signatures.

Log levels:
  INFO  — decisions, gate verdicts, tool summaries  (always on)
  WARNING — declines, missing results, PII masked    (always on)
  DEBUG — token lists, raw scores, parse internals  (T2S_AUDIT_LEVEL=DEBUG)

Output goes to stderr so it doesn't pollute CLI stdout. Control the level
with the T2S_AUDIT_LEVEL environment variable (default: INFO).
"""
from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("t2s_request_id", default="--------")


def new_request() -> str:
    """Assign a fresh 8-hex request ID to the current context and return it."""
    rid = uuid4().hex[:8]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class _ReqFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.req = _request_id.get()
        return super().format(record)


def _build_root() -> logging.Logger:
    level = getattr(logging, os.environ.get("T2S_AUDIT_LEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger("text2sql.audit")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ReqFormatter(
        fmt="[%(asctime)s] [%(req)s] %(name)-34s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    return logger


_build_root()


def get_logger(suffix: str) -> logging.Logger:
    """Return a child logger under text2sql.audit.<suffix>, reusing the root handler."""
    _build_root()
    return logging.getLogger(f"text2sql.audit.{suffix}")
