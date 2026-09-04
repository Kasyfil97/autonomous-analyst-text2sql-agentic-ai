"""Serialize agent results into Sage API JSON (plan Unit 3).

Ports and extends ``web.result_to_payload`` for the agent surface, surfacing the additive R13a
grounding/coverage fields as structured data so the frontend never string-parses warnings.
"""
from __future__ import annotations

from text2sql.agent import MultiDraftResult, Text2SQLResult


def agent_result_to_payload(result: Text2SQLResult) -> dict:
    return {
        "kind": "sql",
        "sql": result.sql,
        "explanation": result.explanation,      # R11: rendered as the "interpretation" region
        "assumptions": result.assumptions,      # R14: prominent, editable panel
        "tables_used": result.tables_used,
        "columns_used": result.columns_used,
        "precedent_ids": result.precedent_ids,
        "dialect": result.dialect,
        "declined": result.declined,
        "missing": result.missing,
        "warnings": result.warnings,
        # R13a / R15 — structured grounding + coarse strength (never a numeric confidence).
        "grounding": result.grounding,
        "grounding_strength": result.grounding_strength,
        "era_top_cosine": result.era_top_cosine,
        "schema_top_cosine": result.schema_top_cosine,
    }


def multidraft_result_to_payload(result: MultiDraftResult) -> dict:
    """Serialize a decomposed multi-part answer (rec #1). Each sub-draft reuses the
    single-draft payload so the frontend can render it with the existing card."""
    return {
        "kind": "sql_multi",
        "question": result.question,
        "sub_drafts": [
            {"sub_need": sd.sub_need, "result": agent_result_to_payload(sd.result)}
            for sd in result.sub_drafts
        ],
        "reconciliation": result.reconciliation,
        "combined_sql": result.combined_sql,
        "warnings": result.warnings,
        "declined": result.declined,
    }


def result_to_payload(result) -> dict:
    """Dispatch any agent result (single or multi-draft) to its JSON payload."""
    if isinstance(result, MultiDraftResult):
        return multidraft_result_to_payload(result)
    return agent_result_to_payload(result)
