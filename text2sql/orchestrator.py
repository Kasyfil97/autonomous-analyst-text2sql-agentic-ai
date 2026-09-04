"""Top-level orchestrator: route a question to the right sub-agent.

Two sub-agents exist — ``text2sql`` (draft SQL, unchanged) and ``search`` (prose answers
about the knowledge base). Rather than a second agent loop (risky on gpt-oss, whose
tool-calling is the very defect ``bedrock_model.py`` works around), routing is a single
forced-tool-choice classification call against ``BedrockSession.invoke``. We force the
model to call ``route_intent`` and read its ``intent`` argument; if the model fails to
call the tool, we fall back to a keyword heuristic that defaults to ``sql`` so existing
behavior is preserved.
"""
from __future__ import annotations

import json

from text2sql import agent as _agent
from text2sql import search_agent as _search
from text2sql.agent import MultiDraftResult, SubDraft, Text2SQLResult
from text2sql.audit_log import get_logger
from text2sql.decompose import decompose_request
from text2sql.embedding_service import pg_config
from text2sql.prompt_loader import load_prompt
from text2sql.reconcile import reconcile
from text2sql.search_agent import SearchResult

_log = get_logger("orchestrator")

ROUTER_PROMPT = load_prompt("router_prompt")

_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "route_intent",
        "description": "Route the user's question to the sql or search sub-agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["sql", "search", "other"],
                           "description": "Which sub-agent should handle the question, "
                                          "or 'other' if it is outside this assistant's scope."},
                "reason": {"type": "string",
                           "description": "One short phrase justifying the choice."},
            },
            "required": ["intent"],
        },
    },
}

# Returned verbatim when the question is out of scope (intent='other').
FALLBACK_MESSAGE = (
    "Maaf, pertanyaan ini di luar cakupan saya. Saya bisa membantu membuat draft SQL "
    "untuk data bank, atau mencari informasi di knowledge base (tabel, kolom, atau ERA "
    "ticket).\n"
    "Sorry, this is outside my scope. I can draft SQL for bank data, or search the "
    "knowledge base (tables, columns, ERA tickets)."
)

# Phrases that signal a KB-exploration ("search") intent — used only as a fallback when
# the model fails to emit a forced tool call.
_SEARCH_HINTS = (
    "apakah ada", "adakah", "apa saja kolom", "kolom dari", "kolom apa", "table apa",
    "tabel apa", "what columns", "which table", "is there a table", "does the table",
    "list the columns", "what tables",
)


def _heuristic_route(question: str) -> str:
    low = question.lower()
    if any(h in low for h in _SEARCH_HINTS):
        return "search"
    return "sql"


def route(question: str, session) -> str:
    """Classify the question as 'sql' or 'search' via a forced tool call.

    Falls back to a keyword heuristic (default 'sql') if the model returns no tool call.
    """
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": question},
    ]
    try:
        message = session.invoke(
            messages,
            tools=[_ROUTE_TOOL],
            max_tokens=200,
            temperature=0.0,
            tool_choice={"type": "function", "function": {"name": "route_intent"}},
        )
    except Exception as exc:  # noqa: BLE001 — never let routing crash the request
        intent = _heuristic_route(question)
        _log.warning("route | invoke failed (%s) — heuristic fallback intent=%r",
                     type(exc).__name__, intent)
        return intent

    tool_calls = (message or {}).get("tool_calls") or []
    if not tool_calls:
        intent = _heuristic_route(question)
        _log.warning(
            "route | model returned no tool_call — heuristic fallback intent=%r"
            "  reason: gpt-oss end_turn defect; defaulting toward sql",
            intent,
        )
        return intent

    args = tool_calls[0].get("function", {}).get("arguments") or "{}"
    try:
        parsed = json.loads(args) if isinstance(args, str) else (args or {})
    except json.JSONDecodeError:
        parsed = {}
    intent = parsed.get("intent")
    if intent not in ("sql", "search", "other"):
        intent = _heuristic_route(question)
        _log.warning("route | unrecognized intent=%r — heuristic fallback=%r",
                     parsed.get("intent"), intent)
    else:
        _log.info("route | intent=%r  reason=%r", intent, parsed.get("reason"))
    return intent


def generate_sql_orchestrated(question: str, *, session, conn,
                              attached_tables=None, history=None):
    """Decompose the request, draft each sub-need, and reconcile (plan rec #1-3).

    Returns a single ``Text2SQLResult`` for a one-part request (the fast path, identical
    to the pre-decomposition flow) or a ``MultiDraftResult`` for a multi-part request.

    Decomposition runs only on the opening turn: a follow-up ("change the year to 2024")
    carries ``history`` and must stay a single coherent edit, so it skips decomposition.
    """
    if history:
        _log.info("orchestrate | follow-up turn — skip decomposition, single draft")
        return _agent.generate_sql(question, session=session, conn=conn,
                                   attached_tables=attached_tables, history=history)

    _log.info("orchestrate | decomposing question: %r", question)
    sub_needs = decompose_request(question, session)

    if len(sub_needs) <= 1:
        _log.info("orchestrate | plan: single sub-need (fast path) — %r",
                  sub_needs[0] if sub_needs else question)
        return _agent.generate_sql(question, session=session, conn=conn,
                                   attached_tables=attached_tables)

    _log.info("orchestrate | plan: %d sub-needs", len(sub_needs))
    for i, sn in enumerate(sub_needs, 1):
        _log.info("orchestrate |   [%d/%d] %s", i, len(sub_needs), sn)
    sub_drafts = [
        SubDraft(sub_need=sn,
                 result=_agent.generate_sql(sn, session=session, conn=conn,
                                            attached_tables=attached_tables))
        for sn in sub_needs
    ]
    rec = reconcile(question, sub_drafts, session)
    return MultiDraftResult(
        question=question,
        sub_drafts=sub_drafts,
        reconciliation=rec["reconciliation"],
        combined_sql=rec["combined_sql"],
        warnings=rec["warnings"],
    )


def _fallback() -> SearchResult:
    """Out-of-scope response — flows through the CLI like any other result."""
    return SearchResult(answer=FALLBACK_MESSAGE, found=False)


def handle(question: str, *, session=None, conn=None
           ) -> Text2SQLResult | SearchResult | MultiDraftResult:
    """Route the question and dispatch to the chosen sub-agent.

    An out-of-scope question ('other') short-circuits to a fallback response without
    opening a DB connection or invoking a sub-agent.
    """
    session = session or _agent.get_session()
    intent = route(question, session)
    if intent == "other":
        _log.info("handle | out-of-scope question — returning fallback response")
        return _fallback()

    import psycopg2

    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**pg_config(readonly=True))
    try:
        if intent == "search":
            return _search.answer_question(question, session=session, conn=conn)
        return generate_sql_orchestrated(question, session=session, conn=conn)
    finally:
        if own_conn:
            conn.close()
