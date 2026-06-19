"""Custom Strands ``Model`` provider for gpt-oss-120b on Bedrock (plan KD1).

Why this exists: gpt-oss-120b does **not** work with Strands' default ``BedrockModel``
tool loop — it doesn't support ConverseStream, and even non-streaming Converse returns
``stopReason: end_turn`` instead of ``tool_use``, so tools are never executed
(strands-agents issues #630/#644/#910). This provider instead drives the model through
the existing federated session's OpenAI-style ``invoke_model`` body
(``BedrockSession.invoke``) and translates, in both directions, between Strands'
Bedrock-shaped events/history and the OpenAI ``tools``/``tool_calls`` format — emitting
``stopReason="tool_use"`` ourselves so the Strands event loop, ``@tool`` execution,
conversation manager, and structured output all work.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterable

from strands.models import Model

import bedrock_session as _bs


class Text2SqlBedrockModel(Model):
    """Drives gpt-oss-120b via ``BedrockSession.invoke`` inside Strands' loop."""

    def __init__(self, bedrock_session, *, max_tokens=2048, temperature=0.0,
                 allow_mantle=False):
        self.bedrock_session = bedrock_session
        self.config: dict[str, Any] = {
            "model_id": getattr(bedrock_session, "model_id", None),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "allow_mantle": allow_mantle,
        }

    # -- Strands Model config hooks ------------------------------------------
    def get_config(self) -> dict[str, Any]:
        return self.config

    def update_config(self, **kwargs: Any) -> None:
        self.config.update(kwargs)

    # -- The load-bearing adapter --------------------------------------------
    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        *,
        tool_choice=None,
        **kwargs: Any,
    ) -> AsyncIterable[dict]:
        openai_messages = to_openai_messages(messages, system_prompt)
        openai_tools = to_openai_tools(tool_specs)
        oai_tool_choice = to_openai_tool_choice(tool_choice, openai_tools)

        # Blocking invoke runs in a worker thread to satisfy the async signature.
        message = await asyncio.to_thread(
            self.bedrock_session.invoke,
            openai_messages,
            tools=openai_tools,
            max_tokens=self.config["max_tokens"],
            temperature=self.config["temperature"],
            tool_choice=oai_tool_choice,
            allow_mantle=self.config["allow_mantle"],
        )
        for event in message_to_events(message):
            yield event

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        """Force a single tool call for ``output_model`` and return the parsed instance.

        Generic implementation (mirrors Strands' BedrockModel): drives ``self.stream``
        with ``tool_choice={"any": {}}``. If gpt-oss can't be forced to call the tool
        (it returns ``end_turn``), this raises and the agent layer (U6) falls back to
        parsing JSON from the final text.
        """
        from strands.event_loop import streaming
        from strands.tools import convert_pydantic_to_tool_spec

        tool_spec = convert_pydantic_to_tool_spec(output_model)
        response = self.stream(
            messages=prompt,
            tool_specs=[tool_spec],
            system_prompt=system_prompt,
            tool_choice={"any": {}},
            **kwargs,
        )
        event = None
        async for event in streaming.process_stream(response):
            yield event

        stop_reason, message, _, _ = event["stop"]
        if stop_reason != "tool_use":
            raise ValueError(f'Model returned stop_reason: {stop_reason} instead of "tool_use".')

        output_response = None
        for block in message["content"]:
            if block.get("toolUse") and block["toolUse"]["name"] == tool_spec["name"]:
                output_response = block["toolUse"]["input"]
        if output_response is None:
            raise ValueError("No structured-output tool use found in the response.")
        yield {"output": output_model(**output_response)}


# --------------------------------------------------------------------------
# Forward translation: OpenAI assistant message -> Strands stream events
# --------------------------------------------------------------------------

def message_to_events(message: dict) -> list[dict]:
    """Translate an OpenAI ``choices[0].message`` into Strands stream events.

    Emits ``stopReason="tool_use"`` whenever the model returned ``tool_calls`` — the
    fix for gpt-oss's ``end_turn`` defect.
    """
    events: list[dict] = [{"messageStart": {"role": "assistant"}}]
    tool_calls = message.get("tool_calls") or []

    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_use_id = tc.get("id") or f"tooluse_{uuid.uuid4().hex[:16]}"
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args or {})
            events.append({"contentBlockStart": {"start": {"toolUse": {
                "toolUseId": tool_use_id, "name": fn.get("name", ""),
            }}}})
            events.append({"contentBlockDelta": {"delta": {"toolUse": {"input": args}}}})
            events.append({"contentBlockStop": {}})
        stop_reason = "tool_use"
    else:
        text = _bs._strip_reasoning(message.get("content") or "")
        events.append({"contentBlockDelta": {"delta": {"text": text}}})
        events.append({"contentBlockStop": {}})
        stop_reason = "end_turn"

    events.append({"messageStop": {"stopReason": stop_reason}})
    events.append({"metadata": {
        "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        "metrics": {"latencyMs": 0},
    }})
    return events


