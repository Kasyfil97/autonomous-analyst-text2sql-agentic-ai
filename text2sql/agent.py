"""Agent assembly: Strands Agent + tools + gates -> typed draft SQL (plan U6).

Flow: the agent researches via the knowledge tools, then emits a JSON object describing
its draft. We parse that (JSON-from-final-text — chosen primary because forced
structured-output forces a *single* tool call and would block the retrieval tools), then
run the gates. The gates are authoritative but warn-don't-block: a failing gate attaches
a severity-tagged warning to the result instead of declining, so the draft still reaches
the human reviewer. The only hard declines left are *no parseable answer* and *no SQL at
all* — there is nothing to return in those cases. The dialect is taken deterministically
from the cited top ERA precedent's ``query_engine``.
"""
from __future__ import annotations

import json
import re
import time

import psycopg2
from pydantic import BaseModel, Field
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

import bedrock_session as _bs
from text2sql.embedding_service import pg_config
from text2sql import gates
from text2sql.audit_log import get_logger, new_request
from text2sql.bedrock_model import Text2SqlBedrockModel
from text2sql.prompt_loader import load_prompt
from text2sql.tools import RetrievalContext, build_tools

SYSTEM_PROMPT = load_prompt("system_prompt")

_log = get_logger("agent")

_session = None
_known_tables_cache: set | None = None


class Text2SQLResult(BaseModel):
    sql: str | None = None
    explanation: str | None = None
    tables_used: list[str] = Field(default_factory=list)
    columns_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    precedent_ids: list[str] = Field(default_factory=list)
    dialect: str | None = None
    declined: bool = False
    missing: str | None = None
    warnings: list[str] = Field(default_factory=list)  # severity-tagged gate findings

    @classmethod
    def decline(cls, reason: str) -> "Text2SQLResult":
        return cls(declined=True, missing=reason)


def get_session():
    """Lazily build a single federated BedrockSession (OIDC is expensive)."""
    global _session
    if _session is None:
        _session = _bs.BedrockSession()
    return _session


def known_tables(conn) -> set:
    global _known_tables_cache
    if _known_tables_cache is None:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM schema_tables "
                        "UNION SELECT table_name FROM schema_columns")
            _known_tables_cache = {r[0] for r in cur.fetchall() if r[0]}
    return _known_tables_cache


def _extract_json(text: str) -> dict | None:
    """Tolerantly extract the first JSON object from the model's final text."""
    text = _bs._strip_reasoning(text or "")
    # strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def parse_result(raw_text: str) -> Text2SQLResult:
    _log.debug("parse_result | raw_len=%d chars", len(raw_text or ""))
    data = _extract_json(raw_text)
    if not data:
        _log.warning(
            "parse_result | FAIL — no parseable JSON in model output"
            "  reason: model may have responded in prose instead of the required JSON schema",
        )
        return Text2SQLResult.decline("model did not return a parseable answer")

    result = Text2SQLResult(
        sql=data.get("sql") or None,
        explanation=data.get("explanation"),
        tables_used=data.get("tables_used") or [],
        columns_used=data.get("columns_used") or [],
        assumptions=data.get("assumptions") or [],
        precedent_ids=data.get("precedent_ids") or [],
        dialect=data.get("dialect"),
        declined=bool(data.get("declined")),
        missing=data.get("missing") or None,
    )
    if result.declined:
        _log.warning(
            "parse_result | model self-declined — missing=%r"
            "  reason: model determined it lacked sufficient knowledge to answer",
            result.missing,
        )
    else:
        _log.info(
            "parse_result | OK — sql_len=%d  dialect=%r  tables=%s  precedents=%s",
            len(result.sql or ""), result.dialect,
            result.tables_used, result.precedent_ids,
        )
    return result


def precedent_dialect(ctx: RetrievalContext, conn) -> str | None:
    """Dialect from the highest-scoring cited ERA precedent's query_engine.

    Picks the ERA call with the max top_cosine across the loop (not call order), so a
    refining second search can't drag the dialect off the best precedent.
    """
    era_calls = [c for c in ctx.calls if c["kb"] == "era_knowledge" and c["ids"]]
    if not era_calls:
        _log.warning(
            "precedent_dialect | no ERA calls with results — dialect will be None"
            "  reason: search_era_knowledge returned 0 rows; dialect falls back to model claim",
        )
        return None
    best = max(era_calls, key=lambda c: c.get("top_cosine", 0.0))
    top_id = best["ids"][0]
    with conn.cursor() as cur:
        cur.execute("SELECT query_engine FROM era_knowledge WHERE id = %s", (top_id,))
        row = cur.fetchone()
    dialect = row[0] if row else None
    _log.info(
        "precedent_dialect | dialect=%r  source=ERA:%s  cosine=%.3f"
        "  reason: highest-cosine precedent determines SQL dialect (not model self-report)",
        dialect, top_id, best.get("top_cosine", 0.0),
    )
    return dialect


