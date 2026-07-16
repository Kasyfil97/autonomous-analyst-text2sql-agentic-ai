"""Unit 0 (BRISA plan) — one-off, read-only live-KB inspection.

Measures the metadata reality that gates the search-surface card/facet shape and the
demo-dataset curation (plan Unit 0):

  * schema_tables: row count, `domain_tags` population + distinct domains, business-title
    availability, `[AI]` boilerplate rate, `tid<N>` id prevalence.
  * schema_columns: business-title fill rate, PII-fragment hits (gates.RESTRICTED_FRAGMENTS),
    `tid<N>` parent-table prevalence.
  * denylist reachability: can the read-only role even SELECT a TABLE_DENYLIST table?
  * (optional) curated-question top-5 hit rate via retrieval.hybrid_search, when a
    questions file is passed — the KPI reality check from the plan's Unit 0 decision gate.

Read-only: uses ``pg_config(readonly=True)``; issues only SELECTs. Run from the repo root:

    python scripts/inspect_kb.py
    python scripts/inspect_kb.py --questions docs/brisa-demo-questions.txt --target-col table_name

The questions file (optional) is one line per demo question; blank lines and lines
starting with ``#`` are ignored. ``--expect`` (repeatable) or an inline ``\t``-separated
expected-table on each question line lets the script compute an actual top-5 hit rate.
"""
from __future__ import annotations

import argparse
import os
import sys

# Repo-root on path so `text2sql.*` imports resolve when run as a plain script (mirrors conftest.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import psycopg2  # noqa: E402
from psycopg2 import sql  # noqa: E402

from text2sql import gates  # noqa: E402
from text2sql.embedding_service import pg_config  # noqa: E402


def _columns(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def _scalar(cur, sql: str, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return row[0] if row else None


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.1f}%" if total else "n/a"


def inspect_schema_tables(cur) -> dict:
    cols = _columns(cur, "schema_tables")
    total = _scalar(cur, "SELECT count(*) FROM schema_tables") or 0
    out: dict = {"total_rows": total, "columns_present": sorted(cols)}

    if "domain_tags" in cols:
        populated = _scalar(
            cur,
            "SELECT count(*) FROM schema_tables "
            "WHERE domain_tags IS NOT NULL AND array_length(domain_tags, 1) > 0",
        ) or 0
        out["domain_tags_populated"] = f"{populated}/{total} ({_pct(populated, total)})"
        cur.execute(
            "SELECT DISTINCT unnest(domain_tags) AS d FROM schema_tables "
            "WHERE domain_tags IS NOT NULL ORDER BY d"
        )
        out["distinct_domains"] = [r[0] for r in cur.fetchall()]
    else:
        out["domain_tags_populated"] = "COLUMN ABSENT — R8 facet has no direct backing"

    # Table-level business title: column name varies; probe the likely candidates.
    title_col = next((c for c in ("business_title", "table_business_title") if c in cols), None)
    if title_col:
        non_null = _scalar(
            cur, f"SELECT count(*) FROM schema_tables WHERE {title_col} IS NOT NULL "
                 f"AND btrim({title_col}) <> ''") or 0
        out["business_title"] = f"col={title_col}: {non_null}/{total} ({_pct(non_null, total)}) non-empty"
    else:
        out["business_title"] = "no table-level business_title column — R5 headline falls back to table_name"

    if "table_description" in cols:
        ai = _scalar(cur, "SELECT count(*) FROM schema_tables WHERE table_description LIKE %s",
                     ("[AI]%",)) or 0
        out["ai_boilerplate_descriptions"] = f"{ai}/{total} ({_pct(ai, total)})"

    tid = _scalar(cur, "SELECT count(*) FROM schema_tables WHERE table_name LIKE %s", ("tid%",)) or 0
    out["tid<N>_table_names"] = f"{tid}/{total} ({_pct(tid, total)})"
    return out


def inspect_schema_columns(cur) -> dict:
    cols = _columns(cur, "schema_columns")
    total = _scalar(cur, "SELECT count(*) FROM schema_columns") or 0
    out: dict = {"total_rows": total, "columns_present": sorted(cols)}

    if "business_title" in cols:
        non_null = _scalar(
            cur, "SELECT count(*) FROM schema_columns WHERE business_title IS NOT NULL "
                 "AND btrim(business_title) <> ''") or 0
        out["business_title_filled"] = f"{non_null}/{total} ({_pct(non_null, total)})"

    # PII-fragment hits on field names (gates.RESTRICTED_FRAGMENTS) — informs the R5a badge.
    if "field_name" in cols:
        cur.execute("SELECT DISTINCT lower(field_name) FROM schema_columns WHERE field_name IS NOT NULL")
        names = [r[0] for r in cur.fetchall()]
        hits = sorted({n for n in names for frag in gates.RESTRICTED_FRAGMENTS if frag in n})
        out["pii_fragment_columns"] = {"count": len(hits), "sample": hits[:30]}

    if "table_name" in cols:
        tid = _scalar(cur, "SELECT count(*) FROM schema_columns WHERE table_name LIKE %s", ("tid%",)) or 0
        out["tid<N>_parent_tables"] = f"{tid}/{total} ({_pct(tid, total)})"
    return out


def check_denylist_reachability(cur) -> dict:
    """Confirm whether the read-only role can even SELECT a denylisted table (hardens R4a)."""
    out: dict = {}
    for tbl in sorted(gates.TABLE_DENYLIST):
        try:
            cur.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(tbl)))
            cur.fetchone()
            out[tbl] = "READABLE (application-level denylist is the only barrier)"
        except psycopg2.Error as exc:
            cur.connection.rollback()
            out[tbl] = f"blocked at DB layer ({exc.pgcode}) — defense-in-depth confirmed"
    return out


