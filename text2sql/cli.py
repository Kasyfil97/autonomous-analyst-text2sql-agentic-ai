"""Interactive CLI for the Text-to-SQL agent (plan U7).

    python -m text2sql.cli

Type a question (Indonesian or English); get a draft SQL query + reasoning + sources,
or an honest decline naming what knowledge was missing. Type 'exit' or 'quit' to leave.
Set T2S_DEBUG=1 for verbose auth/identity output.
"""
from __future__ import annotations

import sys

from text2sql.agent import Text2SQLResult, generate_sql, get_session


def format_result(r: Text2SQLResult) -> str:
    if r.declined:
        return f"\n⚠️  Cannot produce SQL — {r.missing or 'insufficient knowledge'}\n"
    lines = ["", f"Dialect:    {r.dialect or '?'}"]
    if r.precedent_ids:
        lines.append(f"Precedents: {', '.join(r.precedent_ids)}")
    if r.tables_used:
        lines.append(f"Tables:     {', '.join(r.tables_used)}")
    if r.explanation:
        lines.append(f"\n{r.explanation}")
    lines.append(f"\nSQL:\n{r.sql}\n")
    return "\n".join(lines)


def run_repl(generate=generate_sql, in_fn=input, out=print) -> None:
    """The interactive loop, factored out for testability."""
    while True:
        try:
            question = in_fn("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            out("\nBye!")
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            out("Bye!")
            break
        try:
            result = generate(question)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            out(f"\n❌ {type(exc).__name__}: {exc}\n")
            continue
        out(format_result(result))


def main() -> None:
    print("=" * 60)
    print("💬 Text-to-SQL Agent (draft SQL only — no execution)")
    print("   Grounded in ERA precedents + schema KB. 'exit' to quit.")
    print("=" * 60)
    session = get_session()  # authenticate once up front
    run_repl(generate=lambda q: generate_sql(q, session=session))


if __name__ == "__main__":
    sys.exit(main())
