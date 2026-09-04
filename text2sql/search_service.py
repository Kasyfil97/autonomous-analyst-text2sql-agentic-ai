"""Sage Data Search Engine (plan Unit 2) — pure semantic table search, no LLM.

Ranks with ``retrieval.hybrid_search`` over ``schema_tables`` only, hydrates the ranked ids
into policy-safe table cards, and lazy-loads a column dictionary on demand. All policy controls
that live on the agent path are re-applied here (R4a): the ``TABLE_DENYLIST`` is filtered out,
free-text is PII-redacted via ``redact_note`` before serialization, and the PII badge uses the
shared ``gates.restricted_columns`` classifier.

Relevance signals ARE emitted (supersedes the original R6 no-scores rule): each search result
carries ``relevance`` (dense cosine, 0-1, ``None`` if the row was found only by the sparse lane)
and ``score`` (the fused RRF score used for ordering). Deterministic lookups (``table_detail``,
``table_columns``) have no query, so they carry no relevance signals.
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


def _attach_scores(card: dict, ranked_row: dict) -> dict:
    """Attach relevance signals from a ranked retrieval row onto a result card (in place).

    ``relevance`` = dense cosine (0-1; ``None`` when the row was found only by the sparse lane).
    ``score`` = the fused RRF score used for ordering (small by construction, ~0.03 max/lane).
    """
    # Coerce to float: Postgres returns the RRF score as Decimal, which is not JSON-serializable
    # by the API's default encoder.
    dc = ranked_row.get("dense_cosine")
    card["relevance"] = round(float(dc), 4) if dc is not None else None
    card["score"] = round(float(ranked_row.get("score") or 0.0), 6)
    return card


def search_tables(conn, query: str, *, domain: str | None = None, limit: int = 10) -> dict:
    """Ranked, policy-filtered table cards (no LLM). Each card carries relevance + RRF score."""
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
        card = _attach_scores(_card(m), r)
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


def table_detail(conn, table_id: str) -> dict | None:
    """Full detail for a single table, addressed by its schema-qualified id (physical name).

    Returns ``{"card": ..., "columns": [...]}`` or ``None`` when the id is unknown or resolves to
    a denylisted table (R4a: defense-in-depth, even though the DB role already blocks it).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, table_name, table_description, domain_tags, column_names, n_columns "
            "FROM schema_tables WHERE id = %s", (table_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c.name for c in cur.description]
        meta = dict(zip(cols, row))

    if (meta.get("table_name") or "").lower() in gates.TABLE_DENYLIST:
        return None
    return {"card": _card(meta), "columns": table_columns(conn, meta.get("table_name") or "")}


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


def _hydrate_columns(conn, ids: list[str]) -> dict[str, dict]:
    """Hydrate ranked ``schema_columns`` ids into their payload rows (id = schema.table.field)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, table_name, field_name, business_title, description, data_type, domain_tags "
            "FROM schema_columns WHERE id = ANY(%s)", (ids,))
        cols = [c.name for c in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _norm_table_filter(name: str) -> str:
    """Normalize a table filter to a bare, lower-cased table name.

    Accepts either a bare table name or a schema-qualified physical name (``schema.table``);
    ``schema_columns.table_name`` stores the bare name, so a leading schema segment is stripped.
    """
    name = (name or "").strip()
    if "." in name:                       # schema.table → table (table_name has no dot)
        name = name.split(".", 1)[1]
    return name.lower()


def _column_card(meta: dict) -> dict:
    """One column result card — same field-level shape as ``table_columns`` plus table context.

    Relevance signals (``relevance``/``score``) are attached by the caller via ``_attach_scores``.
    R4a: free-text redacted. R5a: column PII is a bool (``restricted_fragment``), matching
    ``table_columns`` (the table-card 'present'/'unclassified' badge is a different signal).
    """
    field_name = meta.get("field_name") or ""
    return {
        "id": meta["id"],                                # schema.table.field
        "physical_name": meta["id"],
        "table_name": meta.get("table_name") or "",
        "field_name": field_name,
        "business_title": redact_note(meta.get("business_title") or ""),
        "description": redact_note((meta.get("description") or "").replace("\n", " ").strip()),
        "data_type": meta.get("data_type") or "string",
        "domain_tags": meta.get("domain_tags") or [],
        "pii": bool(gates.restricted_fragment(field_name)),
    }


def search_columns_semantic(conn, query: str, *, table: str | None = None,
                            domain: str | None = None, limit: int = 10) -> dict:
    """Ranked, policy-filtered column cards from ``schema_columns`` (semantic, no LLM).

    ``table`` scopes the search to a single table (filter-then-rank, pushed into the retrieval
    query): results are the most similar columns *within that table only*. Without ``table`` the
    whole corpus is ranked. ``domain`` is a broad facet applied as rank-then-filter; when it empties
    the results, ``filter_caused_empty`` + ``closest_related`` report it (R9). Each result carries
    ``relevance`` (dense cosine) and ``score`` (RRF).
    """
    new_request()
    query = (query or "").strip()
    if not query:
        return {"query": query, "table": table, "domain": domain, "results": [],
                "filter_caused_empty": False}

    norm_table = _norm_table_filter(table) if table else None
    # domain is a broad facet → over-fetch then post-filter. A table scope is pushed into the
    # query (table_filter), so no pool inflation is needed for it.
    pool_limit = limit * _DOMAIN_POOL_FACTOR if domain else limit
    pool = max(50, pool_limit)
    ranked = hybrid_search("schema_columns", query, limit=pool_limit, pool=pool, conn=conn,
                           table_filter=norm_table)
    meta = _hydrate_columns(conn, [r["id"] for r in ranked])

    unfiltered: list[dict] = []
    filtered: list[dict] = []
    for r in ranked:
        m = meta.get(r["id"])
        if not m:
            continue
        tname = (m.get("table_name") or "")
        if tname.lower() in gates.TABLE_DENYLIST:       # R4a: defense-in-depth (DB already blocks)
            continue
        card = _attach_scores(_column_card(m), r)
        unfiltered.append(card)
        if domain and domain not in (m.get("domain_tags") or []):
            continue
        filtered.append(card)

    results = (filtered if domain else unfiltered)[:limit]
    filter_caused = bool(domain and not filtered and unfiltered)
    out = {"query": query, "table": table, "domain": domain, "results": results,
           "filter_caused_empty": filter_caused}
    if filter_caused:
        out["closest_related"] = unfiltered[:limit]
    _log.info("search_columns_semantic | q=%r table=%r domain=%r ranked=%d returned=%d "
              "filter_caused_empty=%s", query[:80], table, domain, len(ranked), len(results),
              filter_caused)
    return out


def list_domains(conn) -> list[str]:
    """Distinct domain tags for the R8 facet."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT unnest(domain_tags) AS d FROM schema_tables "
                    "WHERE domain_tags IS NOT NULL ORDER BY d")
        return [r[0] for r in cur.fetchall()]
