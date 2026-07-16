"""Endpoint-contract tests for the search routes (plan Unit 2) — injected fake service, no DB."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from text2sql import api


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
    monkeypatch.setattr(api._agent, "known_tables", lambda conn: set())
    monkeypatch.delenv("SAGE_API_TOKEN", raising=False)

    captured: dict = {}

    def fake_search(conn, q, domain=None, limit=10):
        captured.update(q=q, domain=domain, limit=limit)
        return {"query": q, "domain": domain,
                "results": [{"headline": "Kartu Kredit", "physical_name": "datalake.x",
                             "pii": "unclassified"}],
                "filter_caused_empty": False}

    monkeypatch.setattr(api._search, "search_tables", fake_search)
    monkeypatch.setattr(api._search, "table_columns",
                        lambda conn, t: [{"field_name": "address", "pii": True}])
    monkeypatch.setattr(api._search, "list_domains", lambda conn: ["kartu", "pinjaman"])

    with TestClient(api.create_app()) as c:
        yield c, captured


def test_search_contract_and_no_score_leak(client):
    c, cap = client
    resp = c.get("/api/search", params={"q": "kartu", "domain": "kartu", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["headline"] == "Kartu Kredit"
    assert "score" not in body["results"][0]
    assert cap["limit"] == 5 and cap["domain"] == "kartu"


def test_limit_is_clamped(client):
    c, cap = client
    c.get("/api/search", params={"q": "x", "limit": 999})
    assert cap["limit"] == 50


def test_query_too_long_rejected(client):
    c, _ = client
    assert c.get("/api/search", params={"q": "a" * (api.MAX_QUERY_CHARS + 1)}).status_code == 400


def test_columns_and_domains_routes(client):
    c, _ = client
    cols = c.get("/api/search/columns", params={"table": "datalake.x"}).json()
    assert cols["columns"][0]["pii"] is True
    assert "kartu" in c.get("/api/search/domains").json()["domains"]


def test_columns_rejects_empty_table(client):
    c, _ = client
    assert c.get("/api/search/columns", params={"table": ""}).status_code == 400
