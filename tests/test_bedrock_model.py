"""U2 tests: custom Strands Model provider translation + end-to-end tool firing.

These use a stubbed BedrockSession (no network). The end-to-end test proves the
event translation makes a real Strands Agent execute a @tool — i.e. the gpt-oss
``end_turn`` defect is fixed at the provider boundary. (Live proof is U2.5.)
"""
import json

import pytest
from strands import Agent, tool

from text2sql.bedrock_model import (
    Text2SqlBedrockModel,
    message_to_events,
    to_openai_messages,
    to_openai_tools,
    to_openai_tool_choice,
)


class StubSession:
    """Returns queued OpenAI messages in order, recording the bodies it was called with."""

    model_id = "openai.gpt-oss-120b-1:0"

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def invoke(self, messages, tools=None, max_tokens=2048, temperature=0.0,
               *, tool_choice=None, allow_mantle=False):
        self.calls.append({"messages": messages, "tools": tools,
                           "tool_choice": tool_choice, "max_tokens": max_tokens})
        return self._replies.pop(0)


# -- forward translation -----------------------------------------------------

def test_tool_calls_yield_tool_use_stopreason():
    msg = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}]}
    events = message_to_events(msg)
    stop = [e for e in events if "messageStop" in e][0]["messageStop"]["stopReason"]
    assert stop == "tool_use"
    start = [e for e in events if "contentBlockStart" in e][0]
    assert start["contentBlockStart"]["start"]["toolUse"]["name"] == "echo"


def test_text_yields_end_turn_and_strips_reasoning():
    msg = {"role": "assistant", "content": "<reasoning>secret</reasoning>SELECT 1"}
    events = message_to_events(msg)
    stop = [e for e in events if "messageStop" in e][0]["messageStop"]["stopReason"]
    text = [e for e in events
            if e.get("contentBlockDelta", {}).get("delta", {}).get("text") is not None
            ][0]["contentBlockDelta"]["delta"]["text"]
    assert stop == "end_turn"
    assert "secret" not in text and "SELECT 1" in text


def test_tool_call_without_id_gets_minted_id():
    msg = {"role": "assistant", "tool_calls": [
        {"function": {"name": "x", "arguments": "{}"}}]}
    events = message_to_events(msg)
    tu_id = [e for e in events if "contentBlockStart" in e
             ][0]["contentBlockStart"]["start"]["toolUse"]["toolUseId"]
    assert tu_id  # non-empty


# -- reverse translation -----------------------------------------------------

def test_reverse_translation_pairs_tooluse_and_result():
    messages = [
        {"role": "user", "content": [{"text": "q"}]},
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "echo", "input": {"text": "hi"}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1", "status": "success",
                            "content": [{"text": "hi back"}]}}]},
    ]
    oai = to_openai_messages(messages, system_prompt="sys")
    assert oai[0] == {"role": "system", "content": "sys"}
    asst = [m for m in oai if m["role"] == "assistant"][0]
    assert asst["tool_calls"][0]["id"] == "t1"
    tool_msg = [m for m in oai if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "t1" and "hi back" in tool_msg["content"]


def test_reverse_drops_orphan_toolresult():
    # toolResult with no matching assistant toolUse (eviction dropped the assistant)
    messages = [
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "ghost", "status": "success",
                            "content": [{"text": "x"}]}}]},
    ]
    oai = to_openai_messages(messages)
    assert all(m["role"] != "tool" for m in oai)  # orphan dropped, not sent dangling


def test_reverse_drops_orphan_toolcall():
    # assistant toolUse with no matching result -> drop the tool_call (no dangling)
    messages = [
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "echo", "input": {}}}]},
    ]
    oai = to_openai_messages(messages)
    assert all("tool_calls" not in m for m in oai)


# -- tool spec / choice ------------------------------------------------------

def test_tool_spec_to_openai():
    specs = [{"name": "echo", "description": "d",
              "inputSchema": {"json": {"type": "object", "properties": {}}}}]
    tools = to_openai_tools(specs)
    assert tools[0]["function"]["name"] == "echo"
    assert tools[0]["function"]["parameters"]["type"] == "object"


def test_tool_choice_any_is_required():
    tools = [{"type": "function", "function": {"name": "x"}}]
    assert to_openai_tool_choice({"any": {}}, tools) == "required"
    assert to_openai_tool_choice({"tool": {"name": "x"}}, tools) == {
        "type": "function", "function": {"name": "x"}}
    assert to_openai_tool_choice(None, tools) is None


# -- end-to-end: a real Strands Agent executes a @tool via the provider ------

def test_agent_executes_tool_end_to_end():
    calls = {"n": 0}

    @tool
    def echo(text: str) -> str:
        """Echo the text back."""
        calls["n"] += 1
        return f"echoed:{text}"

    # Turn 1: model asks to call echo. Turn 2: model returns final text.
    stub = StubSession([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}]},
        {"role": "assistant", "content": "done"},
    ])
    model = Text2SqlBedrockModel(stub)
    agent = Agent(model=model, tools=[echo], system_prompt="test")

    result = agent("please echo hi")

    assert calls["n"] == 1, "the @tool must actually execute (gpt-oss defect fixed)"
    assert "done" in str(result)
    # the tool result was threaded back into a second invoke as an OpenAI tool message
    second_call_msgs = stub.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1"
               for m in second_call_msgs)
