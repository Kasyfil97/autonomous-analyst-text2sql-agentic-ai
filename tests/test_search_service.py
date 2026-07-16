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


def test_search_returns_scoreless_cards(conn):
    out = S.search_tables(conn, "transaksi kartu kredit nasabah", limit=5)
    assert out["results"], "expected relevant tables for a credit-card query"
    for card in out["results"]:
        # R6: no numeric relevance signal in the payload
        assert not ({"score", "dense_cosine", "bm25"} & set(card))
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


def test_list_domains(conn):
    doms = S.list_domains(conn)
    assert "kartu" in doms and "pinjaman" in doms