# --------------------------------------------------------------------------
# Reverse translation: Strands history -> OpenAI messages (every turn)
# --------------------------------------------------------------------------

def to_openai_messages(messages, system_prompt=None) -> list[dict]:
    """Translate Strands Bedrock-shaped history into OpenAI chat messages.

    Assistant ``toolUse`` blocks become ``assistant.tool_calls``; ``toolResult`` blocks
    become ``{"role":"tool",...}`` messages. Unpaired (eviction-boundary) tool blocks
    are dropped — never synthesized (plan: repair rule).
    """
    out: list[dict] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg.get("role", "user")
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []

        for block in msg.get("content", []) or []:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append({
                    "id": tu["toolUseId"],
                    "type": "function",
                    "function": {
                        "name": tu["name"],
                        "arguments": json.dumps(tu.get("input", {})),
                    },
                })
            elif "toolResult" in block:
                tr = block["toolResult"]
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tr["toolUseId"],
                    "content": _tool_result_text(tr),
                })

        if role == "assistant":
            m: dict[str, Any] = {"role": "assistant",
                                 "content": "\n".join(text_parts) if text_parts else None}
            if tool_calls:
                m["tool_calls"] = tool_calls
            out.append(m)
        else:
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
            out.extend(tool_results)

    return _repair_tool_pairs(out)


def _tool_result_text(tool_result: dict) -> str:
    parts: list[str] = []
    for c in tool_result.get("content", []) or []:
        if "text" in c:
            parts.append(c["text"])
        elif "json" in c:
            parts.append(json.dumps(c["json"]))
    return "\n".join(parts)


def _repair_tool_pairs(messages: list[dict]) -> list[dict]:
    """Drop unpaired tool blocks so OpenAI never sees a dangling tool_call/result.

    Keep a ``tool`` message only if a preceding assistant ``tool_call`` shares its id,
    and keep an assistant ``tool_call`` only if some ``tool`` message answers it.
    """
    assistant_ids = {
        tc["id"]
        for m in messages if m.get("role") == "assistant"
        for tc in m.get("tool_calls", [])
    }
    result_ids = {
        m["tool_call_id"] for m in messages if m.get("role") == "tool"
    }

    repaired: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            if m["tool_call_id"] in assistant_ids:
                repaired.append(m)
            # else: orphan result -> drop
        elif m.get("role") == "assistant" and m.get("tool_calls"):
            kept = [tc for tc in m["tool_calls"] if tc["id"] in result_ids]
            if kept:
                nm = dict(m)
                nm["tool_calls"] = kept
                repaired.append(nm)
            elif m.get("content"):
                nm = dict(m)
                nm.pop("tool_calls", None)
                repaired.append(nm)
            # else: assistant with only orphaned tool_calls -> drop
        else:
            repaired.append(m)
    return repaired


# --------------------------------------------------------------------------
# Tool spec + tool choice translation
# --------------------------------------------------------------------------

def to_openai_tools(tool_specs) -> list[dict] | None:
    if not tool_specs:
        return None
    tools = []
    for ts in tool_specs:
        schema = ts.get("inputSchema", {}) or {}
        # Strands wraps the JSON schema as {"json": {...}}.
        params = schema.get("json", schema) if isinstance(schema, dict) else {}
        tools.append({"type": "function", "function": {
            "name": ts["name"],
            "description": ts.get("description", ""),
            "parameters": params,
        }})
    return tools


def to_openai_tool_choice(tool_choice, openai_tools):
    """Map Strands ToolChoice -> OpenAI tool_choice. ``{"any": {}}`` (structured output)
    becomes ``"required"`` to force a tool call."""
    if not tool_choice or not openai_tools:
        return None
    if "any" in tool_choice:
        return "required"
    if "tool" in tool_choice:
        name = tool_choice["tool"].get("name")
        if name:
            return {"type": "function", "function": {"name": name}}
    return "auto"
