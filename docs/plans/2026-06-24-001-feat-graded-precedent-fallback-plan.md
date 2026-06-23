---
title: "feat: Graded precedent fallback + assumptions-first drafting"
type: feat
status: completed
date: 2026-06-24
deepened: 2026-06-24
origin: docs/brainstorms/2026-06-24-graded-precedent-fallback-requirements.md
---

# feat: Graded Precedent Fallback + Assumptions-First Drafting

## Overview

Make precedent **advisory** rather than mandatory. Today the coverage gate declines
unless a similar past ticket scores `era_top_cosine >= 0.45` *and* schema scores
`>= 0.40`. After this change, a request with strong schema coverage but no precedent
produces a gated draft SQL (schema-first) instead of a coverage decline. The four
downstream safety gates (SQL validation, policy/denylist, grounding, accumulator) continue
to catch **identifier** hallucination (wrong/unfetched tables). They do **not** inspect
query semantics — and the precedent floor was implicitly anchoring semantics by supplying
a vetted precedent `sql_query` (join shape, the `status not in (...)` "active" idiom, grain
handling). The schema KB cannot supply those idioms. So in the no-precedent path the real
net for semantic correctness is the **human reviewer** (drafts are `UNVERIFIED_DRAFT`, never
executed). The `assumptions` field is what makes that net effective: it surfaces the
business-definition guesses the model made so the reviewer can check exactly the class of
error that is now unguarded. The flow stays one-shot, no interactive questions.

## Problem Frame

The coverage gate treats precedent as a precondition (`gates.py:coverage_ok`, consumed by
`decide`). This over-declines: when the schema KB clearly contains the right tables and
columns but no past ticket is similar, the agent refuses instead of drafting from
confirmed schema metadata. Precedent should be a head-start, not a gate. See origin:
`docs/brainstorms/2026-06-24-graded-precedent-fallback-requirements.md`.

## Requirements Trace

- R1. Coverage gate no longer hard-blocks on precedent; `era_top_cosine < 0.45` alone must
  not cause a decline.
- R2. Schema floor remains the bar: decline only when `schema_top_cosine < 0.40`.
- R3. Precedent is advisory — informs the draft/dialect when confident, schema-first when absent.
- R6. Confident precedent (`>= 0.45`) → dialect from top precedent's `query_engine` (unchanged).
- R7. No confident precedent → dialect from the model's self-reported `dialect`; validation runs under it.
- R8. Result gains an explicit `assumptions` field surfaced to the analyst.
- R9. Agent never asks follow-ups; picks the most reasonable interpretation, records assumptions, stays one-shot.
- R10. Schema-first drafts carry the **standard** `UNVERIFIED_DRAFT` marker; advisory precedent is conveyed by empty `precedent_ids`.
- R11. System prompt describes the schema-first path, self-report dialect, and the assumptions field.
- R12. All other gates unchanged and apply identically in the schema-first path.

## Scope Boundaries

- No interactive, multi-turn clarification — agent never blocks on a user answer.
- No relaxation of SQL-safety, policy/denylist, grounding, or accumulator gates.
- No separate "no-precedent / higher-risk" marker; standard UNVERIFIED marker only.
- No dialect inference from the schema KB (it carries none); no fixed-dialect default.
- No changes to retrieval, embeddings, or KB contents.
- **Column-level grounding is out of scope here but is a committed fast-follow** (see Risks): it is the identified gap that most directly refills the code-enforced protection the precedent floor implicitly provided, and should be planned immediately after this change ships.

## Context & Research

### Relevant Code and Patterns

