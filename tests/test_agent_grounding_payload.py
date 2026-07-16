"""Unit tests for the R13a grounding/coverage payload in apply_gates (plan Unit 3).

No LLM and no DB: ``known_tables`` is stubbed and the crafted ``RetrievalContext`` carries no ERA
calls with ids, so ``precedent_dialect`` never queries. Exercises the case-insensitive per-table
status, canonical display name, and the coarse grounding-strength label.
"""
from __future__ import annotations

import pytest

from text2sql import agent as A
from text2sql.agent import Text2SQLResult, apply_gates
from text2sql.tools import RetrievalContext


class DummyConn:
    pass


def _ctx(retrieved=(), era=0.0, schema=0.0) -> RetrievalContext:
    ctx = RetrievalContext(DummyConn())
    ctx.retrieved_tables = set(retrieved)
    ctx.calls = []
    if era:
        ctx.calls.append({"kb": "era_knowledge", "query": "q", "top_cosine": era,
                          "top_rrf": 0.0, "ids": []})  # ids=[] → precedent_dialect skips DB
    if schema:
        ctx.calls.append({"kb": "schema_tables", "query": "q", "top_cosine": schema,
                          "top_rrf": 0.0, "ids": []})
    return ctx


@pytest.fixture(autouse=True)
def stub_known_tables(monkeypatch):
    # Catalog stores upper case; the model writes lower case — the derivation must be case-insensitive.
    monkeypatch.setattr(A, "known_tables", lambda conn: {"SALES", "CUSTOMERS"})


def test_grounded_table_uses_canonical_case_and_flags():
    ctx = _ctx(retrieved={"SALES"}, schema=0.5)
    out = apply_gates(Text2SQLResult(sql="SELECT id FROM sales"), ctx, DummyConn())
    by_name = {d["name"]: d for d in out.grounding}
    assert "SALES" in by_name                       # canonical catalog casing, not the model's 'sales'
    assert by_name["SALES"]["in_kb"] and by_name["SALES"]["retrieved"]
    assert out.grounding_strength == "schema_only"  # era<0.45, schema>=0.40


def test_named_but_not_retrieved_flagged():
    ctx = _ctx(retrieved=set(), schema=0.5)
    out = apply_gates(Text2SQLResult(sql="SELECT id FROM customers"), ctx, DummyConn())
    row = next(d for d in out.grounding if d["name"] == "CUSTOMERS")
    assert row["in_kb"] is True and row["retrieved"] is False


def test_table_absent_from_kb():
    ctx = _ctx(retrieved=set(), schema=0.5)
    out = apply_gates(Text2SQLResult(sql="SELECT id FROM ghost_table"), ctx, DummyConn())
    row = next(d for d in out.grounding if d["name"].lower() == "ghost_table")
    assert row["in_kb"] is False


def test_grounding_strength_precedent_strong():
    ctx = _ctx(retrieved={"SALES"}, era=0.6, schema=0.5)
    out = apply_gates(Text2SQLResult(sql="SELECT id FROM sales"), ctx, DummyConn())
    assert out.grounding_strength == "precedent_strong"


def test_grounding_strength_none_when_both_weak():
    ctx = _ctx(retrieved={"SALES"}, era=0.1, schema=0.2)
    out = apply_gates(Text2SQLResult(sql="SELECT id FROM sales"), ctx, DummyConn())
    assert out.grounding_strength is None


def test_backward_compatible_defaults():
    r = Text2SQLResult()  # existing consumers see empty/None, never a KeyError
    assert r.grounding == [] and r.grounding_strength is None
    assert r.era_top_cosine is None and r.schema_top_cosine is None