def apply_gates(result: Text2SQLResult, ctx: RetrievalContext, conn, *,
                ground_warn: bool = True) -> Text2SQLResult:
    """Run the gates authoritatively over the model's draft (plan KD3)."""
    _log.info(
        "apply_gates | START — era_top_cosine=%.3f  schema_top_cosine=%.3f"
        "  retrieved_tables=%s  tool_calls=%d",
        ctx.era_top_cosine(), ctx.schema_top_cosine(),
        sorted(ctx.retrieved_tables), len(ctx.calls),
    )

    if result.declined:
        _log.info("apply_gates | skipped — result already declined by model")
        return result
    if not result.sql:
        _log.warning("apply_gates | DECLINE — model produced no SQL field")
        return Text2SQLResult.decline("model produced no SQL")

    # Dialect: a confident precedent's query_engine is authoritative; when no precedent
    # anchors it (schema-first path), fall back to the model's self-reported dialect and
    # validate under that. validate_sql's DDL/DML rejection is dialect-independent, so a
    # wrong self-report can only mislabel — never let an unsafe statement pass.
    dialect = precedent_dialect(ctx, conn) or result.dialect
    decision = gates.decide(
        result.sql, dialect,
        era_top_cosine=ctx.era_top_cosine(),
        schema_top_cosine=ctx.schema_top_cosine(),
        known_tables=known_tables(conn),
        ground_warn=ground_warn,
    )
    warnings = list(decision.warnings)

    # Accumulator authority: every referenced table must have actually been retrieved
    # (catches a model that claims a table it never looked up). Case-insensitive —
    # sqlglot keeps the model's case, retrieved_tables keep the catalog's. No longer a
    # decline — surfaced as a warning so the draft still reaches the reviewer.
    retrieved_lower = {t.lower() for t in ctx.retrieved_tables}
    unretrieved = {t for t in decision.referenced_tables
                   if t.lower() not in retrieved_lower}
    if unretrieved:
        _log.warning(
            "apply_gates | WARN — accumulator gate: tables referenced but never fetched=%s"
            "  reason: model used table names it never looked up via search_schema/get_table_schema",
            sorted(unretrieved),
        )
        warnings.append(
            f"[HIGH] grounding: referenced table(s) never retrieved: {sorted(unretrieved)}")
    else:
        _log.info(
            "apply_gates | accumulator PASS — all %d table(s) were retrieved via tools",
            len(decision.referenced_tables),
        )

    # Output scan: destructive SQL anywhere in the answer text -> warning (not a decline).
    clean, detail = gates.scan_output(f"{result.explanation or ''}\n{result.sql}")
    if not clean:
        _log.warning("apply_gates | WARN — output scan: %s", detail)
        warnings.append(f"[CRITICAL] unsafe output: {detail}")

    result.dialect = dialect
    result.tables_used = sorted(decision.referenced_tables) or result.tables_used
    result.warnings = warnings
    result.sql = gates.UNVERIFIED_MARKER + "\n" + result.sql
    if warnings:
        _log.warning(
            "apply_gates | RETURNED WITH WARNINGS (%d) — dialect=%r  tables=%s  warnings=%s",
            len(warnings), result.dialect, result.tables_used, warnings,
        )
    else:
        _log.info(
            "apply_gates | APPROVED — dialect=%r  tables=%s  UNVERIFIED_MARKER prepended",
            result.dialect, result.tables_used,
        )
    return result


def build_agent(session, ctx: RetrievalContext) -> Agent:
    model = Text2SqlBedrockModel(session, max_tokens=3072, temperature=0.0)
    return Agent(
        model=model,
        tools=build_tools(ctx),
        system_prompt=SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(),
    )


def generate_sql(question: str, *, session=None, conn=None) -> Text2SQLResult:
    """Turn a NL question into a gated draft SQL result (no execution)."""
    rid = new_request()
    t0 = time.perf_counter()
    _log.info("generate_sql START | request_id=%s  question=%r", rid, question[:120])

    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**pg_config(readonly=True))
    try:
        session = session or get_session()
        ctx = RetrievalContext(conn)
        agent = build_agent(session, ctx)

        _log.info("agent_loop START | model=gpt-oss-120b  tools=search_era_knowledge,search_schema,get_table_schema")
        raw = str(agent(question))
        _log.info("agent_loop DONE  | raw_output_len=%d chars  tool_calls_made=%d", len(raw), len(ctx.calls))

        result = parse_result(raw)
        result = apply_gates(result, ctx, conn)

        elapsed = time.perf_counter() - t0
        if result.declined:
            _log.warning(
                "generate_sql DECLINED | elapsed=%.3fs  reason=%r",
                elapsed, result.missing,
            )
        else:
            _log.info(
                "generate_sql RETURNED | elapsed=%.3fs  dialect=%r  tables=%s  precedents=%s"
                "  assumptions=%s  warnings=%s",
                elapsed, result.dialect, result.tables_used, result.precedent_ids,
                result.assumptions, result.warnings,
            )
        return result
    finally:
        if own_conn:
            conn.close()
