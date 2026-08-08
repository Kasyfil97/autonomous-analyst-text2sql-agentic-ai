"""Evaluation script for text2sql agent.

Reads test.xlsx, runs generate_sql() on each question, computes recall against
ground-truth tickets, tables, and columns, and writes results to a timestamped
Excel file in this directory.

Usage (from repo root):
    python evaluation/run_eval.py [--limit N] [--start N] [--out-dir evaluation]
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Ensure the repo root is on sys.path so text2sql imports work.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import xlsxwriter

from text2sql.agent import generate_sql


# ---------------------------------------------------------------------------
# Ground-truth parsers
# ---------------------------------------------------------------------------

def _parse_ticket_gt(raw) -> list[str]:
    """'ERA22-1151' or 'ERA22-1250 or ERA22-1275' -> list of stripped IDs."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    # Split on ' or ' (case-insensitive)
    parts = re.split(r'\s+or\s+', raw.strip(), flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _parse_list_gt(raw) -> list[str]:
    """Parse ground-truth table or column values.

    Handles:
    - Python list literals: "['AS4_LNMAST','LWEXT3']"
    - Comma-separated strings: "accrued_interest, cifno, debet"
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    raw = raw.strip()
    # Try Python literal first (handles quoted lists with brackets)
    if raw.startswith("["):
        try:
            items = ast.literal_eval(raw)
            return [str(i).strip() for i in items if str(i).strip()]
        except (ValueError, SyntaxError):
            pass
    # Fallback: comma-separated
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Recall helper
# ---------------------------------------------------------------------------

def recall(predicted: list[str], ground_truth: list[str]) -> float | None:
    """Case-insensitive recall = |pred ∩ gt| / |gt|. None when gt is empty."""
    if not ground_truth:
        return None
    gt_lower = {g.lower() for g in ground_truth}
    pred_lower = {p.lower() for p in predicted}
    hits = len(pred_lower & gt_lower)
    return hits / len(gt_lower)


def _list_to_str(lst: list[str]) -> str:
    """Serialize list to semicolon-separated string for Excel."""
    return "; ".join(lst) if lst else ""


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_evaluation(
    test_file: Path,
    out_dir: Path,
    limit: int | None,
    start: int,
) -> Path:
    df = pd.read_excel(test_file)
    rows = df.itertuples(index=True)

    results: list[dict] = []

    # Collect the subset to evaluate
    subset = [(i, row) for i, row in enumerate(df.itertuples(index=False)) if i >= start]
    if limit is not None:
        subset = subset[:limit]

    total = len(subset)
    print(f"Evaluating {total} questions (start={start}, limit={limit or 'all'})\n")

    for seq, (idx, row) in enumerate(subset, 1):
        question = row.pertanyaan if hasattr(row, "pertanyaan") else row[df.columns.get_loc("pertanyaan")]
        print(f"[{seq}/{total}] Q: {str(question)[:80]!r}")

        # Parse ground truths
        gt_tickets = _parse_ticket_gt(getattr(row, "ground_truth_ticket", None))
        gt_tables  = _parse_list_gt(getattr(row, "ground_truth_table", None))
        gt_columns = _parse_list_gt(getattr(row, "ground_truth_column", None))

        # Call the agent
        predicted_tickets: list[str] = []
        predicted_tables:  list[str] = []
        predicted_columns: list[str] = []
        error_msg: str = ""
        sql_produced: str = ""

        try:
            result = generate_sql(question)
            predicted_tickets = result.precedent_ids or []
            predicted_tables  = result.tables_used or []
            predicted_columns = result.columns_used or []
            sql_produced      = (result.sql or "").strip()
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

        # Compute recall
        ticket_recall = recall(predicted_tickets, gt_tickets)
        table_recall  = recall(predicted_tables, gt_tables)
        column_recall = recall(predicted_columns, gt_columns)

        print(
            f"  tickets: {predicted_tickets} | gt: {gt_tickets} | recall: {ticket_recall}\n"
            f"  tables:  {predicted_tables} | gt: {gt_tables} | recall: {table_recall}\n"
            f"  columns: {predicted_columns} | gt: {gt_columns} | recall: {column_recall}"
        )
        if error_msg:
            print(f"  ERROR: {error_msg}")
        print()

        record = {
            # Passthrough input columns
            "row_index": idx,
            "Analyst":   getattr(row, "Analyst", ""),
            "category":  getattr(row, "category", ""),
            "pertanyaan": question,
            # Ground truths (normalized)
            "gt_tickets":  _list_to_str(gt_tickets),
            "gt_tables":   _list_to_str(gt_tables),
            "gt_columns":  _list_to_str(gt_columns),
            # Model output
            "pred_tickets":  _list_to_str(predicted_tickets),
            "pred_tables":   _list_to_str(predicted_tables),
            "pred_columns":  _list_to_str(predicted_columns),
            "sql_produced":  sql_produced[:2000],  # truncate to keep xlsx sane
            "error":         error_msg,
            # Per-row recall
            "ticket_recall": ticket_recall,
            "table_recall":  table_recall,
            "column_recall": column_recall,
        }
        results.append(record)

    # ------------------------------------------------------------------
    # Build output DataFrame
    # ------------------------------------------------------------------
    detail_df = pd.DataFrame(results)

    # ------------------------------------------------------------------
    # Summary stats
    # ------------------------------------------------------------------
    ticket_rows  = detail_df["ticket_recall"].dropna()
    table_rows   = detail_df["table_recall"].dropna()
    column_rows  = detail_df["column_recall"].dropna()

    summary = pd.DataFrame([
        {
            "metric":          "ticket_recall",
            "n_questions":     len(ticket_rows),
            "mean_recall":     ticket_rows.mean() if len(ticket_rows) else None,
            "recall_@1.0":     (ticket_rows == 1.0).sum(),
            "recall_@0.5plus": (ticket_rows >= 0.5).sum(),
            "recall_@0":       (ticket_rows == 0.0).sum(),
        },
        {
            "metric":          "table_recall",
            "n_questions":     len(table_rows),
            "mean_recall":     table_rows.mean() if len(table_rows) else None,
            "recall_@1.0":     (table_rows == 1.0).sum(),
            "recall_@0.5plus": (table_rows >= 0.5).sum(),
            "recall_@0":       (table_rows == 0.0).sum(),
        },
        {
            "metric":          "column_recall",
            "n_questions":     len(column_rows),
            "mean_recall":     column_rows.mean() if len(column_rows) else None,
            "recall_@1.0":     (column_rows == 1.0).sum(),
            "recall_@0.5plus": (column_rows >= 0.5).sum(),
            "recall_@0":       (column_rows == 0.0).sum(),
        },
    ])

    # ------------------------------------------------------------------
    # Write Excel
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d%H")
    out_path = out_dir / f"eval_{timestamp}.xlsx"

    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        detail_df.to_excel(writer, sheet_name="Detail", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)

        # Light formatting
        wb = writer.book
        hdr_fmt = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "#FFFFFF"})
        pct_fmt = wb.add_format({"num_format": "0.00%"})

        # Detail sheet
        ws_detail = writer.sheets["Detail"]
        for col_num, col_name in enumerate(detail_df.columns):
            ws_detail.write(0, col_num, col_name, hdr_fmt)
            # Apply percent format to recall columns
            if "recall" in col_name:
                ws_detail.set_column(col_num, col_num, 14, pct_fmt)
            elif col_name in ("pertanyaan", "sql_produced", "error"):
                ws_detail.set_column(col_num, col_num, 40)
            else:
                ws_detail.set_column(col_num, col_num, 30)

        # Summary sheet
        ws_summary = writer.sheets["Summary"]
        for col_num, col_name in enumerate(summary.columns):
            ws_summary.write(0, col_num, col_name, hdr_fmt)
            if "recall" in col_name:
                ws_summary.set_column(col_num, col_num, 16, pct_fmt)
            else:
                ws_summary.set_column(col_num, col_num, 18)

    print(f"\nResults written to: {out_path}")
    print("\n=== Summary ===")
    print(summary.to_string(index=False))
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate text2sql against test.xlsx")
    parser.add_argument(
        "--test-file",
        type=Path,
        default=REPO_ROOT / "test.xlsx",
        help="Path to the test xlsx (default: <repo_root>/test.xlsx)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "evaluation",
        help="Directory to write the output Excel (default: <repo_root>/evaluation)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N questions (for quick tests)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start from this row index (0-based, for resuming)",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_evaluation(
        test_file=args.test_file,
        out_dir=args.out_dir,
        limit=args.limit,
        start=args.start,
    )


if __name__ == "__main__":
    main()
