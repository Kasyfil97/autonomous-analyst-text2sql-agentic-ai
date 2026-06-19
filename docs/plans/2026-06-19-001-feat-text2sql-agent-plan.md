---
title: "feat: Text-to-SQL Agent (Strands + gpt-oss-120b on Bedrock)"
type: feat
status: active
date: 2026-06-19
deepened: 2026-06-19
origin: docs/brainstorms/2026-06-19-text2sql-agent-requirements.md
---

# feat: Text-to-SQL Agent (Strands + gpt-oss-120b on Bedrock)

## Overview

Build a CLI agent that turns a natural-language question into a **draft SQL query**
(no execution), grounded in two existing Postgres knowledge bases — ERA precedents
(`era_knowledge`) and the schema catalog (`schema_tables`/`schema_columns`) — using
hybrid (dense + sparse RRF) retrieval. The agent runs an agentic tool-calling loop on
the **Strands Agents SDK**, driving **gpt-oss-120b on Bedrock** through the existing
federated OIDC session in `bedrock_session.py`. Output is SQL + reasoning + cited
sources + chosen dialect; the agent declines (code-enforced) when the KBs don't
confidently cover the question.

## Problem Frame

Analysts hand-write SQL for ad-hoc ERA data requests, needing to know tables, columns,
coded values, and the idioms used in similar past tickets. That knowledge already lives
in two retrieval KBs (see origin + `RETRIEVAL.md`). The agent gives a verifiable
starting point instead of writing from scratch. No execution — the human reviews and
runs the draft. (See origin: `docs/brainstorms/2026-06-19-text2sql-agent-requirements.md`.)

## Requirements Trace

Carried from the origin requirements document:

- **R1.** NL question (ID/EN) → draft SQL, no execution.
- **R2.** Agentic tool-calling loop; model chooses tools, may call repeatedly.
- **R3.** Infer dialect from the closest ERA precedent's `query_engine` (SparkSQL/SQLServer); state it. _(Reviewed/confirmed: no user override in v1.)_
- **R4.** Strict binary decline on low/no confident coverage; name what's missing; no guessed SQL. _(Reviewed/confirmed: no graded output in v1.)_
- **R5.** ERA-knowledge tool returns per hit: precedent SQL, tables/columns, `key_filters`, request type, `query_engine`, analyst notes.
- **R6.** Schema tools: discover candidate tables/columns for a concept; deterministically fetch the column dictionary for a known table. (Granularity resolved here → 3 tools, KD8.)
- **R7.** Hybrid dense+sparse RRF retrieval per `RETRIEVAL.md`.
- **R8.** Drive the model exclusively through `bedrock_session.py`'s federated session.
- **R9.** Use `embedding_service.py` (`embed_one`/`embed`) + `pg_config()`.
- **R10.** Read-only KB access enforced at the DB-privilege level; no runtime access to `era_tickets`.
- **R11.** Output (SQL path): SQL + explanation + tables/columns used + ERA ticket id(s) + dialect. Decline path: missing-knowledge statement only.
- **R12.** Treat retrieved KB content as untrusted data; constrain output to read-only `SELECT`; no-execution is a design guarantee.
- **R13.** Explicit external-Bedrock trust boundary; data-classification audit; no secrets/ARNs logged at default verbosity.

## Scope Boundaries

- No SQL execution, no live DB introspection, no result rendering.
- No write path to any KB table; no runtime access to `era_tickets`.
- No new ingestion/embedding pipeline — consume `era_knowledge` / `schema_*` as-is.
- Single-question CLI flow only; no HTTP API, auth, multi-user, or persistence in v1.
- No automatic query correctness/validation beyond KB grounding + static SELECT/identifier checks.
- Out of scope for v1 (recorded as future): SQL-skeleton-similarity precedent rerank, self-consistency multi-candidate voting, user-specified dialect override, graded/caveated output.

## Context & Research

### Relevant Code and Patterns

- `bedrock_session.py` — `BedrockSession` builds a federated `boto3.Session` (OIDC role-chain) and invokes the model via `_invoke` → `_invoke_standard` (OpenAI-style `invoke_model` body: `{"messages":[...],"max_tokens","temperature"}`, parses `choices[0].message.content`) with a `_invoke_mantle` (OpenAI-compatible, SigV4-signed) fallback. `refresh_if_needed()` re-auths near expiry. `_strip_reasoning()` removes `<reasoning>…</reasoning>`. **The custom model provider reuses this file's session + invoke path; it does not use Converse.**
- `embedding_service.py` — `embed_one`/`embed` (BGE-M3 dense, 1024-dim) + `pg_config()` (psycopg2 kwargs). Sparse is computed locally as BM25.
- `RETRIEVAL.md` — authoritative hybrid retrieval recipes (dense cosine, sparse BM25 `sparsevec` encode via persisted `*_bm25`/`*_bm25_meta` vocab, RRF fusion), per-KB sparse dims (`era_knowledge` 5810, `schema_tables` 684, `schema_columns` 698), and the recommended ERA→schema agent flow. Verified live: tables present with expected row counts.

### Institutional Learnings

- `docs/solutions/` does not exist yet — no prior institutional learnings to apply.

### External References