- `text2sql/gates.py:coverage_ok` (lines ~220-244) — the two-floor check to relax.
- `text2sql/gates.py:decide` (lines ~266-310) — calls `coverage_ok` first; gate ordering and `reason` categories stay the same.
- `text2sql/agent.py:apply_gates` (lines ~149-210) — computes dialect via `precedent_dialect`, passes it to `gates.decide`, prepends the marker. This is where R7's effective-dialect change lands.
- `text2sql/agent.py:precedent_dialect` (lines ~122-146) — already returns `None` and logs a warning when there are no ERA calls; no change needed, just consume its `None` correctly.
- `text2sql/agent.py:Text2SQLResult` (lines ~36-48) + `parse_result` (lines ~87-119) — where the `assumptions` field is added and parsed.
- `text2sql/cli.py:format_result` (lines ~19-30) — conditional rendering pattern (`if r.precedent_ids:`) to mirror for assumptions.
- `prompts/prompts.md` → `## system_prompt` — process steps + JSON schema to extend. Loaded via `prompt_loader.load_prompt("system_prompt")`.

### Institutional Learnings

- No `docs/solutions/` directory exists yet — no prior institutional learnings to apply.

## Key Technical Decisions

- **Relax `coverage_ok`, keep `decide` ordering**: coverage stays the first gate and keeps
  the `reason="coverage"` category (now triggered only by the schema floor). Smallest blast
  radius; downstream gates and their tests are untouched.
- **Keep the `era_floor` parameter for logging, not blocking**: `coverage_ok` retains
  `era_floor` and emits an INFO line ("no confident precedent — proceeding schema-first")
  when below it, instead of returning a decline. Keeps the signature stable so existing
  callers and any telemetry continue to work.
- **Effective dialect = precedent dialect OR model self-report**: in `apply_gates`, pass
  `precedent_dialect(...) or result.dialect` into `gates.decide` so validation runs under
  the self-reported dialect when no precedent anchors it (R7). Today the model dialect is
  only used for display, not validation — this is the substantive correctness change.
- **`assumptions` as `list[str]`**: mirrors the existing `tables_used` / `columns_used`
  list fields in `Text2SQLResult` and the prompt JSON schema. Empty list when no
  interpretation choices were made.
- **Assumptions carry the semantic-review load in the no-precedent path**: since no gate
  validates query semantics and the precedent exemplar is gone, the prompt requires the
  model — when drafting schema-first — to surface its business-definition guesses (the
  meaning of "active"/status codes, time windows, aggregation grain, join keys) as
  assumptions, and to state plainly that no similar precedent was found. This converts the
  now-unguarded semantic risk into a reviewer-actionable checklist. (Per the
  architecture-strategist review: grounding/accumulator are *not* a sufficient replacement
  for what the precedent floor was doing — they cover identifier provenance, not semantics.)

## Open Questions

### Resolved During Planning

- Keep or drop `era_floor` in `coverage_ok`? → **Keep** it for the informational log threshold; it no longer blocks (R1).
- Is `assumptions` a list or string? → **`list[str]`**, consistent with sibling fields (R8).
- Any hidden precondition reading `era_top_cosine` outside `coverage_ok`? → **No** — grep confirms only `coverage_ok`/`decide` and `precedent_dialect` consume it; the latter already handles the no-precedent case.

### Deferred to Implementation

- Exact INFO log wording in `coverage_ok` for the advisory-precedent case.
- Whether `parse_result` should default a missing `assumptions` key to `[]` (likely yes, matching the `or []` pattern used for other list fields) — confirm against the model's actual output during implementation.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Coverage decision, before vs. after:

| era_cosine | schema_cosine | Before        | After                          |
|------------|---------------|---------------|--------------------------------|
| 0.80       | 0.70          | pass          | pass (precedent-backed)        |
| 0.10       | 0.70          | **decline**   | **pass** (schema-first)        |
| 0.80       | 0.30          | decline       | decline (schema floor)         |
| 0.10       | 0.30          | decline       | decline (schema floor)         |

Dialect resolution in `apply_gates` (directional):

```
effective_dialect = precedent_dialect(ctx, conn) or result.dialect   # R6 / R7
decision = gates.decide(result.sql, effective_dialect, ...)          # validate under it
result.dialect = effective_dialect                                   # show it
```

