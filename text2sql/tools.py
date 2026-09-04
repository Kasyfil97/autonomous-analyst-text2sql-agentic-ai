"""Knowledge tools for the agent loop + pre-context PII redaction (plan U4/KD5/KD8/KD10).

Three ``@tool`` functions expose the ERA-precedent and schema knowledge bases to the
Strands agent:
  - ``search_era_knowledge`` — past cases (precedent SQL, tables, key_filters, engine, notes)
  - ``search_schema``        — candidate tables/columns for a concept, rendered as DDL
  - ``get_table_schema``     — deterministic full column dictionary for a known table

All retrieved free-text is (a) PII-redacted *before* it can reach the external model and
(b) fenced as ``<untrusted>`` so the model treats it as data, not instructions. Each
retrieval call records its coverage signal + retrieved identifiers into a per-request
``RetrievalContext`` that the gates (U5/U6) consult — so grounding/coverage check what
was *retrieved*, not what the model claims.
"""
from __future__ import annotations

import re

from strands import tool

from text2sql.audit_log import get_logger
from text2sql.prompt_loader import load_prompt
from text2sql.retrieval import hybrid_search, hybrid_search_era_corpus, top_dense_cosine

_log = get_logger("tools")

# --------------------------------------------------------------------------
# Redaction (KD10) — mask PII before it reaches the model, without corrupting
# SQL identifiers, coded-value literals, dates, or periods.
# --------------------------------------------------------------------------

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# >=10 contiguous digits: catches PANs / long account numbers but leaves coded
# values (1-3 digits), periods (YYYYMM=6), and dates (YYYYMMDD=8) intact.
_LONG_DIGITS = re.compile(r"(?<!\d)\d{10,}(?!\d)")
# NIK / card numbers written with separators or spaces (>=12 grouped digits).
_GROUPED_PII = re.compile(r"(?<![\d-])(?:\d[ .-]?){12,}\d(?![\d-])")
# Indonesian phone numbers (e.g. 0812-3456-7890, +62 812 3456 7890).
_PHONE = re.compile(r"(?<!\d)(?:\+62|0)[\s.-]?8\d(?:[\s.-]?\d){7,10}(?!\d)")
# Card numbers only, for SQL bodies (very conservative to preserve literals).
_PAN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def redact_note(text: str) -> str:
    """Redact free-text (emails, phones, grouped/long PII digit runs)."""
    if not text:
        return text
    original = text
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    text = _GROUPED_PII.sub("[REDACTED]", text)
    text = _LONG_DIGITS.sub("[REDACTED]", text)
    if text != original:
        _log.info(
            "redact_note | PII masked (len %d→%d) — content sanitised before reaching LLM",
            len(original), len(text),
        )
    return text


def redact_sql(text: str) -> str:
    """Redact a precedent SQL body conservatively (emails + card-length runs only),
    preserving identifiers, coded values, dates, and short literals."""
    if not text:
        return text
    original = text
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PAN.sub("[REDACTED_PAN]", text)
    if text != original:
        _log.info(
            "redact_sql  | PAN/email masked in precedent SQL (len %d→%d)",
            len(original), len(text),
        )
    return text


def _fence(label: str, body: str) -> str:
    return f"<untrusted source=\"{label}\">\n{body}\n</untrusted>"


# --------------------------------------------------------------------------
# Per-request retrieval context (coverage signals + retrieved identifiers)
# --------------------------------------------------------------------------

class RetrievalContext:
    """Holds the read-only DB connection and accumulates what each tool retrieved."""

    def __init__(self, conn):
        self.conn = conn
        self.calls: list[dict] = []
        self.retrieved_tables: set[str] = set()
        self.retrieved_columns: set[str] = set()

    def record(self, kb: str, query: str, rows: list[dict]) -> None:
        self.calls.append({
            "kb": kb,
            "query": query,
            "top_cosine": top_dense_cosine(rows),
            "top_rrf": rows[0]["score"] if rows else 0.0,
            "ids": [r["id"] for r in rows],
        })

    def era_top_cosine(self) -> float:
        vals = [c["top_cosine"] for c in self.calls if c["kb"] == "era_corpus"]
        return max(vals) if vals else 0.0

    def schema_top_cosine(self) -> float:
        vals = [c["top_cosine"] for c in self.calls
                if c["kb"] in ("schema_tables", "schema_columns")]
        return max(vals) if vals else 0.0


