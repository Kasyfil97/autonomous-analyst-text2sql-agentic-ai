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
    # Clear BOTH token names — require_auth dual-reads SAGE_API_TOKEN or BRISA_API_TOKEN.
    monkeypatch.delenv("SAGE_API_TOKEN", raising=False)
    monkeypatch.delenv("BRISA_API_TOKEN", raising=False)

    captured: dict = {}

    def fake_search(conn, q, domain=None, limit=10):
        captured.update(q=q, domain=domain, limit=limit)
        return {"query": q, "domain": domain,
                "results": [{"headline": "Kartu Kredit", "physical_name": "datalake.x",
                             "pii": "unclassified", "relevance": 0.82, "score": 0.0312}],
                "filter_caused_empty": False}

    def fake_search_columns_semantic(conn, q, table=None, domain=None, limit=10):
        captured.update(cq=q, ctable=table, cdomain=domain, climit=limit)
        return {"query": q, "table": table, "domain": domain,
                "results": [{"field_name": "card_no", "table_name": "tx",
                             "physical_name": "datalake.tx.card_no", "pii": True,
                             "relevance": 0.77, "score": 0.0290}],
                "filter_caused_empty": False}

    monkeypatch.setattr(api._search, "search_tables", fake_search)
    monkeypatch.setattr(api._search, "table_columns",
                        lambda conn, t: [{"field_name": "address", "pii": True}])
    monkeypatch.setattr(api._search, "search_columns_semantic", fake_search_columns_semantic)
    monkeypatch.setattr(api._search, "list_domains", lambda conn: ["kartu", "pinjaman"])

    with TestClient(api.create_app()) as c:
        yield c, captured


def test_search_contract_exposes_scores(client):
    c, cap = client
    resp = c.get("/api/search", params={"q": "kartu", "domain": "kartu", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    top = body["results"][0]
    assert top["headline"] == "Kartu Kredit"
    assert top["relevance"] == 0.82 and top["score"] == 0.0312
    assert cap["limit"] == 5 and cap["domain"] == "kartu"


def test_limit_is_clamped(client):
    c, cap = client
    c.get("/api/search", params={"q": "x", "limit": 999})
    assert cap["limit"] == 50


def test_query_too_long_rejected(client):
    c, _ = client
    assert c.get("/api/search", params={"q": "a" * (api.MAX_QUERY_CHARS + 1)}).status_code == 400


def test_table_columns_lookup_and_domains_routes(client):
    c, _ = client
    cols = c.get("/api/search/table/columns", params={"table": "datalake.x"}).json()
    assert cols["columns"][0]["pii"] is True
    assert "kartu" in c.get("/api/search/domains").json()["domains"]


def test_table_columns_lookup_rejects_empty_table(client):
    c, _ = client
    assert c.get("/api/search/table/columns", params={"table": ""}).status_code == 400


def test_column_search_contract_and_filters(client):
    c, cap = client
    resp = c.get("/api/search/columns",
                 params={"q": "nomor kartu", "table": "datalake.tx", "domain": "kartu", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    top = body["results"][0]
    assert top["field_name"] == "card_no"
    assert top["relevance"] == 0.77 and top["score"] == 0.0290
    assert cap["cq"] == "nomor kartu" and cap["ctable"] == "datalake.tx"
    assert cap["cdomain"] == "kartu" and cap["climit"] == 5


def test_column_search_limit_clamped(client):
    c, cap = client
    c.get("/api/search/columns", params={"q": "x", "limit": 999})
    assert cap["climit"] == 50


def test_column_search_query_too_long_rejected(client):
    c, _ = client
    assert c.get("/api/search/columns",
                 params={"q": "a" * (api.MAX_QUERY_CHARS + 1)}).status_code == 400
