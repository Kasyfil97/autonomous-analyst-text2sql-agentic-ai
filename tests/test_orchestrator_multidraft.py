"""Orchestration wiring for decomposed multi-draft requests (plan rec #1-3) — no DB."""
from text2sql import orchestrator as O
from text2sql import api_serializers as S
from text2sql.agent import MultiDraftResult, Text2SQLResult


def _fake_result(sql="SELECT 1"):
    return Text2SQLResult(sql=sql, dialect="SparkSQL", tables_used=["t"],
                          explanation="- does x", era_top_cosine=0.6, schema_top_cosine=0.6)


def _patch(monkeypatch, *, sub_needs, recon=None):
    monkeypatch.setattr(O, "decompose_request", lambda q, s: sub_needs)
    monkeypatch.setattr(O._agent, "generate_sql",
                        lambda q, **kw: _fake_result(f"SELECT '{q}'"))
    monkeypatch.setattr(O, "reconcile",
                        lambda q, drafts, s: recon or
                        {"reconciliation": "- join on CIF", "combined_sql": "SELECT 1",
                         "warnings": []})


def test_single_need_returns_plain_result(monkeypatch):
    _patch(monkeypatch, sub_needs=["one need"])
    out = O.generate_sql_orchestrated("one need", session=None, conn=None)
    assert isinstance(out, Text2SQLResult)


def test_multi_need_returns_multidraft(monkeypatch):
    _patch(monkeypatch, sub_needs=["CIF Selindo", "Giro Selindo"])
    out = O.generate_sql_orchestrated("CIF dan Giro Selindo", session=None, conn=None)
    assert isinstance(out, MultiDraftResult)
    assert [sd.sub_need for sd in out.sub_drafts] == ["CIF Selindo", "Giro Selindo"]
    assert out.reconciliation == "- join on CIF"
    assert out.declined is False


def test_followup_with_history_skips_decomposition(monkeypatch):
    called = {"decompose": False}
    monkeypatch.setattr(O, "decompose_request",
                        lambda q, s: called.__setitem__("decompose", True) or ["x"])
    monkeypatch.setattr(O._agent, "generate_sql", lambda q, **kw: _fake_result())
    out = O.generate_sql_orchestrated("change year to 2024", session=None, conn=None,
                                      history=[{"role": "user", "content": "prev"}])
    assert isinstance(out, Text2SQLResult)
    assert called["decompose"] is False  # follow-ups never decompose


def test_multidraft_serializes_with_nested_subdrafts(monkeypatch):
    _patch(monkeypatch, sub_needs=["CIF", "Giro"])
    out = O.generate_sql_orchestrated("CIF dan Giro", session=None, conn=None)
    payload = S.result_to_payload(out)
    assert payload["kind"] == "sql_multi"
    assert len(payload["sub_drafts"]) == 2
    # each sub-draft reuses the single-draft payload shape
    assert payload["sub_drafts"][0]["result"]["kind"] == "sql"
    assert "sub_need" in payload["sub_drafts"][0]
    assert payload["reconciliation"] == "- join on CIF"


def test_single_result_serializes_as_sql(monkeypatch):
    _patch(monkeypatch, sub_needs=["one"])
    out = O.generate_sql_orchestrated("one", session=None, conn=None)
    assert S.result_to_payload(out)["kind"] == "sql"
