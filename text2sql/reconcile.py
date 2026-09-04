"""Reconcile multiple sub-drafts into a combined view (plan rec #3).

After each sub-need is drafted independently (Case A composition), this step explains
how they fit together (join keys, unified filters, the shared "terkini"/time window) and
offers a best-effort ``combined_sql``. It also resolves a dialect conflict deterministically.

Guardrails:
- The verified per-sub-need SQL is never rewritten by the model; ``combined_sql`` is
  advisory, prefixed with the UNVERIFIED marker, and clearly a suggestion.
- Every step is non-fatal: an invoke failure or unparseable reply degrades to
  "no reconciliation", keeping the sub-drafts intact.
"""
from __future__ import annotations

from text2sql import gates
from text2sql.agent import SubDraft, _extract_json
from text2sql.audit_log import get_logger
from text2sql.prompt_loader import load_prompt

_log = get_logger("reconcile")

RECONCILE_PROMPT = load_prompt("reconcile_prompt")


def resolve_dialect(sub_drafts: list[SubDraft]) -> tuple[str | None, str | None]:
    """Pick one dialect across sub-drafts; warn on conflict.

    Returns ``(dialect, warning_or_None)``. On disagreement, the dialect of the
    sub-draft with the highest ERA precedent cosine wins (most-grounded precedent).
    """
    dialects = [d.result.dialect for d in sub_drafts if d.result.dialect]
    distinct = sorted(set(dialects))
    if len(distinct) <= 1:
        return (distinct[0] if distinct else None), None
    best = max(sub_drafts, key=lambda d: (d.result.era_top_cosine or 0.0))
    chosen = best.result.dialect
    warn = (f"[HIGH] dialect conflict across sub-drafts {distinct}; using {chosen!r} "
            "(from the highest-confidence precedent) — verify each part parses under it")
    _log.warning("resolve_dialect | conflict=%s chosen=%r", distinct, chosen)
    return chosen, warn


def _summarize(sub_drafts: list[SubDraft]) -> str:
    """Compact, model-readable summary of each sub-draft for the reconcile prompt."""
    blocks = []
    for i, d in enumerate(sub_drafts, 1):
        r = d.result
        sql = (r.sql or "").replace(gates.UNVERIFIED_MARKER, "").strip()
        blocks.append(
            f"### Sub-need {i}: {d.sub_need}\n"
            f"tables: {', '.join(r.tables_used) or '—'}\n"
            f"dialect: {r.dialect or '—'}\n"
            f"SQL:\n{sql or '(no draft)'}"
        )
    return "\n\n".join(blocks)


def reconcile(question: str, sub_drafts: list[SubDraft], session) -> dict:
    """Return ``{reconciliation, combined_sql, warnings}`` (all best-effort/optional)."""
    warnings: list[str] = []
    dialect, dwarn = resolve_dialect(sub_drafts)
    if dwarn:
        warnings.append(dwarn)

    # Only sub-drafts that actually produced SQL are worth combining.
    drafted = [d for d in sub_drafts if (d.result.sql or "").strip()]
    if len(drafted) < 2:
        return {"reconciliation": None, "combined_sql": None, "warnings": warnings}

    user = (f"Original request: {question}\n\n"
            f"Target dialect: {dialect or 'unknown'}\n\n"
            f"{_summarize(sub_drafts)}")
    messages = [
        {"role": "system", "content": RECONCILE_PROMPT},
        {"role": "user", "content": user},
    ]
    try:
        message = session.invoke(messages, max_tokens=1500, temperature=0.0)
        data = _extract_json(message.get("content", "")) or {}
    except Exception as exc:  # noqa: BLE001 — reconciliation is optional, never fatal
        _log.warning("reconcile | invoke/parse failed (%s) — sub-drafts kept, no merge",
                     type(exc).__name__)
        return {"reconciliation": None, "combined_sql": None, "warnings": warnings}

    reconciliation = (data.get("reconciliation") or "").strip() or None
    combined = (data.get("combined_sql") or "").strip()
    combined_sql = (gates.UNVERIFIED_MARKER + "\n" + combined) if combined else None
    _log.info("reconcile | reconciliation=%s combined_sql=%s dialect=%r",
              bool(reconciliation), bool(combined_sql), dialect)
    return {"reconciliation": reconciliation, "combined_sql": combined_sql,
            "warnings": warnings}