## Implementation Units

- [ ] **Unit 1: Relax the coverage gate to make precedent advisory**

**Goal:** `coverage_ok` declines only on the schema floor; weak/absent precedent no longer blocks.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `text2sql/gates.py` (`coverage_ok`; verify `decide` needs no change beyond passing signals through)
- Test: `tests/test_gates.py`

**Approach:**
- Replace the `era_top_cosine < era_floor → return False` branch with an INFO log noting schema-first fallback; keep the `era_floor` parameter.
- Keep the schema-floor branch returning `(False, "weak schema coverage …")`.
- `decide` is unchanged: coverage remains gate 1, `reason="coverage"` now reflects only the schema floor.

**Patterns to follow:** Existing `_log.info`/`_log.warning` structured-reason style already in `coverage_ok`.

**Test scenarios:**
- Happy path: `coverage_ok(0.80, 0.70)` → passes (unchanged regression guard).
- Edge case: `coverage_ok(0.10, 0.90)` → **passes** (precedent absent, schema strong) — replaces the old `test_coverage_declines_below_floor` assertion.
- Edge case: `coverage_ok(0.80, 0.30)` → declines, detail mentions schema coverage.
- Edge case: `coverage_ok(0.10, 0.30)` → declines on schema floor (not precedent).
- Integration (`decide`): `decide("SELECT 1 FROM t", "spark", era_top_cosine=0.1, schema_top_cosine=0.7, known_tables={"t"})` → `d.ok` is True (updates the now-obsolete `test_decide_declines_on_low_coverage_first`).
- Integration (`decide`): low schema (`schema_top_cosine=0.3`) → `d.reason == "coverage"`.

**Verification:** A no-precedent / strong-schema query reaches the SQL-validation gate instead of being declined at coverage; a weak-schema query still declines with `reason="coverage"`.

- [ ] **Unit 2: Validate under the effective dialect (precedent or model self-report)**

**Goal:** When no precedent exists, validate and display SQL under the model's self-reported dialect.

**Requirements:** R6, R7

**Dependencies:** Unit 1 (the no-precedent path is only reachable once coverage allows it)

**Files:**
- Modify: `text2sql/agent.py` (`apply_gates`)
- Test: `tests/test_agent.py`

**Approach:**
- Compute `effective_dialect = precedent_dialect(ctx, conn) or result.dialect`.
- Pass `effective_dialect` into `gates.decide` (currently it passes the precedent-only dialect).
- Set `result.dialect = effective_dialect` for display.
- Leave `precedent_dialect` itself unchanged — it already returns `None` + logs when no ERA call has results.

**Patterns to follow:** Existing `dialect or result.dialect` display fallback in `apply_gates`; `FakeCtx(era_id=None)` already models the no-precedent case in tests.

**Test scenarios:**
- Happy path (precedent present): `FakeCtx(era=0.8)` with a precedent that resolves to a dialect → that dialect wins (precedent-backed regression guard; may require the DB-backed `conn` fixture as existing tests do).
- Integration (no precedent): `FakeCtx(era=0.1, schema=0.7, era_id=None, retrieved={"trx_teller"})`, `result.dialect="SparkSQL"`, valid grounded SELECT → not declined, `out.dialect == "SparkSQL"`, standard marker prepended, `precedent_ids` empty.
- Edge case: no precedent **and** model self-reports no dialect (`result.dialect=None`) → validation falls back to Spark parsing (dialect-independent structural rejection still holds); a clean SELECT still passes.

**Verification:** A schema-first draft is validated under and labeled with the model's self-reported dialect; precedent-backed drafts are unaffected.

- [ ] **Unit 3: Add the `assumptions` field to the result contract and CLI**

**Goal:** Capture and display the model's interpretation assumptions.

**Requirements:** R8, R9, R10

