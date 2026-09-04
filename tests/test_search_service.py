"""DB-dependent tests for the BRISA search service (plan Unit 2).

Requires the live KB (localhost, read-only role). Mirrors the module-scoped ``conn`` fixture
pattern from ``tests/test_retrieval.py``.
"""
from __future__ import annotations

import psycopg2
import pytest

from text2sql import gates
from text2sql import search_service as S
from text2sql.embedding_service import pg_config


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**pg_config(readonly=True))
    yield c
    c.close()


def test_humanize_strips_prefixes():
    assert S.humanize("datalake.1900_priority_t_detail_transaksi_kartu_kredit").startswith("Priority")
    assert S.humanize("dim_branch_jbr") == "Dim Branch Jbr"


def test_search_returns_cards_with_scores(conn):
    out = S.search_tables(conn, "transaksi kartu kredit nasabah", limit=5)
    assert out["results"], "expected relevant tables for a credit-card query"
    for card in out["results"]:
        # Relevance signals are exposed (supersedes R6); raw bm25/dense_cosine keys are not.
        assert "relevance" in card and "score" in card
        assert not ({"dense_cosine", "bm25"} & set(card))
        assert card["relevance"] is None or 0.0 <= card["relevance"] <= 1.0
        assert isinstance(card["score"], (int, float))
        assert card["headline"] and card["physical_name"]
        assert card["pii"] in ("present", "unclassified")  # R5a: never a bare 'safe'


def test_empty_query_returns_no_results(conn):
    assert S.search_tables(conn, "   ", limit=5)["results"] == []


def test_domain_filter_scopes_results(conn):
    out = S.search_tables(conn, "kartu kredit", domain="kartu", limit=5)
    if out["results"]:
        assert all("kartu" in c["domain_tags"] for c in out["results"])
    else:  # R9: filter emptied it → say so and offer closest matches
        assert out["filter_caused_empty"] and out.get("closest_related")


def test_denylisted_tables_never_returned(conn):
    out = S.search_tables(conn, "era ticket knowledge precedent", limit=25)
    returned = {c["table_name"].lower() for c in out["results"]}
    assert not (returned & gates.TABLE_DENYLIST)


def test_table_columns_shape_and_pii_flag(conn):
    out = S.search_tables(conn, "nasabah alamat email telepon", limit=8)
    cols = []
    for card in out["results"]:
        cols = S.table_columns(conn, card["table_name"])
        if cols:
            break
    assert cols, "expected at least one result table to have a column dictionary"
    for col in cols:
        assert {"field_name", "business_title", "description", "data_type", "pii"} <= set(col)
        assert isinstance(col["pii"], bool)


def test_search_columns_semantic_returns_column_cards_with_scores(conn):
    out = S.search_columns_semantic(conn, "nomor kartu kredit nasabah", limit=5)
    assert out["results"], "expected relevant columns for a credit-card query"
    for card in out["results"]:
        assert not ({"dense_cosine", "bm25"} & set(card))  # raw lane scores stay internal
        assert {"field_name", "table_name", "physical_name", "business_title", "description",
                "data_type", "domain_tags", "pii", "relevance", "score"} <= set(card)
        assert card["relevance"] is None or 0.0 <= card["relevance"] <= 1.0
        assert isinstance(card["score"], (int, float))
        assert isinstance(card["pii"], bool)


def test_search_columns_semantic_empty_query(conn):
    assert S.search_columns_semantic(conn, "   ", limit=5)["results"] == []


def test_search_columns_semantic_table_filter_scopes_results(conn):
    # Discover a real table from an unfiltered pass, then re-run scoped to it. Filter-then-rank
    # means a valid table always returns its own columns — never emptied by ranking.
    seed = S.search_columns_semantic(conn, "nasabah alamat email telepon", limit=10)
    assert seed["results"], "expected a seed result to derive a table filter"
    table = seed["results"][0]["table_name"]
    out = S.search_columns_semantic(conn, "nasabah alamat email telepon", table=table, limit=10)
    assert out["results"], "a scoped search on a valid table must return that table's columns"
    assert all(c["table_name"].lower() == table.lower() for c in out["results"])


def test_search_columns_semantic_unknown_table_returns_empty(conn):
    out = S.search_columns_semantic(conn, "nasabah alamat", table="no_such_table_xyz", limit=10)
    assert out["results"] == []


def test_search_columns_semantic_denylisted_tables_never_returned(conn):
    out = S.search_columns_semantic(conn, "era ticket knowledge precedent", limit=25)
    returned = {c["table_name"].lower() for c in out["results"]}
    assert not (returned & gates.TABLE_DENYLIST)


def test_list_domains(conn):
    doms = S.list_domains(conn)
    assert "kartu" in doms and "pinjaman" in doms


def test_card_redacts_pii_in_description():
    """R4a: free-text on the search path is redacted before serialization (no DB needed)."""
    meta = {
        "id": "datalake.t", "table_name": "t",
        "table_description": "contact admin@bri.co.id or 0812-3456-7890 for access",
        "domain_tags": ["x"], "column_names": ["id"], "n_columns": 1,
    }
    card = S._card(meta)
    assert "admin@bri.co.id" not in card["description"]
    assert "0812-3456-7890" not in card["description"]
    assert "[EMAIL]" in card["description"] or "[PHONE]" in card["description"]
