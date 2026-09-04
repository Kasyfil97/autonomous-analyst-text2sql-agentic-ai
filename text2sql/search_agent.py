"""Search sub-agent: a RAG agent that answers KB-exploration questions in prose.

Mirrors the text2sql agent's shape (Strands Agent + the same three knowledge tools)
but for a different intent: the user asks whether something EXISTS in the catalog, or
asks about its structure (ERA precedents, tables, columns) — not for SQL or data rows.

Flow: the agent researches via the knowledge tools, then writes a prose answer grounded
in what it retrieved. We do NOT parse JSON from the answer — the format is free prose.
Sources are derived deterministically from the per-request ``RetrievalContext`` (what was
actually retrieved), never from the model's claims. The gate is warn-don't-block
(``gates.decide_search``): the answer always reaches the user, with severity-tagged
warnings attached when a check fails.
"""
from __future__ import annotations

import time

import psycopg2
from pydantic import BaseModel, Field
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

import bedrock_session as _bs
from text2sql import gates
from text2sql.audit_log import get_logger, new_request
from text2sql.bedrock_model import Text2SqlBedrockModel
from text2sql.embedding_service import pg_config
from text2sql.prompt_loader import load_prompt
from text2sql.tools import RetrievalContext, build_tools

SEARCH_SYSTEM_PROMPT = load_prompt("search_system_prompt")

_log = get_logger("search_agent")


class SearchResult(BaseModel):
    answer: str | None = None
    sources: dict = Field(default_factory=dict)  # {"era_precedents": [...], "tables": [...]}
    found: bool = False
    warnings: list[str] = Field(default_factory=list)
    declined: bool = False
    missing: str | None = None

    @classmethod
    def decline(cls, reason: str) -> "SearchResult":
        return cls(declined=True, missing=reason)


def build_search_agent(session, ctx: RetrievalContext) -> Agent:
    model = Text2SqlBedrockModel(session, max_tokens=2048, temperature=0.0)
    return Agent(
        model=model,
        tools=build_tools(ctx),
        system_prompt=SEARCH_SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(),
    )


def _derive_sources(ctx: RetrievalContext) -> dict:
    """Sources from what was retrieved this request — not from the model's claims."""
    era_ids: list[str] = []
    for c in ctx.calls:
        if c["kb"] == "era_corpus":
            era_ids.extend(c["ids"])
    sources: dict = {}
    if era_ids:
        sources["era_precedents"] = sorted(dict.fromkeys(era_ids))
    if ctx.retrieved_tables:
        sources["tables"] = sorted(ctx.retrieved_tables)
    return sources


def answer_question(question: str, *, session=None, conn=None) -> SearchResult:
    """Answer a KB-exploration question in prose, gated warn-don't-block."""
    rid = new_request()
    t0 = time.perf_counter()
    _log.info("answer_question START | request_id=%s  question=%r", rid, question[:120])

    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**pg_config(readonly=True))
    try:
        if session is None:
            from text2sql.agent import get_session
            session = get_session()
        ctx = RetrievalContext(conn)
        agent = build_search_agent(session, ctx)

        _log.info("search_loop START | tools=search_era_knowledge,search_schema,get_table_schema")
        raw = str(agent(question))
        answer = _bs._strip_reasoning(raw)
        _log.info("search_loop DONE  | answer_len=%d chars  tool_calls_made=%d",
                  len(answer), len(ctx.calls))

        sources = _derive_sources(ctx)
        found = bool(sources)
        warnings = gates.decide_search(answer, ctx.retrieved_tables)

        result = SearchResult(answer=answer, sources=sources, found=found, warnings=warnings)
        elapsed = time.perf_counter() - t0
        _log.info(
            "answer_question RETURNED | elapsed=%.3fs  found=%s  sources=%s  warnings=%s",
            elapsed, found, sources, warnings,
        )
        return result
    finally:
        if own_conn:
            conn.close()
