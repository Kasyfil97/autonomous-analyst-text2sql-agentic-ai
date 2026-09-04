"""Interactive CLI for the Text-to-SQL agent (plan U7).

    python -m text2sql.cli

Type a question (Indonesian or English); get a draft SQL query + reasoning + sources,
or an honest decline naming what knowledge was missing. Type 'exit' or 'quit' to leave.
Set T2S_DEBUG=1 for verbose auth/identity output.
"""
from __future__ import annotations

import sys

from text2sql.agent import MultiDraftResult, Text2SQLResult, get_session
from text2sql.audit_log import get_logger
from text2sql.orchestrator import handle as orchestrate
from text2sql.search_agent import SearchResult

_log = get_logger("cli")


def format_search_result(r: SearchResult) -> str:
    if r.declined:
        return f"\n⚠️  {r.missing or 'no answer'}\n"
    lines = ["", r.answer or "(no answer)"]
    if r.sources.get("era_precedents"):
        lines.append(f"\nSources (ERA): {', '.join(r.sources['era_precedents'])}")
    if r.sources.get("tables"):
        lines.append(f"Sources (tables): {', '.join(r.sources['tables'])}")
    if r.warnings:
        lines.append("\n⚠️  GATE WARNINGS — review before relying on this answer:")
        lines.extend(f"  ⚠️  {w}" for w in r.warnings)
    lines.append("")
    return "\n".join(lines)


def format_multidraft_result(r: MultiDraftResult) -> str:
    """Render a decomposed multi-part answer: each sub-draft via the single-draft
    formatter, then the reconciliation + any combined SQL and cross-draft warnings."""
    lines = ["", f"MULTI-PART REQUEST — {len(r.sub_drafts)} sub-draft(s)"]
    for i, sd in enumerate(r.sub_drafts, 1):
        lines.append("\n" + "=" * 56)
        lines.append(f"[{i}] {sd.sub_need}")
        lines.append(format_result(sd.result).rstrip())
    if r.reconciliation:
        lines.append("\n" + "=" * 56)
        lines.append("RECONCILIATION:\n" + r.reconciliation)
    if r.combined_sql:
        lines.append(f"\nCombined SQL (suggestion — verify):\n{r.combined_sql}")
    if r.warnings:
        lines.append("\n⚠️  CROSS-DRAFT WARNINGS:")
        lines.extend(f"  ⚠️  {w}" for w in r.warnings)
    lines.append("")
    return "\n".join(lines)


def format_result(r: Text2SQLResult | SearchResult | MultiDraftResult) -> str:
    if isinstance(r, MultiDraftResult):
        return format_multidraft_result(r)
    if isinstance(r, SearchResult):
        return format_search_result(r)
    if r.declined:
        return f"\n⚠️  Cannot produce SQL — {r.missing or 'insufficient knowledge'}\n"
    lines = ["", f"Dialect:    {r.dialect or '?'}"]
    if r.precedent_ids:
        lines.append(f"Precedents: {', '.join(r.precedent_ids)}")
    if r.tables_used:
        lines.append(f"Tables:     {', '.join(r.tables_used)}")
    if r.explanation:
        lines.append(f"\n{r.explanation}")
    if r.assumptions:
        lines.append("\nAssumptions (verify before running):")
        lines.extend(f"  • {a}" for a in r.assumptions)
    if r.warnings:
        lines.append("\n⚠️  GATE WARNINGS — this draft did not clear every safety gate."
                     " Review carefully before use:")
        lines.extend(f"  ⚠️  {w}" for w in r.warnings)
    lines.append(f"\nSQL:\n{r.sql}\n")
    return "\n".join(lines)


def run_repl(generate=orchestrate, in_fn=input, out=print) -> None:
    """The interactive loop, factored out for testability."""
    while True:
        try:
            question = in_fn("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            out("\nBye!")
            _log.info("session END — user interrupted")
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            out("Bye!")
            _log.info("session END — user typed exit/quit")
            break
        _log.info("question RECEIVED | %r", question[:120])
        try:
            result = generate(question)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            _log.error("generate EXCEPTION | %s: %s", type(exc).__name__, exc)
            out(f"\n❌ {type(exc).__name__}: {exc}\n")
            continue
        if result.declined:
            _log.warning("question DECLINED | reason=%r", result.missing)
        elif isinstance(result, SearchResult):
            _log.info("question ANSWERED (search) | found=%s  sources=%s", result.found, result.sources)
        elif isinstance(result, MultiDraftResult):
            _log.info("question ANSWERED (multi) | sub_drafts=%d", len(result.sub_drafts))
        else:
            _log.info("question ANSWERED | dialect=%r  tables=%s", result.dialect, result.tables_used)
        out(format_result(result))


def main() -> None:
    print("=" * 60)
    print("💬 Text-to-SQL Agent (draft SQL + KB search — no execution)")
    print("   Ask for a query, or explore the KB (tables, columns, ERA tickets).")
    print("   Grounded in ERA precedents + schema KB. 'exit' to quit.")
    print("=" * 60)
    _log.info("session START — authenticating (OIDC)")
    session = get_session()  # authenticate once up front
    _log.info("session READY — OIDC auth complete")
    run_repl(generate=lambda q: orchestrate(q, session=session))


if __name__ == "__main__":
    sys.exit(main())
