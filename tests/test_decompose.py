"""Unit tests for request decomposition (plan rec #1) — no DB, mocked session."""
import json

from text2sql import decompose as D


class StubSession:
    """Returns a canned assistant message (or raises) and records invoke args."""
    def __init__(self, message):
        self.message = message
        self.calls = []

    def invoke(self, messages, tools=None, max_tokens=2048, temperature=0.0,
               *, tool_choice=None, allow_mantle=False):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if isinstance(self.message, Exception):
            raise self.message
        return self.message


def _tool_msg(sub_needs):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"function": {"name": "decompose_request",
                      "arguments": json.dumps({"sub_needs": sub_needs})}}]}


def test_multi_part_splits():
    sess = StubSession(_tool_msg([{"text": "Retail CIF Selindo terkini"},
                                  {"text": "Retail Giro Selindo terkini"}]))
    out = D.decompose_request("CIF dan Giro Selindo terkini", sess)
    assert out == ["Retail CIF Selindo terkini", "Retail Giro Selindo terkini"]
    # forced tool choice was requested
    assert sess.calls[0]["tool_choice"]["function"]["name"] == "decompose_request"


def test_single_need_fast_path():
    sess = StubSession(_tool_msg([{"text": "jumlah transaksi teller per cabang 2025"}]))
    out = D.decompose_request("jumlah transaksi teller per cabang 2025", sess)
    assert out == ["jumlah transaksi teller per cabang 2025"]


def test_no_tool_call_falls_back_to_whole_question():
    sess = StubSession({"role": "assistant", "content": "prose, no tool call"})
    out = D.decompose_request("apa saja data nasabah", sess)
    assert out == ["apa saja data nasabah"]


def test_invoke_failure_falls_back():
    sess = StubSession(RuntimeError("bedrock down"))
    out = D.decompose_request("data giro", sess)
    assert out == ["data giro"]


def test_caps_at_max_sub_needs():
    many = [{"text": f"need {i}"} for i in range(8)]
    sess = StubSession(_tool_msg(many))
    out = D.decompose_request("many parts", sess)
    assert len(out) == D.MAX_SUB_NEEDS


def test_coerce_accepts_strings_and_dedupes():
    # tolerant of string items and duplicate texts
    got = D._coerce_sub_needs({"sub_needs": ["A", {"text": "A"}, {"text": "B"}]}, "q")
    assert got == ["A", "B"]


def test_coerce_empty_returns_question():
    assert D._coerce_sub_needs({"sub_needs": []}, "the question") == ["the question"]