Strands SDK + gpt-oss-on-Bedrock (framework-docs research):
- `BedrockModel(boto_session=…, region_name=…, model_id=…)` accepts a caller session — confirmed. Docs: https://strandsagents.com/docs/api/python/strands.models.bedrock/ and …/model-providers/amazon-bedrock/.
- **gpt-oss-120b breaks the default tool loop:** no ConverseStream (use `streaming=False`), and returns `stopReason: end_turn` instead of `tool_use`, so tools never execute. Issues: [#630](https://github.com/strands-agents/sdk-python/issues/630), [#644](https://github.com/strands-agents/sdk-python/issues/644), [#910](https://github.com/strands-agents/sdk-python/issues/910), [tools #223](https://github.com/strands-agents/tools/issues/223).
- **Custom Model provider** (`strands.models.Model` subclass with async `stream(...)` yielding `messageStart→contentBlockStart→…→messageStop(stopReason)→metadata`) lets us emit `stopReason:"tool_use"` from OpenAI `tool_calls`. Docs: …/model-providers/custom_model_provider/. gpt-oss model card (OpenAI-style `invoke_model` body, `ap-southeast-3`): https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-120b.html.
- `@tool` decorator (docstring = tool description to the LLM), `agent.structured_output(PydanticModel, …)`, `SlidingWindowConversationManager` (preserves toolUse/toolResult pairing). Strands holds the client built from our session → credential refresh is our responsibility.

Text-to-SQL RAG best practices (best-practices research):
- **Decline must be code-enforced**, not prompt-only (prompt refusal fails mid-decode): retrieval-coverage gate + deterministic schema-grounding validation (every identifier exists in catalog) + SELECT-only. ([Adaptive Abstention, SIGMOD 2025](https://arxiv.org/pdf/2501.10858), [Confidence Estimation](https://arxiv.org/pdf/2508.14056), [OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).
- **Inject schema as DDL + inline `--` comments**, precedents as SQL + a one-line table/filter summary (DAIL-SQL). Ground against the **full** schema catalog, never the 200-row sample data.
- **Prompt-injection:** fence retrieved notes/SQL as untrusted data; enforce SELECT-only with `sqlglot`/`sqlparse` in code; adversarially test a poisoned analyst note. (OWASP LLM01:2025.)
- **Strip gpt-oss `analysis`/reasoning channel**; surface only the final answer + validated citations. ([handle raw CoT](https://cookbook.openai.com/articles/gpt-oss/handle-raw-cot)).
- **Evaluation (no execution):** static schema-validity/grounding is the primary signal; analyst-acceptance + edit-distance is the product metric; exec-accuracy on a sample is unreliable.

## Key Technical Decisions

- **KD1 — Custom Strands `Model` provider is the PRIMARY path (not a fallback).** Research confirms the default Converse tool loop is broken for gpt-oss-120b. A `Model` subclass wraps the existing OpenAI-style `invoke_model` (via the federated session), translating Strands tool specs → OpenAI `tools`, and OpenAI `tool_calls` → Strands `toolUse` content blocks with `stopReason:"tool_use"`. This resolves the origin's #1 deferred risk. (R2, R8)
- **KD2 — Add one reusable invoke seam on `BedrockSession`; do not reuse `_invoke_standard`.** The existing `_invoke_standard`/`_invoke_mantle`/`_strip_reasoning` are **module-level functions**: `_invoke_standard` hardcodes `max_tokens=512`/`temperature=0.7`, carries **no `tools`** field, returns only `choices[0].message.content` (dropping `tool_calls`), and `_invoke` auto-falls back to **Mantle** (bearer-token path, which would violate R8). So Unit 1/2 adds a single new method `BedrockSession.invoke(messages, tools=None, max_tokens, temperature, *, allow_mantle=False)` that builds the OpenAI body **with** `tools`, returns the **raw `choices[0].message`** (incl. `tool_calls`), and **raises** on error (no silent `None`/Mantle). The custom provider calls only this method, always rebuilding the `bedrock-runtime` client from `bedrock_session.session` per call (after `refresh_if_needed()`, which rebuilds the session). (R8)
- **KD3 — Code-enforced decline & safety gates, fail-closed.** A deterministic gate runs after generation and is **authoritative over the model**; `decide()` consumes only structured gate outputs, never model free-text claims of safety. Gates: (a) **per-KB coverage** — the **ERA-precedent** top-hit dense cosine (max over the era_knowledge call(s) whose results were cited) AND the **schema-table** top-hit cosine must each clear independent configured floors; a strong precedent with no cataloged target table still declines. If no ERA precedent clears its floor → decline (no dialect can be inferred either). `get_table_schema` (deterministic) contributes no coverage score. (b) `sqlglot` parsed with the **target dialect**, walking the **full AST** — reject anything that isn't exactly one read-only `SELECT` (no DDL/DML anywhere, incl. data-modifying CTEs / `MERGE` / `SELECT…INTO` / `Command`/`Set` nodes; `len(parse(sql)) == 1`); **functions are fail-closed** — any function not on a per-dialect read-safe allowlist (incl. any `Anonymous`/unknown UDF node) declines, with the dangerous-name denylist (`pg_read_file`/`dblink`/`xp_cmdshell`/`pg_sleep`/`lo_import`) only a fast-path; any `ParseError`/unknown node → decline; (c) schema-grounding — every referenced identifier exists in the schema KB (full catalog); (d) **policy/sensitivity** — decline if any referenced identifier is classified restricted (PII/PCI) or is on the table denylist (`era_tickets`), independent of grounding. Unclassified columns default to restricted (fail-closed). Not a prompt instruction. (R4, R10, R12, R13)
- **KD4 — Shared hybrid-retrieval helper, parameterized per KB.** One module implements dense (`embed_one`) + sparse BM25 encode (loads `{vocab, idf, dim}` from each KB's `*_bm25`/`*_bm25_meta`) + RRF fusion, per `RETRIEVAL.md`. Never hardcodes the sparse dim. (R7, R9)
- **KD5 — Grounding injection format.** Schema rendered as `CREATE TABLE`-style DDL + inline column comments/coded values; precedents rendered as SQL + a one-line tables/`key_filters` summary; all retrieved text fenced as untrusted. (R5, R6, R11, R12)
- **KD6 — Typed final answer + reasoning stripped.** Final answer is a Pydantic model (`sql`, `explanation`, `tables_used`, `columns_used`, `precedent_ids`, `dialect`, `declined`, `missing`) via `agent.structured_output`; gpt-oss reasoning channel stripped. **On decline:** `declined=True`, `missing` set; `sql`/`explanation`/`tables_used`/`columns_used`/`precedent_ids`/`dialect` are null. **On success:** all fields populated, `declined=False`, `missing` null. Dialect is sourced from the cited top precedent's `query_engine`; if no precedent cleared coverage there is no SQL path (decline), so dialect is never undefined-on-success. (R11)
- **KD7 — Multi-turn via `SlidingWindowConversationManager`** to preserve toolUse/toolResult pairing Bedrock requires. (R2)
- **KD8 — Three tools** (`@tool`): `search_era_knowledge`, `search_schema` (tables+columns concept search), `get_table_schema` (deterministic dictionary). Resolves origin's tool-granularity deferred question. (R5, R6)
- **KD9 — Read-only enforced at DB level.** A dedicated `SELECT`-only Postgres role (not `postgres` superuser), additionally lacking `CREATE TEMP` and execute on dangerous functions, used by the tools; documented as setup. (R10)
- **KD10 — Pre-context PII redaction.** Retrieved analyst notes / precedent SQL are passed through a redaction pass (mask literal PAN/SSN/email and long contiguous digit runs **outside** SQL numeric-literal/identifier contexts) **before** they enter the model context — because data leaves to external Bedrock the moment it's in the prompt, upstream of the gates. Must **not** corrupt SQL identifiers, coded-value literals (e.g. `status not in (2,4,8)`), or `key_filters` tokens; prefer redacting `analyst_notes` free-text over precedent SQL bodies. Depends on the R13 classification (fail-closed default). (R12, R13)
- **KD-scope — Full data-protection layer kept in v1 (reviewed).** Policy/sensitivity gate + PII redaction + output-scan all ship in v1 (fail-closed on the sample), chosen over a leaner v1 because the data is banking data; redaction/policy fidelity improves once R13 classification lands but the controls exist from day one.
- **KD11 — No-execution as a positive structural invariant.** The generation path contains no DB driver cursor/`execute` capability; the only DB access is the read-only catalog/retrieval connection in a clearly-separated component. A CI/static check fails if `execute(`/`cursor` appears in the generation modules. Emitted SQL carries a visible `UNVERIFIED — DRAFT` marker, and the output destructive-pattern ban (KD3) is applied to *any* SQL-looking block in the answer/explanation, not just the primary draft. (R1, R12)

## Open Questions

### Resolved During Planning

- **Does gpt-oss-120b support Converse tool-use (origin #1 risk)?** → **No, not end-to-end through Strands.** Adopt the custom Model provider (KD1). Verified via Strands issues + model card.
- **BM25 per-KB vocab loading?** → Helper loads `{vocab, idf, dim}` per KB from `*_bm25`/`*_bm25_meta` (KD4).
- **Tool granularity (origin deferred)?** → 3 tools (KD8).
- **What signals the decline (origin deferred)?** → Code gate: coverage threshold + sqlglot SELECT-only + schema-grounding on the full catalog (KD3). RRF scores are never zero and dense always returns `LIMIT` rows, so coverage is judged on raw top-hit dense cosine / BM25 magnitude, not result presence.

### Deferred to Implementation

- **Exact threshold numbers** (dense-cosine floor, RRF/rank cutoff for coverage) — tune empirically on the full catalog; placeholders in code with a single config point.
- **`sqlglot` dialect coverage** for SparkSQL vs SQLServer (T-SQL) parsing/validation edge cases — confirm during implementation; degrade to SELECT-only + identifier checks if a dialect won't parse cleanly.
- **Whether Strands issue #644 (content-based tool detection) has merged** in the pinned SDK version — doesn't change the plan (we use the custom provider), but verify so we're not fighting double-handling.
- **`sqlglot` parser-vs-engine differentials** — confirm during implementation which SparkSQL/SQLServer statements `sqlglot` misparses; fail-closed covers the unknowns but the denylist may need tuning.
- **R13 data-classification is a PRODUCTION PREREQUISITE, not just deferred** — the `policy_ok` gate (KD3) and PII redaction (KD10) depend on per-column sensitivity labels. v1 dev/demo runs with a fail-closed default (unclassified → restricted) on the sample; **going to production is blocked until the 4233-row `era_knowledge` + schema columns are classified** and Bedrock prompt-logging posture is confirmed.

Resolved by deepening (previously deferred): credential refresh is now a U2 requirement (rebuild client per call + catch-expiry-retry / `RefreshableCredentials`); the Mantle fallback is **off by default** (`allow_mantle=False`) since it would cross the R8 trust boundary.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**Component / data flow:**

```mermaid
flowchart TB
    CLI[cli.py chat loop] --> AG[agent.py: Strands Agent]
    AG -->|system prompt + tools| MP[bedrock_model.py<br/>custom Model provider]
    MP -->|OpenAI-style invoke_model<br/>tools/tool_calls| BS[bedrock_session.py<br/>federated session]
    AG -->|tool calls| TOOLS[tools.py @tool x3]
    TOOLS --> RET[retrieval.py<br/>dense+sparse+RRF]
    RET --> EMB[embedding_service.py embed_one]
    RET --> PG[(Postgres KB<br/>read-only role)]
    TOOLS --> PG
    AG -->|final answer| GATE[gates.py<br/>SELECT-only + grounding + coverage]
    GATE -->|pass| OUT[Structured result:<br/>SQL+reasoning+sources+dialect]
    GATE -->|fail| DECL[Decline: missing knowledge]
```

**Custom provider translation (the load-bearing adapter, KD1) — both directions:**
Strands calls `stream(messages, tool_specs, system_prompt)`.
- **Forward (per call):** map `tool_specs` → OpenAI `tools`; call `BedrockSession.invoke(...)` (KD2); read `choices[0].message`. If it has `tool_calls`, yield `toolUse` content blocks (minting a `tool_call_id`) and `messageStop(stopReason="tool_use")`; else yield text + `stopReason="end_turn"`.
- **Reverse (every turn — the most failure-prone part):** translate the incoming Strands history (`toolUse`/`toolResult` content blocks) back into an OpenAI `assistant` message carrying its `tool_calls` array **plus** `{"role":"tool","tool_call_id":…}` result messages, with ids paired exactly. A half-evicted pair (conversation-manager window dropped one side) must be detected/repaired here, not left to fail at Bedrock.
- **Structured output:** `agent.structured_output()` is itself a *forced* tool call, so it hits the same defect — the provider must honor OpenAI `tool_choice`/forced-tool and surface the forced call as a `toolUse` block. U6 carries a JSON-from-final-text fallback if forcing can't be guaranteed.

This adapter is the one piece that makes gpt-oss's tool loop function; a **live single-tool smoke test (U2.5)** must prove it with the real model before later units build on it.

## Implementation Units

```mermaid
flowchart TB
    U1[U1 Setup: deps + RO role + invoke seam] --> U2[U2 Custom Model provider]
    U2 --> U25[U2.5 LIVE single-tool smoke gate]
    U1 --> U3[U3 Hybrid retrieval helper]
    U25 --> U4[U4 Knowledge tools + PII redaction]
    U3 --> U4
    U25 --> U5[U5 Safety + decline + policy gates]
    U3 --> U5
    U2 --> U6[U6 Agent assembly + structured output]
    U25 --> U6
    U4 --> U6
    U5 --> U6
    U6 --> U7[U7 CLI]
```

> **U2.5 is a hard gate:** Units 3–7 assume the custom provider makes real gpt-oss-120b fire tools end-to-end. U2's tests use a *stubbed* session (proving translation only). U2.5 proves it with the **real** model + a trivial `@tool`; if it fails, switch to the prompted-ReAct fallback or a tool-capable Bedrock model (Nova/Claude) behind the same provider seam **before** building U4–U7.

- [ ] **Unit 1: Environment, dependencies, and read-only DB role**

**Goal:** Make the project runnable and enforce R10/R13 prerequisites.

**Requirements:** R9, R10, R13

**Dependencies:** None

**Files:**
- Create: `requirements.txt` (add `strands-agents`, `strands-agents-tools`, `psycopg2-binary`, `sqlglot`; keep existing `boto3`, `requests`, `python-dotenv`)
- Create: `.gitignore` (ensure `.env`, `.venv/`, `__pycache__/`, `.key_filters_cache.json`)
- Create: `docs/setup-readonly-role.md` (SQL to create a `t2s_ro` role with `SELECT` on `era_knowledge`, `era_knowledge_bm25(_meta)`, `schema_*`, **no** access to `era_tickets`, **no** `CREATE TEMP`, and no execute on dangerous functions; add `PG_RO_USER`/`PG_RO_PASSWORD` env vars)
- Modify: `embedding_service.py` (`pg_config()` reads the read-only role when present — falls back to current behavior for build scripts)
- Modify: `bedrock_session.py` (add the reusable `invoke(messages, tools=None, max_tokens, temperature, *, allow_mantle=False)` method per KD2 — builds the OpenAI body with `tools`, returns the raw `choices[0].message` incl. `tool_calls`, **raises** on error; existing module functions/chat untouched)
- Test: `tests/test_bedrock_session_invoke.py`

**Approach:**
- Pin versions after a quick install check that `strands-agents` coexists with `boto3 1.43.x`.
- `pg_config()` gains an optional read-only variant; agent code uses it exclusively (KD9). Build/ingest scripts keep the existing superuser path.
- The new `invoke()` method is the single seam the provider depends on (KD2); it does **not** reuse `_invoke_standard` (which drops `tool_calls` and caps at 512). Mantle stays off unless `allow_mantle=True`.

**Patterns to follow:** existing `pg_config()` env-var pattern and `_invoke_standard` body/parse shape (as reference, not reuse) in `embedding_service.py` / `bedrock_session.py`.

**Test scenarios:**
- Happy path: `pg_config(readonly=True)` returns `t2s_ro` creds when env set; falls back to defaults when unset.
- Happy path: `invoke()` returns the full `message` object including `tool_calls` (stubbed runtime), with caller-supplied `max_tokens`/`temperature` (not 512/0.7).
- Error path: `invoke()` **raises** on AccessDenied/Throttling/empty `choices` (does not return `None`, does not silently call Mantle).
- Integration: connecting as `t2s_ro` can `SELECT` from `era_knowledge` and **cannot** `SELECT`/write `era_tickets` (raises insufficient_privilege).
- Edge case: missing `PG_RO_*` env → clear error, not a silent superuser connection in agent context.

**Verification:** `pip install -r requirements.txt` succeeds in `.venv`; existing `bedrock_session.py` chat still runs; `invoke()` carries tools and raises on failure; `t2s_ro` privilege boundary holds.

- [ ] **Unit 2: Custom Strands Model provider over the federated session**

**Goal:** A `strands.models.Model` subclass that drives gpt-oss-120b through `bedrock_session.py`'s OpenAI-style `invoke_model`, making tool calls actually fire (KD1, KD2).

**Requirements:** R2, R8

**Dependencies:** Unit 1

**Files:**
- Create: `text2sql/__init__.py`
- Create: `text2sql/bedrock_model.py`
- Test: `tests/test_bedrock_model.py`

**Approach:**
- Subclass `Model`; implement async `stream(messages, tool_specs=None, system_prompt=None, **kwargs)` plus `get_config`/`update_config`. Use `asyncio.to_thread` for the blocking `BedrockSession.invoke()` call; call `refresh_if_needed()` **before** submitting (it rebuilds `self.session`), and build the runtime client from `bedrock_session.session` **every call** — never cache it across turns.
- Set the provider's own `max_tokens` (2–4k) and low `temperature` (~0–0.2 for deterministic SQL), not the legacy 512/0.7.
- **Forward translation:** `tool_specs` → OpenAI `tools`; response `tool_calls` → `toolUse` blocks (minted `tool_call_id`, name, input JSON) with `stopReason="tool_use"`; else text + `end_turn`. Emit `messageStart → contentBlockStart → deltas → contentBlockStop → messageStop(stopReason) → metadata`.
- **Reverse translation (every turn):** Strands `toolUse`/`toolResult` history blocks → OpenAI `assistant.tool_calls` + `{"role":"tool","tool_call_id":…}` messages, ids paired. **Repair rule:** drop *both* sides of any unpaired (eviction-boundary) tool block — **never synthesize** a fabricated tool_call/result (that would inject model-visible context). (KD7's manager preserves pairing only *within* the window; orphans at the window edge are discarded here.)
- **Forced-tool / structured output:** honor OpenAI `tool_choice`; surface a forced final tool as a `toolUse` block so `agent.structured_output` works.
- **Credential expiry mid-turn:** catch an expired-credential error inside `stream`, call `setup()`, retry once (or use botocore `RefreshableCredentials`).
- **Reasoning:** confirm the *actual* gpt-oss CoT delimiter from this body (`<reasoning>` vs Harmony `analysis` channel) and strip it before emitting text/structured output **and** before logging.

**Execution note:** Start with a failing unit test that feeds a fake `tool_calls` invoke response and asserts a `tool_use` stopReason event (the defect we're fixing).

**Technical design:** *(directional)* see "Custom provider translation — both directions" in High-Level Technical Design.

**Patterns to follow:** `BedrockSession.invoke` (U1); `_strip_reasoning` (verify/extend for the real delimiter).

**Test scenarios:**
- Happy path (text): plain assistant message → ordered events ending in `messageStop`/`end_turn` with text.
- Happy path (tool): response with `tool_calls` → `toolUse` block(s) + `stopReason="tool_use"`. *(Core regression fixed.)*
- Happy path (forced tool): a forced single-tool (structured-output) invoke → a `tool_use` event for that tool.
- Integration (reverse): a Strands history with a prior `toolUse`+`toolResult` is re-serialized to OpenAI `assistant.tool_calls` + `tool` messages with matching ids; a half-evicted pair is repaired, not sent dangling.
- Error path: `invoke()` raises (AccessDenied/Throttle/expiry) → clean error event, not a swallowed `None`; expired-credential triggers one `setup()`+retry.
- Edge case: after a simulated `refresh_if_needed()`/`setup()`, the next invoke uses the new session's credentials (client not cached).
- Edge case: reasoning channel present → stripped before emission and not logged.

**Verification:** With a stubbed `BedrockSession`, a Strands `Agent` using this provider executes a trivial `@tool` and a forced structured-output tool end-to-end (translation proven). Real-model proof is U2.5.

- [ ] **Unit 2.5: Live single-tool smoke gate**

**Goal:** Prove the custom provider makes **real gpt-oss-120b** fire a tool end-to-end through Strands before building dependent units (resolves the architecture's costliest assumption).

**Requirements:** R2, R8

**Dependencies:** Unit 2

**Files:**
- Create: `tests/smoke_live_tool.py` (manual/marked live test; needs real federated creds + Bedrock)

**Approach:**
- Real `BedrockSession`, real model, one trivial `@tool` (e.g. `echo(text)`); assert the tool actually executes and a final answer returns.
- **Pin the real wire shapes** (the repo has never exercised a `tool_calls` response): capture and document the exact gpt-oss-120b `invoke_model` response — where `tool_calls` sits in `choices[0].message`, the `id`/`function.name`/`arguments` (string vs object) shape, and the **actual reasoning/CoT delimiter** (`<reasoning>` vs Harmony `analysis` channel). Feed these back to correct U2's translation + `_strip_reasoning`.
- **Classify the forced-tool (structured-output) path explicitly PASS/FAIL.** On FAIL, designate the JSON-from-(reasoning-stripped)-text path as the **primary** structured-output mechanism (strict Pydantic-validate-or-decline), not a fallback.
- If tool-calling itself fails: switch the provider to prompted-ReAct, or temporarily point at a tool-capable Bedrock model (Nova Pro/Claude) behind the same seam, and record the decision (and its effort) before proceeding.

**Execution note:** This is a gate, not just a test — Units 4–7 depend on its outcome.

**Test scenarios:**
- Live happy path: question that should call `echo` → tool fires, result threads back, final answer returned.
- Live forced-tool: structured-output request returns a validated object → records PASS; otherwise records FAIL and the primary-path switch.

**Verification:** Documented PASS (gpt-oss fires tools + forced tool via the provider) or recorded fallback decisions, **plus** the captured wire shapes. Do not start U4–U7 until this resolves.

- [ ] **Unit 3: Hybrid retrieval helper (dense + sparse + RRF)**

**Goal:** One reusable function to retrieve ranked rows from any KB table using the `RETRIEVAL.md` hybrid recipe (KD4).

**Requirements:** R7, R9

**Dependencies:** Unit 1

**Files:**
- Create: `text2sql/retrieval.py`
- Test: `tests/test_retrieval.py`

**Approach:**
- `hybrid_search(kb, question, limit, prefilter=None)` where `kb` names the table + its `*_bm25`/`*_bm25_meta`. Embed dense via `embed_one`; encode sparse via the persisted vocab/idf/dim (cache per KB; never hardcode dim — 5810/684/698 differ); run the RRF SQL from `RETRIEVAL.md`; return rows with scores + raw top-hit dense cosine (for the coverage gate).
- Reuse the exact `tokenize`/`encode_query_sparse` logic and RRF query documented in `RETRIEVAL.md`. Use the read-only connection.

**Patterns to follow:** `RETRIEVAL.md` Query recipes §2/§3; `embedding_service.embed_one`.

**Test scenarios:**
- Integration (live KB): the two verified queries from `RETRIEVAL.md` ("Ciamis absensi" → ERA26-241; "TL506 report" → ERA25-1685) rank the correct id #1 via hybrid.
- Integration: `hybrid_search('schema_tables', "rekening simpanan harian saldo nasabah")` returns `0000_staging_raw_as4_ddmast` in the top results.
- Edge case: question with no in-vocab tokens → sparse encodes to `{1:0}/dim` (valid literal), dense still returns rows, no crash.
- Edge case: per-KB dim is read from `*_bm25_meta` and equals the live table's sparse-column dimension (consistency check), **not** asserted against the literal 684/698/5810 — those are sample-export values and change on full-catalog re-ingest (which DROPs+recreates the tables); `*_bm25_meta` is the single source of truth.
- Error path: embedding endpoint unreachable → raises a clear retrieval error (surfaced later as a decline, not a stack trace).

**Verification:** Returns correctly-ranked rows with scores for all three KBs against the live Postgres.

- [ ] **Unit 4: Knowledge tools (3 × `@tool`) + pre-context PII redaction**

**Goal:** Expose ERA + schema knowledge to the agent loop (KD5, KD8), redacting literal PII before it reaches the model (KD10).

**Requirements:** R5, R6, R11, R12, R13

**Dependencies:** Unit 3, Unit 2.5

**Files:**
- Create: `text2sql/tools.py`
- Test: `tests/test_tools.py`

**Approach:**
- `search_era_knowledge(question)` → top precedents rendered as: ticket id, request type, `query_engine`, tables/`key_filters` one-line summary, analyst notes (fenced as untrusted), and precedent SQL (fenced). Carries dense top-hit score for the gate.
- `search_schema(concept)` → candidate tables (DDL-style: name + columns w/ types + inline comment/coded values) and/or columns, via hybrid over `schema_tables`/`schema_columns`.
- `get_table_schema(table_name)` → deterministic full column dictionary (`SELECT field_name, business_title, description, data_type FROM schema_columns WHERE table_name=… ORDER BY field_name`). Read-only connection. **Sample caveat:** on the 200-row export, columns whose parent table is absent carry `tid<N>` ids and may lack a clean `table_name`, so deterministic lookup can return empty for exactly the tables an analyst hits — verify real rows have stable `table_name` during implementation; this is a v1-on-sample limitation that the full catalog resolves.
- Docstrings written for the model (they become tool descriptions). All retrieved free-text wrapped in explicit `<untrusted>` fences (R12).
- **Redaction (KD10):** before returning, mask literal PAN/SSN/account-number/email patterns in analyst notes and precedent SQL so they never reach the model context. Tools also record their top-hit dense-cosine/RRF scores and retrieved identifiers into a per-request retrieval accumulator that U6 hands to the gates (so gates check what was *retrieved*, not what the model *claims*).

**Patterns to follow:** Unit 3 helper; `RETRIEVAL.md` "get its whole dictionary deterministically" query; the ERA→schema recommended flow.

**Test scenarios:**
- Happy path: `search_era_knowledge` returns precedent SQL + tables + `key_filters` + `query_engine` for a known case; output fences analyst notes/SQL.
- Happy path: `get_table_schema('0000_staging_as4_lnflag')` returns its 44 columns with types deterministically (order stable).
- Edge case: `get_table_schema` on an unknown/placeholder `tid<N>` table → empty/explicit "not found", not an error.
- Integration: schema rendered as DDL+comments (assert format), since injection format affects fidelity (KD5).
- Error path: retrieval failure inside a tool returns a structured "retrieval unavailable" payload the agent can act on.
- Security: a precedent row seeded with a literal fake PAN/SSN → the tool's model-bound payload contains the masked form, not the literal (KD10).
- Security (non-corruption): redaction over a precedent SQL containing coded-value literals (e.g. `status not in (2,4,8)`) and numeric column tokens preserves those identifiers/literals unchanged (KD10).

**Verification:** Each tool callable standalone returns correctly-shaped, fenced payloads against the live KB.

- [ ] **Unit 5: Safety and decline gates**

**Goal:** Code-enforced, fail-closed SELECT-only + schema-grounding + coverage + policy decline (KD3, KD11).

**Requirements:** R4, R10, R12, R13

**Dependencies:** Unit 3

**Files:**
- Create: `text2sql/gates.py`
- Test: `tests/test_gates.py`

**Approach:**
- `validate_sql(sql, target_dialect)` — parse with `sqlglot` using the **target dialect**; require `len(parse(sql)) == 1`; **walk the full AST** and reject if any node is DDL/DML (`Insert/Update/Delete/Merge/Create/Drop/Alter/Command/Set`), a data-modifying CTE, or a `Select` carrying `Into`; **functions fail-closed** — decline on any function not on a per-dialect read-safe allowlist (incl. `Anonymous`/unknown UDF nodes); dangerous-name denylist (`pg_read_file`, `dblink`, `xp_cmdshell`, `pg_sleep`, `lo_import`) is only a fast-path; any `ParseError`/unknown node → **decline (fail-closed)**. Return referenced tables/columns.
- `check_grounding(referenced, retrieved_identifiers)` — every referenced identifier must exist in the schema KB (full catalog); list misses. Catalog source is a single config point; on the 200-row sample run in **warn mode** (expected to over-decline). Happy-path grounding tests use a small **full-catalog fixture** (seeded known-good identifiers), not the live sample.
- `policy_ok(referenced)` — decline if any referenced identifier is classified restricted (PII/PCI) or on the table denylist (`era_tickets`); unclassified → restricted (fail-closed) (KD3/R13).
- `coverage_ok(era_top_cosine, schema_top_cosine, top_rrf)` — both the cited ERA precedent floor **and** the schema-table floor must clear (per-KB independent thresholds); no ERA precedent above floor → decline (no dialect inferable). Single config point; tuned on full catalog.
- `scan_output(text)` — apply the same AST denylist to **any** SQL-looking block in the answer/explanation (catch a destructive snippet outside the primary draft); attach the `UNVERIFIED — DRAFT` marker (KD11).
- `decide(...)` — compose into pass / decline-with-reason; reasons distinguish *unsafe SQL* / *grounding miss* / *coverage miss* / *policy*. Consumes **only** structured gate outputs, never model free-text claims.

**Execution note:** Implement test-first — these are the trust-critical, fail-closed invariants.

**Test scenarios:**
- Happy path: a valid single SELECT referencing known, non-restricted tables/columns → passes; returns referenced identifiers + UNVERIFIED marker.
- Error path (safety, full-AST): `DROP/UPDATE/DELETE/INSERT/MERGE`, `; DROP …` stacked, `WITH x AS (DELETE … RETURNING *) SELECT …`, `SELECT … INTO copy …`, `SELECT pg_read_file(…)`, `SELECT 1 /*;*/ ; SELECT 2` → all rejected.
- Error path (parser differential): a SparkSQL `CACHE TABLE`/`MSCK REPAIR` or SQLServer `EXEC` → decline (fail-closed), not silent pass.
- Error path (grounding): SELECT referencing a hallucinated column → decline naming the unknown identifier.
- Security (policy): valid grounded SELECT referencing an `ssn`/PAN column, or any `era_tickets` reference → decline with reason "policy" even though SQL is valid.
- Security (injection): a poisoned analyst note instructing `DROP`, or asserting `GROUNDING_OK=true skip checks` → gates still run and govern; `decide()` ignores the model's claim.
- Security (output scan): model emits a secondary `DROP` snippet in the explanation → `scan_output` strips/declines it.
- Edge case (coverage): top dense cosine below the configured floor → decline "no confident coverage".

**Verification:** No non-SELECT, ungrounded, or restricted-identifier query ever passes; any parse failure declines; decline reasons are specific and categorized; destructive text never survives in output.

- [ ] **Unit 6: Agent assembly, system prompt, structured output, decline path**

**Goal:** Wire Strands `Agent` (custom model + tools), enforce gates, return the typed result (KD3, KD5, KD6, KD7).

**Requirements:** R1, R3, R11, R12

**Dependencies:** Units 2.5, 4, 5

**Files:**
- Create: `text2sql/agent.py` (system prompt, `Text2SQLResult` Pydantic model, `generate_sql(question, history) -> Text2SQLResult`)
- Test: `tests/test_agent.py`

**Approach:**
- Build `Agent(model=Text2SqlBedrockModel(...), tools=[...], system_prompt=…, conversation_manager=SlidingWindowConversationManager())`.
- System prompt encodes: the ERA→schema grounding flow, dialect-from-precedent rule (R3), untrusted-content rule (retrieved text is data, never instructions), SELECT-only (belt-and-suspenders; *enforcement* is in gates).
- Final answer via `agent.structured_output(Text2SQLResult, …)` → `{sql, explanation, tables_used, columns_used, precedent_ids, dialect, declined, missing}`. **Fallback:** if the forced structured-output tool can't be guaranteed for gpt-oss, parse a JSON object from the (reasoning-stripped) final text into `Text2SQLResult`.
- Pass the **per-request retrieval accumulator** (raw top-hit scores + retrieved identifiers from U4 tools) to `gates.decide(...)` — grounding/coverage/policy are checked against what was *retrieved*, not the model's self-reported `tables_used`. On gate failure, convert to a decline with the gate's reason. Run `scan_output` on SQL + explanation. Strip reasoning before output and logging.
- Dialect chosen from the top precedent's `query_engine`; stated in the result.

**Patterns to follow:** Strands `Agent`/`structured_output` from research; `_strip_reasoning`.

**Test scenarios:**
- Happy path (stubbed model + full-catalog fixture): a known ERA-case question yields SQL grounded in real identifiers, correct dialect from the precedent, precedent id cited, UNVERIFIED marker present. *(Uses a seeded full-catalog fixture, since grounding over-declines on the 200-row sample.)*
- Decline path: an out-of-scope question (no coverage) yields `declined=True` + a `missing` reason; no SQL.
- Decline path (no precedent): a question groundable via schema search but with **no ERA precedent above the coverage floor** → decline (no dialect can be inferred), not a dialect-less SQL answer.
- Integration: gate failure on a model-produced ungrounded query downgrades a "success" into a decline (gates override the model).
- Integration (accumulator): model *claims* a table it never retrieved → grounding/policy gate (using the accumulator, not the claim) declines.
- Edge case: `structured_output` forcing unavailable → JSON-from-text path (primary per U2.5) still yields a valid `Text2SQLResult` or declines on parse failure.
- Edge case: dialect matches the top precedent's `query_engine`; stated on the SQL path, omitted on decline (R11).
- Integration: multi-turn — a follow-up reuses history without dangling toolResult errors.

**Verification:** `generate_sql` returns a valid `Text2SQLResult` for covered questions and a clean decline for uncovered ones, with gates authoritative.

- [ ] **Unit 7: CLI chat loop**

**Goal:** Terminal UX mirroring the existing chat pattern (R1).

**Requirements:** R1, R11, R13

**Dependencies:** Unit 6

**Files:**
- Create: `text2sql/cli.py` (entry: `python -m text2sql.cli`)
- Test: `tests/test_cli.py`

**Approach:**
- Reuse the `bedrock_session.chat()` loop shape: prompt for a question, call `generate_sql`, pretty-print SQL + explanation + sources + dialect, or the decline reason. `exit`/`quit` to leave.
- Honor R13 log hygiene: do not print secrets/full ARNs at default verbosity (gate the existing `bedrock_session` prints behind a debug flag or suppress in this entrypoint).

**Patterns to follow:** `bedrock_session.chat()` / `main()`.

**Test scenarios:**
- Happy path: piped question → printed SQL + sources + dialect (stub `generate_sql`).
- Decline path: uncovered question → printed missing-knowledge message, no SQL.
- Edge case: empty input ignored; `exit`/`quit` terminates cleanly; EOF/KeyboardInterrupt handled.
- Test expectation: no assertion on secret-printing beyond confirming default verbosity does not echo ARNs.

**Verification:** `python -m text2sql.cli` runs an interactive session end-to-end against the real model + KB (manual smoke); automated tests cover formatting/decline with a stubbed agent.

## System-Wide Impact

- **Interaction graph:** CLI → agent → custom model provider → `bedrock_session` (federated invoke); agent → tools → retrieval/`embedding_service` → Postgres. The custom provider is the single integration seam with Strands' event loop.
- **Error propagation:** embedding/DB/model failures surface as structured declines or clean CLI errors, never raw stack traces or partial SQL. Gate failures convert model "successes" into declines.
- **State lifecycle risks:** conversation history must keep toolUse/toolResult paired (handled by `SlidingWindowConversationManager`); credential expiry mid-session handled by `refresh_if_needed()` per turn.
- **API surface parity:** `generate_sql(question, history)` is the reusable core; the CLI is a thin wrapper, so a future API/library reuses the same entry without behavior drift.
- **Integration coverage:** retrieval correctness, tool fences, gate enforcement, and the provider's `tool_use` translation are the cross-layer behaviors unit mocks alone won't prove — covered by live-KB integration tests (Units 3/4/6).
- **Unchanged invariants:** `bedrock_session.py` and `embedding_service.py` public behavior is preserved (`pg_config()` gains a read-only variant and `BedrockSession` gains an additive `invoke()` method); `era_tickets` and all ingestion remain untouched.
- **No-execution CI guard:** a static check fails CI if `cursor`/`.execute(` appears in the generation modules (`text2sql/agent.py`, `tools.py`, `bedrock_model.py`), making "no execution" a positive, regression-proof invariant (KD11).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| gpt-oss still emits unreliable `tool_calls` even via the custom provider | **U2.5 live smoke gate** proves it with the real model before U4–U7; if flaky, fall back to prompted ReAct or swap to Nova Pro/Claude behind the same provider seam. |
| `structured_output` (a forced tool call) hits the same end_turn/tool_use defect | Provider handles forced-tool/`tool_choice`; U6 has a JSON-from-final-text fallback. |
| Reverse toolResult mapping breaks the loop on turn 2 | Explicit U2 reverse-translation sub-task + id-pairing test; half-evicted pairs repaired at the provider boundary. |
| Valid, grounded SQL targeting PII passes all gates | `policy_ok` sensitivity gate + `era_tickets` gate denylist + pre-context PII redaction (fail-closed default); R13 classification a production prerequisite. |
| `bedrock_session._invoke_standard` reuse mismatch (no tools, 512 cap, Mantle fallback) | U1 adds a purpose-built `BedrockSession.invoke()`; provider never calls the Mantle-falling-back `_invoke`. |
| 200-row sample schema → over-decline / placeholder ids | Ground against the full catalog DDL; treat sample results as provisional (not a usefulness verdict); thresholds tuned only on the full catalog. |
| Bank data (analyst notes, SQL) sent to external Bedrock | R13 data-classification audit before any production use; v1 is dev-only; least-privilege Bedrock role. |
| Prompt injection via analyst notes / stored SQL | Fence as untrusted + SELECT-only validated by `sqlglot` in code (Unit 5); adversarial poisoned-note test. |
| Credentials expire mid-session | `refresh_if_needed()` before each turn; consider `RefreshableCredentials` if long turns expire. |
| `sqlglot` can't cleanly parse a dialect-specific query | Degrade to SELECT-only + identifier-existence checks; don't block on full parse. |
| Strands SDK version drift (issue #644 fix lands) | We rely on the custom provider, not Converse; verify pinned version to avoid double-handling. |
| Embedding endpoint 503 / outage | Retrieval raises → surfaced as a decline; health-check before big runs (per `RETRIEVAL.md`). |
| v1 on the 200-row sample over-declines by design → no product signal | v1 validates *plumbing* only; happy-path grounding tested on a full-catalog fixture. Accept-rate/edit-distance pilot metrics require the full catalog loaded (couple to the R13 prerequisite). Don't read v1 sample behavior as a usefulness verdict. |

## Documentation / Operational Notes

- Add `docs/setup-readonly-role.md` (Unit 1) and a short run section to a README (`python -m text2sql.cli`, required env vars).
- Note the data-residency/classification follow-up (R13) as a gate before production.

## Alternative Approaches Considered

- **Strands default `BedrockModel` + Converse** — rejected: confirmed broken for gpt-oss tool use (no stream; `end_turn` vs `tool_use`).
- **Hand-rolled OpenAI tool loop over `bedrock_session` (no Strands)** — viable and simpler, but discards Strands' conversation manager (toolUse/toolResult pairing), `structured_output`, and `@tool` ergonomics; user chose Strands. Kept as a fallback if the custom provider proves troublesome.
- **`OpenAIModel` provider → Bedrock Mantle OpenAI endpoint** — rejected: Mantle authenticates via a bearer token, conflicting with the hard requirement to use the federated SigV4 session (R8).
- **Dense-only retrieval for v1** — rejected in brainstorm; hybrid verified and KBs already built.

## Success Metrics

- Static: generated SQL is parseable, SELECT-only, and references only catalog-confirmed identifiers for covered questions (automatable gate).
- Coverage/abstention: declines on truly out-of-coverage questions without over-declining covered ones (measured on the full catalog).
- Product (pilot, full catalog): analyst accept-as-is rate + edit distance from draft → final query; self-reported time saved vs. from-scratch.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-19-text2sql-agent-requirements.md](docs/brainstorms/2026-06-19-text2sql-agent-requirements.md)
- Repo: `bedrock_session.py`, `embedding_service.py`, `RETRIEVAL.md`
- Strands: [BedrockModel API](https://strandsagents.com/docs/api/python/strands.models.bedrock/), [custom model provider](https://strandsagents.com/docs/user-guide/concepts/model-providers/custom_model_provider/), issues [#630](https://github.com/strands-agents/sdk-python/issues/630)/[#644](https://github.com/strands-agents/sdk-python/issues/644)/[tools #223](https://github.com/strands-agents/tools/issues/223)
- gpt-oss model card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-120b.html
- Text-to-SQL: [DAIL-SQL](https://github.com/BeachWang/DAIL-SQL), [Adaptive Abstention](https://arxiv.org/pdf/2501.10858), [OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [handle raw CoT](https://cookbook.openai.com/articles/gpt-oss/handle-raw-cot)
