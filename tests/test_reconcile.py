"""Unit tests for reconciliation + dialect resolution (plan rec #3) — no DB."""
import json

from text2sql import gates
from text2sql import reconcile as RC
from text2sql.agent import SubDraft, Text2SQLResult


def _sd(sub_need, sql, dialect, era=0.6):
    return SubDraft(sub_need=sub_need,
                    result=Text2SQLResult(sql=sql, dialect=dialect,
                                          tables_used=["t"], era_top_cosine=era))


class StubSession:
    def __init__(self, message):
        self.message = message

    def invoke(self, messages, max_tokens=2048, temperature=0.0, **kw):
        if isinstance(self.message, Exception):
            raise self.message
        return self.message


# -- dialect resolution (pure) ----------------------------------------------

def test_resolve_dialect_agreement():
    drafts = [_sd("a", "SELECT 1", "SparkSQL"), _sd("b", "SELECT 2", "SparkSQL")]
    dialect, warn = RC.resolve_dialect(drafts)
    assert dialect == "SparkSQL" and warn is None


def test_resolve_dialect_none():
    drafts = [_sd("a", "SELECT 1", None), _sd("b", "SELECT 2", None)]
    assert RC.resolve_dialect(drafts) == (None, None)


def test_resolve_dialect_conflict_picks_highest_cosine():
    drafts = [_sd("a", "SELECT 1", "SparkSQL", era=0.4),
              _sd("b", "SELECT 2", "SQLServer", era=0.9)]
    dialect, warn = RC.resolve_dialect(drafts)
    assert dialect == "SQLServer"
    assert warn and "dialect conflict" in warn and "[HIGH]" in warn


# -- reconcile() ------------------------------------------------------------

def test_reconcile_combines_two_drafts():
    msg = {"role": "assistant", "content": json.dumps({
        "reconciliation": "- join on CIF\n- unify position_date",
        "combined_sql": "SELECT c.cif FROM cif c JOIN giro g ON c.cif = g.cif"})}
    drafts = [_sd("CIF", "SELECT cif FROM cif", "SparkSQL"),
              _sd("Giro", "SELECT cif FROM giro", "SparkSQL")]
    out = RC.reconcile("CIF dan Giro", drafts, StubSession(msg))
    assert "join on CIF" in out["reconciliation"]
    assert out["combined_sql"].startswith(gates.UNVERIFIED_MARKER)
    assert "JOIN giro" in out["combined_sql"]
    assert out["warnings"] == []  # same dialect → no conflict warning


def test_reconcile_single_draft_skips_llm():
    # fewer than 2 drafts with SQL → no merge attempt, but dialect warning still surfaces
    drafts = [_sd("CIF", "SELECT cif FROM cif", "SparkSQL"),
              _sd("Giro", "", "SQLServer")]  # second has no SQL
    out = RC.reconcile("q", drafts, StubSession(RuntimeError("must not be called")))
    assert out["reconciliation"] is None and out["combined_sql"] is None
    # dialect conflict is computed from declared dialects regardless of SQL presence
    assert any("dialect conflict" in w for w in out["warnings"])


def test_reconcile_llm_failure_is_non_fatal():
    drafts = [_sd("a", "SELECT 1", "SparkSQL"), _sd("b", "SELECT 2", "SparkSQL")]
    out = RC.reconcile("q", drafts, StubSession(RuntimeError("bedrock down")))
    assert out["reconciliation"] is None and out["combined_sql"] is None
