"""BRISA Data Search Engine (plan Unit 2) — pure semantic table search, no LLM.

Ranks with ``retrieval.hybrid_search`` over ``schema_tables`` only, hydrates the ranked ids
into policy-safe table cards, and lazy-loads a column dictionary on demand. All policy controls
that live on the agent path are re-applied here (R4a): the ``TABLE_DENYLIST`` is filtered out,
free-text is PII-redacted via ``redact_note`` before serialization, the PII badge uses the shared
``gates.restricted_columns`` classifier, and raw cosine/RRF scores are never emitted (R6).
"""
from __future__ import annotations

import re

from text2sql import gates
from text2sql.audit_log import get_logger, new_request
from text2sql.retrieval import hybrid_search
from text2sql.tools import redact_note

_log = get_logger("search")

# Ranked pool multiplier when a domain facet is applied (rank first, then filter).
_DOMAIN_POOL_FACTOR = 4


def humanize(table_name: str) -> str:
    """Turn a physical table name into a readable headline (no table-level business title exists)."""
    name = re.sub(r"^[a-z0-9_]+\.", "", table_name or "")  # strip a schema prefix if present
    name = re.sub(r"^\d+_", "", name)                       # strip leading numeric prefixes
    humanized = name.replace("_", " ").strip()
    return humanized.title() if humanized else (table_name or "")


def _hydrate(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, table_name, table_description, domain_tags, column_names, n_columns "
            "FROM schema_tables WHERE id = ANY(%s)", (ids,))
        cols = [c.name for c in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _pii_status(column_names: list[str] | None) -> str:
    """R5a: 'present' when a name hits the PII heuristic, else 'unclassified' — never 'safe'."""
    return "present" if gates.restricted_columns(column_names or []) else "unclassified"


def _card(meta: dict) -> dict:
    tname = meta.get("table_name") or ""
    return {
        "id": meta["id"],
        "table_name": tname,
        "physical_name": meta["id"],                 # schema.table_name (the id is schema-qualified)
        "headline": humanize(tname),
        "description": redact_note(meta.get("table_description") or ""),
        "domain_tags": meta.get("domain_tags") or [],
        "n_columns": meta.get("n_columns"),
        "pii": _pii_status(meta.get("column_names")),
    }


def search_tables(conn, query: str, *, domain: str | None = None, limit: int = 10) -> dict:
    """Ranked, policy-filtered table cards. No LLM, no scores in the payload."""
    new_request()
    query = (query or "").strip()
    if not query:
        return {"query": query, "domain": domain, "results": [], "filter_caused_empty": False}

    pool_limit = limit * _DOMAIN_POOL_FACTOR if domain else limit
    ranked = hybrid_search("schema_tables", query, limit=pool_limit, conn=conn)
    meta = _hydrate(conn, [r["id"] for r in ranked])

    unfiltered: list[dict] = []
    filtered: list[dict] = []
    for r in ranked:
        m = meta.get(r["id"])
        if not m:
            continue
        tname = (m.get("table_name") or "")
        if tname.lower() in gates.TABLE_DENYLIST:      # R4a: defense-in-depth (DB already blocks)
            continue
        card = _card(m)
        unfiltered.append(card)
        if domain and domain not in (m.get("domain_tags") or []):
            continue
        filtered.append(card)

    results = (filtered if domain else unfiltered)[:limit]
    # R9: if a domain filter emptied the results, say so and offer the unfiltered closest matches.
    filter_caused = bool(domain and not filtered and unfiltered)
    out = {"query": query, "domain": domain, "results": results,
           "filter_caused_empty": filter_caused}
    if filter_caused:
        out["closest_related"] = unfiltered[:limit]
    _log.info("search_tables | q=%r domain=%r ranked=%d returned=%d filter_caused_empty=%s",
              query[:80], domain, len(ranked), len(results), filter_caused)
    return out


def table_columns(conn, table_name: str) -> list[dict]:
    """Lazy column dictionary for one table (R7), PII-flagged and redacted."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT field_name, business_title, description, data_type FROM schema_columns "
            "WHERE LOWER(table_name) = LOWER(%s) ORDER BY field_name", (table_name,))
        rows = cur.fetchall()
    out = []
    for field_name, business_title, description, data_type in rows:
        out.append({
            "field_name": field_name,
            "business_title": redact_note(business_title or ""),
            "description": redact_note((description or "").replace("\n", " ").strip()),
            "data_type": data_type or "string",
            "pii": bool(gates.restricted_fragment(field_name)),
        })
    return out


def list_domains(conn) -> list[str]:
    """Distinct domain tags for the R8 facet."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT unnest(domain_tags) AS d FROM schema_tables "
                    "WHERE domain_tags IS NOT NULL ORDER BY d")
        return [r[0] for r in cur.fetchall()]