**Dependencies:** None (independent of Units 1-2)

**Files:**
- Modify: `text2sql/agent.py` (`Text2SQLResult`, `parse_result`)
- Modify: `text2sql/cli.py` (`format_result`)
- Test: `tests/test_agent.py`, `tests/test_cli.py`

**Approach:**
- Add `assumptions: list[str] = Field(default_factory=list)` to `Text2SQLResult`.
- In `parse_result`, read `data.get("assumptions") or []` (mirror the other list fields).
- In `format_result`, add `if r.assumptions:` block rendering them as a labeled bullet list, mirroring the existing `if r.precedent_ids:` conditional.
- Include `assumptions` in the `generate_sql` APPROVED audit-log line so the reviewer's checklist is captured in the audit trail (the assumptions carry the semantic-review load — see Key Technical Decisions).

**Patterns to follow:** `tables_used` / `columns_used` field + parse pattern in `agent.py`; conditional line-append pattern in `cli.py:format_result`.

**Test scenarios:**
- Happy path: `parse_result('{"sql":"SELECT 1","assumptions":["active = status IN (2,4,8)"],"declined":false}')` → `r.assumptions == ["active = status IN (2,4,8)"]`.
- Edge case: JSON without an `assumptions` key → `r.assumptions == []` (no KeyError).
- Happy path (CLI): `format_result` with non-empty assumptions includes them in output under a clear label.
- Edge case (CLI): empty assumptions → no assumptions section rendered (mirrors precedent handling).

**Verification:** Assumptions round-trip from model JSON to CLI output; absence degrades gracefully to no section.

- [ ] **Unit 4: Update the system prompt for the schema-first path**

**Goal:** Instruct the model to draft schema-first when no precedent matches, self-report dialect, and populate assumptions.

**Requirements:** R7, R8, R9, R11

**Dependencies:** Unit 3 (the JSON schema must include `assumptions` before the prompt asks for it)

**Files:**
- Modify: `prompts/prompts.md` (`## system_prompt`)

**Approach:**
- Revise the Process steps so step 1 (precedent) is framed as "if a similar precedent exists, learn from it" and add an explicit schema-first instruction: when no similar precedent is found, confirm tables/columns via `search_schema`/`get_table_schema` and draft from those.
- Instruct: when no precedent anchors the dialect, self-report `dialect` as your best judgment.
- Add `"assumptions": ["..."]` to the required JSON keys with a one-line description; instruct the model to state interpretation choices (definitions, time windows, segments) rather than ask the user.
- **Schema-first discipline (from the safety review):** when drafting without a confident precedent, the model MUST (a) populate `assumptions` with the business-definition guesses it made (status-code/"active" meaning, time window, aggregation grain, join keys), and (b) include in `explanation` an honest note that no similar precedent was found and the business logic should be verified. This is an in-text caveat, not a new marker tier — R10 (standard `UNVERIFIED_DRAFT` marker only) is unchanged.
- Keep the "reference only confirmed identifiers" and "single SELECT" rules verbatim.

**Patterns to follow:** Existing numbered Process list and JSON-keys block in `prompts.md`.

**Test expectation:** none — prompt copy has no unit-level behavioral assertion. Validated indirectly via the Unit 3 parse tests (schema accepts `assumptions`) and the live smoke test (`tests/smoke_live_tool.py`, manual).

**Verification:** Prompt enumerates the schema-first path, the self-report-dialect rule, and the `assumptions` key; JSON key list matches `Text2SQLResult`.

## System-Wide Impact

