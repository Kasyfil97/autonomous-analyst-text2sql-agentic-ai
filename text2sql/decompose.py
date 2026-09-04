"""Deterministic request decomposition (plan rec #1).

A single forced-tool-call classification (same seam as ``orchestrator.route``) that
splits a data request into independent sub-needs, so multi-part requests
("data CIF *dan* giro nasabah retail") can be drafted and reconciled per sub-need
instead of in one overloaded agent loop. Kept OUTSIDE the agent loop on purpose:
gpt-oss tool-calling in a long loop is exactly the defect ``bedrock_model.py`` works
around, so decomposition is one bounded classification call with a safe fallback.
"""
from __future__ import annotations

import json

from text2sql.audit_log import get_logger
from text2sql.prompt_loader import load_prompt

_log = get_logger("decompose")

DECOMPOSE_PROMPT = load_prompt("decompose_prompt")

# Cap: keep the fan-out (and the N× LLM cost downstream) bounded.
MAX_SUB_NEEDS = 4

_DECOMPOSE_TOOL = {
    "type": "function",
    "function": {
        "name": "decompose_request",
        "description": "Break the user's data request into independent sub-needs.",
        "parameters": {
            "type": "object",
            "properties": {
                "sub_needs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string",
                                     "description": "A single self-contained data need, "
                                                    "phrased so it can be drafted on its own."},
                        },
                        "required": ["text"],
                    },
                    "description": "One entry per distinct dataset/metric the request asks "
                                   "for. If the request is a single coherent need, return "
                                   "EXACTLY ONE entry.",
                },
            },
            "required": ["sub_needs"],
        },
    },
}


def _coerce_sub_needs(parsed: dict, question: str) -> list[str]:
    """Pull a clean, de-duplicated, capped list of sub-need texts out of tool args."""
    items = (parsed or {}).get("sub_needs") or []
    texts: list[str] = []
    for it in items:
        if isinstance(it, str):
            t = it.strip()
        elif isinstance(it, dict):
            t = str(it.get("text") or "").strip()
        else:
            t = ""
        if t and t.lower() not in {x.lower() for x in texts}:
            texts.append(t)
    if not texts:
        return [question]
    return texts[:MAX_SUB_NEEDS]


def decompose_request(question: str, session) -> list[str]:
    """Return the sub-needs of ``question`` (>=1). Falls back to ``[question]``.

    A single-element result is the fast path — the caller then behaves exactly as the
    pre-decomposition single-draft flow.
    """
    messages = [
        {"role": "system", "content": DECOMPOSE_PROMPT},
        {"role": "user", "content": question},
    ]
    try:
        message = session.invoke(
            messages,
            tools=[_DECOMPOSE_TOOL],
            max_tokens=400,
            temperature=0.0,
            tool_choice={"type": "function", "function": {"name": "decompose_request"}},
        )
    except Exception as exc:  # noqa: BLE001 — never let decomposition crash the request
        _log.warning("decompose | invoke failed (%s) — single-need fallback",
                     type(exc).__name__)
        return [question]

    tool_calls = (message or {}).get("tool_calls") or []
    if not tool_calls:
        _log.warning(
            "decompose | model returned no tool_call — single-need fallback"
            "  reason: gpt-oss end_turn defect")
        return [question]

    args = tool_calls[0].get("function", {}).get("arguments") or "{}"
    try:
        parsed = json.loads(args) if isinstance(args, str) else (args or {})
    except json.JSONDecodeError:
        _log.warning("decompose | unparseable tool args — single-need fallback")
        return [question]

    sub_needs = _coerce_sub_needs(parsed, question)
    _log.info("decompose | %d sub-need(s): %s", len(sub_needs), sub_needs)
    return sub_needs
