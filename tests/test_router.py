"""Unit tests for the orchestrator router. No DB / no network required."""
import json

from text2sql import orchestrator as O


class _FakeSession:
    """Stands in for BedrockSession; returns a canned assistant message or raises."""

    def __init__(self, *, message=None, raises=False):
        self._message = message
        self._raises = raises
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self._raises:
            raise RuntimeError("boom")
        return self._message


def _tool_message(intent):
    return {"tool_calls": [{
        "id": "tc_1",
        "function": {"name": "route_intent",
                     "arguments": json.dumps({"intent": intent, "reason": "x"})},
    }]}


def test_route_returns_search_from_forced_tool_call():
    s = _FakeSession(message=_tool_message("search"))
    assert O.route("apa saja kolom table X?", s) == "search"
    # the router must force the route_intent tool
    _, kwargs = s.calls[0]
    assert kwargs["tool_choice"]["function"]["name"] == "route_intent"


def test_route_returns_sql_from_forced_tool_call():
    s = _FakeSession(message=_tool_message("sql"))
    assert O.route("buatkan query total transaksi", s) == "sql"


def test_route_falls_back_to_heuristic_search_when_no_tool_call():
    s = _FakeSession(message={"content": "I think this is a lookup"})
    assert O.route("apakah ada table tentang nasabah?", s) == "search"


def test_route_falls_back_to_default_sql_when_no_tool_call():
    s = _FakeSession(message={"content": "no tool call here"})
    assert O.route("total transaksi teller per cabang", s) == "sql"


def test_route_falls_back_on_invoke_exception():
    s = _FakeSession(raises=True)
    # heuristic still applies on the fallback path
    assert O.route("apa saja kolom dari table teller?", s) == "search"
    assert O.route("hitung jumlah transaksi", s) == "sql"


def test_route_handles_unrecognized_intent():
    s = _FakeSession(message=_tool_message("banana"))
    # invalid enum -> heuristic; this phrasing defaults to sql
    assert O.route("tampilkan data", s) == "sql"


def test_route_returns_other_for_out_of_scope():
    s = _FakeSession(message=_tool_message("other"))
    assert O.route("apa cuaca hari ini?", s) == "other"


def test_handle_returns_fallback_for_other(monkeypatch):
    from text2sql.search_agent import SearchResult

    monkeypatch.setattr(O, "route", lambda q, s: "other")
    r = O.handle("ceritakan lelucon dong", session=object())
    assert isinstance(r, SearchResult)
    assert not r.found and not r.declined
    assert r.answer == O.FALLBACK_MESSAGE
