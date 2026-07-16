"""DB-dependent tests for the R17a attached-table grounding seam (plan Unit 4).

Verifies the security-critical pieces without invoking Bedrock: server-side re-resolution of
untrusted names against the live catalog + denylist, and that ``table_schema_text`` seeds a real
table into ``retrieved_tables`` through the actual retrieval path.
"""
from __future__ import annotations

import psycopg2
import pytest

from text2sql import api, gates
from text2sql.agent import known_tables
from text2sql.embedding_service import pg_config
from text2sql.tools import RetrievalContext, table_schema_text


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**pg_config(readonly=True))
    yield c
    c.close()


def test_resolve_drops_unknown_and_denylisted(conn):
    real = next(iter(known_tables(conn)))
    resolved = api._resolve_attached_tables(
        [real.lower(), "no_such_table_xyz_123", "era_tickets"], conn)
    assert real in resolved                                        # canonical catalog casing
    assert "no_such_table_xyz_123" not in [r.lower() for r in resolved]
    assert not ({r.lower() for r in resolved} & gates.TABLE_DENYLIST)


def test_resolve_rejects_oversized_and_empty(conn):
    assert api._resolve_attached_tables(["x" * 200], conn) == []
    assert api._resolve_attached_tables([], conn) == []
    assert api._resolve_attached_tables(["   "], conn) == []


def test_resolve_caps_at_ten(conn):
    real = next(iter(known_tables(conn)))
    resolved = api._resolve_attached_tables([real] * 25, conn)
    assert len(resolved) <= 10


def test_table_schema_text_seeds_retrieved(conn):
    # Find a known table that actually has a column dictionary, then confirm it becomes grounded.
    seeded_ctx = None
    for i, name in enumerate(known_tables(conn)):
        if i > 60:
            break
        ctx = RetrievalContext(conn)
        table_schema_text(ctx, name)
        if name.lower() in ctx.retrieved_tables:
            seeded_ctx = (name, ctx)
            break
    assert seeded_ctx is not None, "expected some known table to have columns and seed retrieval"
    name, ctx = seeded_ctx
    assert name.lower() in ctx.retrieved_tables  # grounded via the real retrieval path (R17a)
