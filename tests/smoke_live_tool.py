"""U2.5 — LIVE single-tool smoke gate (plan hard gate before U4-U7).

Requires real federated Bedrock credentials (.env) + network. Run directly:

    ./.venv/Scripts/python.exe tests/smoke_live_tool.py

It (1) raw-probes whether real gpt-oss-120b returns OpenAI ``tool_calls`` via
``invoke_model`` and captures the wire shape + reasoning delimiter, (2) drives a real
Strands Agent and asserts a @tool actually fires, and (3) tries a forced
structured-output call and classifies it PASS/FAIL.

PASS here unblocks U4-U7. FAIL means switch to prompted-ReAct or a tool-capable
Bedrock model behind the same provider seam.
"""
import json
import sys

from bedrock_session import BedrockSession
from text2sql.bedrock_model import Text2SqlBedrockModel


def hr(title):
    print("\n" + "=" * 60 + f"\n{title}\n" + "=" * 60)


def raw_probe(session):
    hr("1) RAW PROBE: does gpt-oss return OpenAI tool_calls?")
    tools = [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo the given text back to the user.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }]
    msg = session.invoke(
        messages=[{"role": "user",
                   "content": "Call the echo tool with text='ping'. Use the tool."}],
        tools=tools, max_tokens=1024, temperature=0.0,
    )
    print("raw message keys:", list(msg.keys()))
    print("raw message (truncated):", json.dumps(msg)[:800])
    has_tool_calls = bool(msg.get("tool_calls"))
    print(f"\n>> tool_calls present: {has_tool_calls}")
    if has_tool_calls:
        tc = msg["tool_calls"][0]
        print("   tool_call shape:", json.dumps(tc)[:300])
    # reasoning delimiter probe
    content = msg.get("content")
    if content:
        print("   content delimiter sample:", repr(content[:160]))
    return has_tool_calls


def agent_tool_fires(session):
    hr("2) STRANDS AGENT: does a @tool actually fire?")
    from strands import Agent, tool
    fired = {"n": 0}

    @tool
    def echo(text: str) -> str:
        """Echo the text back."""
        fired["n"] += 1
        return f"echoed:{text}"

    model = Text2SqlBedrockModel(session, max_tokens=1024, temperature=0.0)
    agent = Agent(model=model, tools=[echo],
                  system_prompt="You are a tool-using assistant. Use tools when asked.")
    result = agent("Use the echo tool to echo the word 'pong'.")
    print("agent result:", str(result)[:300])
    print(f">> tool fired {fired['n']} time(s)")
    return fired["n"] > 0


def forced_structured_output(session):
    hr("3) FORCED STRUCTURED OUTPUT (gpt-oss forced tool call)")
    from strands import Agent
    from pydantic import BaseModel

    class Answer(BaseModel):
        sql: str
        dialect: str

    model = Text2SqlBedrockModel(session, max_tokens=1024, temperature=0.0)
    agent = Agent(model=model, system_prompt="Return a trivial SELECT 1 query.")
    try:
        out = agent.structured_output(
            Answer, "Give SQL 'SELECT 1' with dialect 'SparkSQL'.")
        print(">> structured_output PASS:", out)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f">> structured_output FAIL: {type(exc).__name__}: {exc}")
        print("   (U6 will use the JSON-from-text path as primary.)")
        return False


def main():
    hr("U2.5 LIVE SMOKE GATE — gpt-oss-120b on Bedrock")
    session = BedrockSession()

    raw_ok = raw_probe(session)
    agent_ok = agent_tool_fires(session)
    forced_ok = forced_structured_output(session)

    hr("RESULT")
    print(f"raw tool_calls:        {'PASS' if raw_ok else 'FAIL'}")
    print(f"agent @tool fires:     {'PASS' if agent_ok else 'FAIL'}")
    print(f"forced structured out: {'PASS' if forced_ok else 'FAIL (fallback to JSON-from-text)'}")
    gate = agent_ok  # the load-bearing requirement for U4-U7
    print(f"\nGATE (agent tool firing): {'PASS — proceed to U4-U7' if gate else 'FAIL — switch provider/model'}")
    sys.exit(0 if gate else 1)


if __name__ == "__main__":
    main()