- **Interaction graph:** `generate_sql` → `apply_gates` → `gates.decide` → `coverage_ok` is the only affected chain. No new entry points; `generate_sql` stays a single call (R9).
- **Error propagation:** Decline categories are preserved (`coverage`, `unsafe_sql`, `policy`, `grounding`). A weak-schema request still declines with `reason="coverage"`; the change only removes the precedent-triggered decline.
- **State lifecycle risks:** None — no persistence, no caching beyond the existing `_known_tables_cache` (untouched).
- **API surface parity:** `Text2SQLResult` gains an optional field with a default; existing constructors and `decline()` are unaffected. CLI is the only consumer to update.
- **Integration coverage:** The no-precedent → schema-first path is proven only when coverage relaxation (Unit 1) and effective-dialect (Unit 2) combine; the Unit 2 no-precedent integration test exercises both together.
- **Unchanged invariants:** SQL-validation, policy/denylist, grounding, accumulator, and output-scan gates are byte-for-byte unchanged and still fire in the schema-first path. The read-only DB role and `TABLE_DENYLIST` are untouched.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **Identifier** hallucination (wrong/unfetched table) in schema-first path | Grounding + accumulator gates (every referenced table must have been retrieved this session) remain mandatory and fully cover this class; the schema floor (0.40) still gates on retrieval quality. This class *is* code-enforced. |
| **Semantic** errors accepted by the gates (right tables, wrong JOIN grain / filter logic / business definition / aggregation grain) | **No gate inspects query semantics — before or after this change.** The precedent floor implicitly anchored semantics via the precedent `sql_query`; the schema KB cannot supply those idioms (it defines columns/codes, not the "active = status not in (…)" idiom). Accepted residual: the **human reviewer** is the net (drafts are `UNVERIFIED_DRAFT`, never executed). Mitigated by the schema-first assumptions discipline (Unit 4) that hands the reviewer an explicit checklist of the model's guesses. No-precedent is precisely the regime where these dominate. |
| Plausible-looking wrong SQL gets rubber-stamped by the reviewer | The Unit 4 in-`explanation` "no precedent found — verify business logic" caveat raises reviewer scrutiny exactly where the net is thinnest; `assumptions` names the specific guesses to check. |
| **Column** hallucination on a real table (e.g. `saldo_akhir` vs `saldo_rata2`) | Known gap: grounding/accumulator are **table-level only**; a wrong-but-plausible column on a confirmed table passes unless it matches a PII fragment. This asymmetry was masked by precedent and is now exposed in the no-precedent path. See Deferred / follow-up: **column-level grounding** is the cheapest mitigation that re-adds code-enforced protection (reuse `validate_sql`'s `referenced_columns` + the accumulator's fetched-column set + a set-membership check mirroring `check_grounding`). |
| Model self-reports a wrong dialect with no precedent | Safety: `validate_sql` performs dialect-independent DDL/DML rejection and falls back to Spark parsing, so a wrong dialect cannot turn an unsafe statement into a passing one. Correctness residual (accepted, human-caught): a function valid as-parsed but wrong for the real engine yields a wrong-but-runnable draft. |
| Existing coverage tests encode the old mandatory-precedent behavior | Units 1-2 explicitly update `test_coverage_declines_below_floor`, `test_decide_declines_on_low_coverage_first`, and `test_apply_gates_declines_low_coverage` to the new semantics rather than leaving them stale. |
| Audit-log readers keyed on "no confident ERA precedent" decline lines | The era branch now logs at INFO as a schema-first fallback, not a decline; note this in any log-based monitoring. |

## Documentation / Operational Notes

- Update `CLAUDE.md` Gates section (item 1, "Coverage gate") to state precedent is advisory and the schema floor is the decline bar.
- `tests/smoke_live_tool.py` (manual, needs live OIDC) is the end-to-end check that a no-precedent question now yields a draft with assumptions.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-24-graded-precedent-fallback-requirements.md](docs/brainstorms/2026-06-24-graded-precedent-fallback-requirements.md)
- Related code: `text2sql/gates.py:coverage_ok`, `text2sql/agent.py:apply_gates`, `text2sql/cli.py:format_result`, `prompts/prompts.md`
- Prior plan: `docs/plans/2026-06-19-001-feat-text2sql-agent-plan.md`