# --------------------------------------------------------------------------
# Payload fetches (read-only)
# --------------------------------------------------------------------------

def _fetch_era_payloads(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, solution_source, query_engine, tables, key_filters, report_codes, "
            "analyst_notes, solution, canonical_need, has_solution, domain_tags "
            "FROM era_corpus WHERE id = ANY(%s)", (ids,))
        cols = [c.name for c in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _fetch_table_columns(conn, table_name: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT field_name, business_title, description, data_type "
            "FROM schema_columns WHERE LOWER(table_name) = LOWER(%s) ORDER BY field_name", (table_name,))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _render_table_ddl(conn, table_name: str, description: str | None = None,
                      *, ctx: "RetrievalContext | None" = None) -> str:
    """Render a table as CREATE TABLE DDL + inline column comments (KD5).

    When ``ctx`` is provided, the rendered columns are recorded into
    ``ctx.retrieved_columns`` — so any column the model can see in the DDL counts as
    retrieved for column-level grounding (single source: both ``search_schema`` and
    ``get_table_schema`` go through here).
    """
    columns = _fetch_table_columns(conn, table_name)
    if ctx is not None and columns:
        ctx.retrieved_columns.update(c["field_name"] for c in columns)
    lines = []
    if description:
        lines.append(f"-- {table_name}: {redact_note(description)}")
    lines.append(f"CREATE TABLE {table_name} (")
    if columns:
        for c in columns:
            title = redact_note(c.get("business_title") or "")
            desc = redact_note((c.get("description") or "").replace("\n", " ").strip())
            comment = f"  -- {title}: {desc}".rstrip(": ").rstrip()
            lines.append(f"    {c['field_name']} {c.get('data_type') or 'string'},{comment}")
    else:
        lines.append("    -- (no column dictionary available for this table in the KB)")
    lines.append(");")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool core logic (plain, testable)
# --------------------------------------------------------------------------

def era_knowledge_text(ctx: RetrievalContext, question: str, limit: int = 5) -> str:
    _log.info("search_era_knowledge | question=%r  limit=%d", question[:100], limit)

    rows = hybrid_search_era_corpus(question, limit=limit, conn=ctx.conn)
    ctx.record("era_corpus", question, rows)
    payloads = _fetch_era_payloads(ctx.conn, [r["id"] for r in rows])

    if not rows:
        _log.warning(
            "search_era_knowledge | NO RESULTS — era_top_cosine=0.0"
            " → precedent is advisory; agent proceeds schema-first"
        )
        return "No matching ERA precedents found."

    all_tables: list[str] = []
    blocks = []
    for r in rows:
        p = payloads.get(r["id"], {})
        ctx.retrieved_tables.update(p.get("tables") or [])
        all_tables.extend(p.get("tables") or [])
        tables = ", ".join(p.get("tables") or []) or "—"
        kfs = ", ".join(p.get("key_filters") or []) or "—"
        solved = "yes" if p.get("has_solution") else "no (partial/notes only)"
        header = (f"### Precedent {r['id']}  (source: {p.get('solution_source','?')}, "
                  f"engine: {p.get('query_engine') or '?'}, has_solution: {solved})\n"
                  f"Tables: {tables}\nKey filters: {kfs}")
        notes = redact_note(p.get("analyst_notes") or "")
        sql = redact_sql(p.get("solution") or "")
        parts = [header]
        if notes.strip():
            parts.append("Analyst notes:\n" + _fence(f"{r['id']}:notes", notes))
        if sql.strip():
            parts.append("Precedent SQL:\n" + _fence(f"{r['id']}:sql", sql))
        blocks.append("\n".join(parts))

    top_cosine = rows[0].get("dense_cosine") or 0.0
    precedent_ids = [r["id"] for r in rows]
    _log.info(
        "search_era_knowledge | found=%d  precedents=%s  top_cosine=%.3f  "
        "tables_discovered=%s  coverage_gate_likely=%s",
        len(rows), precedent_ids, top_cosine, sorted(set(all_tables)),
        "PASS" if top_cosine >= 0.45 else f"FAIL (cosine {top_cosine:.3f} < 0.45)",
    )

    return "\n\n".join(blocks)


def schema_text(ctx: RetrievalContext, concept: str, limit: int = 4) -> str:
    _log.info("search_schema | concept=%r  limit=%d", concept[:100], limit)

    rows = hybrid_search("schema_tables", concept, limit=limit, conn=ctx.conn)
    ctx.record("schema_tables", concept, rows)

    if not rows:
        _log.warning(
            "search_schema | NO RESULTS for concept=%r"
            " → schema_top_cosine may stay 0.0 → coverage gate may DECLINE (floor=0.40)",
            concept[:80],
        )
        return "No matching schema tables found."

    with ctx.conn.cursor() as cur:
        cur.execute("SELECT id, table_name, table_description FROM schema_tables "
                    "WHERE id = ANY(%s)", ([r["id"] for r in rows],))
        meta = {r[0]: {"table_name": r[1], "table_description": r[2]}
                for r in cur.fetchall()}

    table_names: list[str] = []
    blocks = []
    for r in rows:
        m = meta.get(r["id"], {})
        tname = m.get("table_name") or r["id"]
        ctx.retrieved_tables.add(tname)
        table_names.append(tname)
        blocks.append(_render_table_ddl(ctx.conn, tname, m.get("table_description"), ctx=ctx))

    top_cosine = rows[0].get("dense_cosine") or 0.0
    _log.info(
        "search_schema | found=%d  tables=%s  top_cosine=%.3f  schema_coverage_likely=%s",
        len(rows), table_names, top_cosine,
        "PASS" if top_cosine >= 0.40 else f"FAIL (cosine {top_cosine:.3f} < 0.40)",
    )

    return "\n\n".join(blocks)


def table_schema_text(ctx: RetrievalContext, table_name: str) -> str:
    _log.info("get_table_schema | table=%r", table_name)

    columns = _fetch_table_columns(ctx.conn, table_name)
    if not columns:
        _log.warning(
            "get_table_schema | table=%r — no column dictionary in KB"
            " (may cause grounding gate to DECLINE if SQL references columns from this table)",
            table_name,
        )
        return (f"No column dictionary found for table '{table_name}'. "
                "(On the sample export some tables are absent / use tid<N> ids.)")

    ctx.retrieved_tables.add(table_name.lower())
    col_names = [c["field_name"] for c in columns]
    _log.info(
        "get_table_schema | table=%r  columns=%d  names=%s",
        table_name, len(columns), col_names,
    )
    # ctx=ctx records retrieved_columns (single source in _render_table_ddl).
    return _render_table_ddl(ctx.conn, table_name, ctx=ctx)


# --------------------------------------------------------------------------
# @tool wrappers, bound to a per-request context via closure
# --------------------------------------------------------------------------

def build_tools(ctx: RetrievalContext):
    """Build the 3 knowledge tools bound to ``ctx`` (robust across tool-exec threads)."""

    def search_era_knowledge(question: str) -> str:
        return era_knowledge_text(ctx, question)
    search_era_knowledge.__doc__ = load_prompt("search_era_knowledge")
    search_era_knowledge = tool(search_era_knowledge)

    def search_schema(concept: str) -> str:
        return schema_text(ctx, concept)
    search_schema.__doc__ = load_prompt("search_schema")
    search_schema = tool(search_schema)

    def get_table_schema(table_name: str) -> str:
        return table_schema_text(ctx, table_name)
    get_table_schema.__doc__ = load_prompt("get_table_schema")
    get_table_schema = tool(get_table_schema)

    return [search_era_knowledge, search_schema, get_table_schema]