def _load_questions(path: str) -> list[tuple[str, str | None]]:
    items: list[tuple[str, str | None]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "\t" in line:
                q, expected = line.split("\t", 1)
                items.append((q.strip(), expected.strip() or None))
            else:
                items.append((line.strip(), None))
    return items


def kpi_top5(conn, questions_path: str) -> dict:
    """Run curated questions through hybrid_search('schema_tables') and report top-5 ids.

    When a question carries an expected table (tab-separated), compute the hit rate — the
    plan's Unit 0 decision gate (target >= 80%).
    """
    from text2sql.retrieval import hybrid_search  # local import: needs the embedding endpoint

    items = _load_questions(questions_path)
    results = []
    hits = 0
    graded = 0
    for q, expected in items:
        rows = hybrid_search("schema_tables", q, limit=5, conn=conn)
        top5 = [r["id"] for r in rows]
        rec = {"question": q, "top5": top5}
        if expected:
            graded += 1
            hit = any(expected.lower() in str(i).lower() for i in top5)
            hits += int(hit)
            rec["expected"] = expected
            rec["hit"] = hit
        results.append(rec)
    summary = {"questions": len(items), "graded": graded}
    if graded:
        rate = 100.0 * hits / graded
        summary["top5_hit_rate"] = f"{hits}/{graded} ({rate:.1f}%)"
        summary["meets_80pct_gate"] = rate >= 80.0
    return {"summary": summary, "detail": results}


def main() -> None:
    ap = argparse.ArgumentParser(description="BRISA Unit 0 — read-only KB inspection.")
    ap.add_argument("--questions", help="Optional curated-questions file for the top-5 KPI check.")
    args = ap.parse_args()

    import json

    conn = psycopg2.connect(**pg_config(readonly=True))
    try:
        report: dict = {}
        with conn.cursor() as cur:
            report["schema_tables"] = inspect_schema_tables(cur)
            report["schema_columns"] = inspect_schema_columns(cur)
            report["denylist_reachability"] = check_denylist_reachability(cur)
        if args.questions:
            report["kpi_top5"] = kpi_top5(conn, args.questions)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
