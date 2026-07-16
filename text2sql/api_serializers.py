"""Serialize agent results into Sage API JSON (plan Unit 3).

Ports and extends ``web.result_to_payload`` for the agent surface, surfacing the additive R13a
grounding/coverage fields as structured data so the frontend never string-parses warnings.
"""
from __future__ import annotations

from text2sql.agent import Text2SQLResult


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
