"""Endpoint-contract tests for the agent route (plan Unit 3) — injected fake generate_sql, no LLM."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from text2sql import api
from text2sql.agent import Text2SQLResult


class FakePool:
    def getconn(self):
        return object()

    def putconn(self, conn):
        pass

    def closeall(self):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "build_pool", lambda: FakePool())
    monkeypatch.setattr(api, "build_session", lambda: object())
    monkeypatch.setattr(api._agent, "known_tables", lambda conn: {"SALES"})
    # Clear BOTH token names — require_auth dual-reads SAGE_API_TOKEN or BRISA_API_TOKEN.
    monkeypatch.delenv("SAGE_API_TOKEN", raising=False)
    monkeypatch.delenv("BRISA_API_TOKEN", raising=False)

    captured: dict = {}

    def fake_gen(question, session=None, conn=None, attached_tables=None):
        captured.update(question=question, attached=attached_tables)
        return Text2SQLResult(
            sql="-- UNVERIFIED\nSELECT 1",
            explanation="totals per month",
            assumptions=["'active' = status='A'"],
            grounding=[{"name": "SALES", "in_kb": True, "retrieved": True}],
            grounding_strength="schema_only",
            tables_used=["SALES"],
            warnings=[],
        )

    monkeypatch.setattr(api._agent, "generate_sql", fake_gen)
    # Deterministic routing — default to the SQL path; individual tests override to 'other'.
    monkeypatch.setattr(api._orchestrator, "route", lambda q, s: "sql")
    # raise_server_exceptions=False so the registered 500 handler's response is observed.
    with TestClient(api.create_app(), raise_server_exceptions=False) as c:
        yield c, captured


def test_agent_contract_exposes_grounding(client):
    c, cap = client
    resp = c.post("/api/agent/chat", json={"question": "total per bulan"})
    assert resp.status_code == 200
    b = resp.json()
    assert b["sql"].endswith("SELECT 1")
    assert b["explanation"] == "totals per month"      # R11 interpretation region
    assert b["assumptions"] == ["'active' = status='A'"]
    assert b["grounding"][0] == {"name": "SALES", "in_kb": True, "retrieved": True}
    assert b["grounding_strength"] == "schema_only"     # R15: label, never a number
    assert cap["question"] == "total per bulan"


def test_question_required_uses_error_envelope(client):
    c, _ = client
    r = c.post("/api/agent/chat", json={"question": "   "})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"  # uniform envelope (not FastAPI's {detail})


def test_request_validation_uses_error_envelope(client):
    c, _ = client
    r = c.post("/api/agent/chat", json={})  # missing required 'question'
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_decline_is_returned_in_band(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(api._agent, "generate_sql",
                        lambda *a, **k: Text2SQLResult.decline("weak schema coverage"))
    resp = c.post("/api/agent/chat", json={"question": "x"})
    assert resp.status_code == 200
    assert resp.json()["declined"] is True
    assert resp.json()["missing"] == "weak schema coverage"


def test_backend_error_yields_generic_envelope(client, monkeypatch):
    c, _ = client

    def boom(*a, **k):
        raise RuntimeError("bedrock exploded: secret-token-abc123")

    monkeypatch.setattr(api._agent, "generate_sql", boom)
    resp = c.post("/api/agent/chat", json={"question": "x"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret-token-abc123" not in str(body)  # raw exception text never leaks


def test_offtopic_question_routes_to_fallback(client, monkeypatch):
    c, cap = client
    monkeypatch.setattr(api._orchestrator, "route", lambda q, s: "other")  # unrelated to data
    resp = c.post("/api/agent/chat", json={"question": "ceritakan lelucon lucu dong"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["declined"] is True
    assert body["missing"] == api._orchestrator.FALLBACK_MESSAGE
    assert "question" not in cap  # generate_sql was NOT called for an off-topic question


def test_attached_tables_resolved_before_generate(client):
    c, cap = client
    # known_tables = {"SALES"}: 'sales' resolves to canonical SALES; unknown + denylisted are dropped.
    resp = c.post("/api/agent/chat",
                  json={"question": "x", "attached_tables": ["sales", "ghost", "era_tickets"]})
    assert resp.status_code == 200
    assert cap["attached"] == ["SALES"]


class _CountingConn:
    def rollback(self):
        pass


class _CountingPool:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def getconn(self):
        self.acquired += 1
        return _CountingConn()

    def putconn(self, conn):
        self.released += 1

    def closeall(self):
        pass


def test_pool_connection_returned_when_generate_raises(monkeypatch):
    pool = _CountingPool()
    monkeypatch.setattr(api, "build_pool", lambda: pool)
    monkeypatch.setattr(api, "build_session", lambda: object())
    monkeypatch.setattr(api._agent, "known_tables", lambda conn: set())
    monkeypatch.setattr(api._agent, "generate_sql",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.delenv("SAGE_API_TOKEN", raising=False)
    monkeypatch.delenv("BRISA_API_TOKEN", raising=False)
    with TestClient(api.create_app(), raise_server_exceptions=False) as c:
        before = pool.acquired
        resp = c.post("/api/agent/chat", json={"question": "x"})
        assert resp.status_code == 500
        assert pool.acquired == before + 1
        assert pool.released == pool.acquired  # connection returned to the pool despite the error
