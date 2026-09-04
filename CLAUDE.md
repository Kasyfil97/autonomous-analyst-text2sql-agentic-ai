# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run the interactive CLI:**
```bash
python -m text2sql.cli
```

Set `T2S_DEBUG=1` for verbose auth/credential output.

**Run all tests:**
```bash
pytest
```

Most tests require a running Postgres KB at `localhost:5432` (schema `adhoc`, see `PG_KB_SCHEMA`) and the BGE-M3 embedding endpoint. Unit-only tests (no DB) are in `tests/test_gates.py`, `tests/test_decompose.py`, and `tests/test_reconcile.py`.

**Run a single test file:**
```bash
pytest tests/test_gates.py -v
```

**Live Bedrock smoke test (requires real OIDC credentials):**
```bash
python tests/smoke_live_tool.py
```

## Architecture

The system converts natural-language questions (Indonesian or English) into draft SQL queries or prose KB answers — it never executes SQL. It is served over a CLI REPL and a FastAPI ("Sage") backend for the Next.js console. A Postgres knowledge base grounds every answer.

### Request Flow

```
question
  → orchestrator.handle / api  — router: one forced route_intent call → sql | search | other
       intent=other  → bilingual out-of-scope message (no DB / sub-agent)
       intent=search → search_agent.answer_question → prose KB answer (SearchResult)
       intent=sql    → generate_sql_orchestrated:
          decompose_request()        — split a multi-part opening turn into sub-needs
                                        (forced tool call; 1 sub-need = fast path; follow-ups skip)
          per sub-need → generate_sql:
             → BedrockSession         — OIDC chain: Entra ID → bridge → target Bedrock role
             → Text2SqlBedrockModel   — custom Strands Model adapter (gpt-oss workaround)
             → Strands Agent loop
                  search_era_knowledge → 3-lane hybrid over era_corpus (dense + sparse BM25 + qvec)
                  search_schema        → hybrid over schema_tables (DDL with columns)
                  get_table_schema     → deterministic schema_columns lookup
             → parse_result()          — extract JSON from the model's final text
             → apply_gates()           — warn-don't-block safety checks (see Gates)
          reconcile()                  — when >1 sub-need: combine drafts (join keys,
                                          unified filters, dialect resolution)
  → Text2SQLResult (single) | MultiDraftResult (multi) | SearchResult — SQL marked UNVERIFIED_DRAFT
  → CLI / API / Sage frontend render dialect, precedents, tables, assumptions, warnings, SQL
```

Decomposition, per-sub-need drafting, and reconciliation are **deterministic Python orchestration** (not one long agent loop) — gpt-oss tool-calling is fragile, so each sub-need runs a short, focused loop. See [`RETRIEVAL_V2.md`](RETRIEVAL_V2.md) §"Recommended analyst flow" for the 3-case design (compose multiple tickets / partial precedent / no precedent).

### Key Modules

| File | Purpose |
|---|---|
| `bedrock_session.py` | OIDC federation and `BedrockSession.invoke()` seam; Mantle fallback |
| `text2sql/embedding_service.py` | BGE-M3 dense embedding client; `pg_config()` factory (superuser vs. read-only) |
| `text2sql/bedrock_model.py` | Custom `Strands.Model` subclass — the critical workaround for `gpt-oss` |
| `text2sql/orchestrator.py` | Router (`route`, forced `route_intent`) + `generate_sql_orchestrated` (decompose → per-sub-need → reconcile) + `handle` |
| `text2sql/decompose.py` | `decompose_request` — forced-tool-call split of a request into sub-needs (fallback = single) |
| `text2sql/reconcile.py` | `reconcile` sub-drafts (join/filter/dialect resolution) + `resolve_dialect` |
| `text2sql/retrieval.py` | Hybrid RRF retrieval; `hybrid_search` (2-lane) + `hybrid_search_era_corpus` (3-lane incl. qvec); `ALLOWED_KBS` guard |
| `text2sql/tools.py` | Three `@tool` functions (ERA tool reads `era_corpus`); `RetrievalContext`; PII redaction |
| `text2sql/gates.py` | Safety gates (warn-don't-block): SQL validation, grounding, policy, coverage, output scan |
| `text2sql/agent.py` | `generate_sql()`, `parse_result()`, `apply_gates()`, `precedent_dialect()`; `Text2SQLResult` / `SubDraft` / `MultiDraftResult` |
| `text2sql/search_agent.py` | Search sub-agent: KB-exploration questions answered in prose (`SearchResult`) |
| `text2sql/api.py` / `api_serializers.py` | Sage FastAPI backend + result→JSON serializers (single & multi-draft dispatch) |
| `text2sql/cli.py` | Interactive REPL entry point (renders single & multi-draft) |
| `preprocessing/` | Offline KB build: `build_era_embedding_corpus.py` (LLM distill) + `embed_and_ingest_corpus.py` (embed+ingest) + `*.sql` DDL |

### Critical Workaround: `text2sql/bedrock_model.py`

The model `openai.gpt-oss-120b-1:0` does not support `ConverseStream` and returns `stopReason: end_turn` (not `tool_use`) even when tool calls are present. The default Strands `BedrockModel` breaks on this model. `Text2SqlBedrockModel` calls `invoke_model` directly and translates OpenAI `tool_calls` format ↔ Strands Bedrock-shaped event stream. Do not replace this with the standard `BedrockModel`.

### Gates (`text2sql/gates.py`)

Gates are authoritative over the model (they consume structured signals — parsed identifiers, retrieval scores — never the model's free-text claims) and **warn-don't-block**: a failing gate attaches a severity-tagged warning (`[LOW]`/`[HIGH]`/`[CRITICAL]`) and still returns the draft, because drafts are never executed and a human reviews them. `generate_sql` never returns `declined=True`; the only hard decline is the router's out-of-scope (`other`) fallback.

1. **Coverage gate** — schema top cosine ≥ 0.40 is the authoritative bar. Precedent is **advisory**: a weak/absent ERA precedent (< 0.45) no longer declines — the agent drafts schema-first and surfaces its interpretation choices in `assumptions`. **Case C escalation**: when schema is weak *and* there is no confident precedent, the coverage warning is raised from `[LOW]` to `[HIGH] needs clarification` (still non-blocking). Table-level grounding/accumulator still apply; column-level grounding is a planned fast-follow.
2. **SQL validation** — `sqlglot` AST parse; single `SELECT` only; no DDL/DML; no dangerous functions
3. **Policy gate** — no `TABLE_DENYLIST` tables; no PII/PCI column name fragments
4. **Grounding** — every table referenced in SQL must exist in schema KB AND have been retrieved during this session
5. **Output scan** — no destructive SQL keywords anywhere in the combined explanation + SQL text

### Postgres Knowledge Base

pgvector 0.8.3 on PG 16 at `localhost:5432` (agent search_path = `PG_KB_SCHEMA`,public, default `adhoc`). Live KB tables:
- **`era_corpus`** — ERA ticket precedents (V2, ~8799 rows). Dense `vector(1024)` (BGE-M3 of `canonical_need`) + sparse `sparsevec` (BM25 of `search_text`) + companion `era_corpus_qvec` (per-synthetic-question vectors → the 3rd retrieval lane). Payload: `solution`, `tables`, `key_filters`, `query_engine`, `has_solution`, `analyst_notes`. **This is what the live agent reads** (`tools.py`, `retrieval.hybrid_search_era_corpus`), replacing the V1 `era_knowledge` table (still present, unused by the agent).
- `schema_tables` — table-level DDL catalog (dense + sparse).
- `schema_columns` — column-level DDL catalog (dense + sparse).

Retrieval fuses dense + sparse (+ qvec for era_corpus) with RRF. Per-KB BM25 vocab/params live in `<kb>_bm25` / `<kb>_bm25_meta`. **The corpus is indexed in Bahasa Indonesia**, so the agent phrases its tool search queries in Indonesian (see `prompts/prompts.md`) — English queries miss the lexical BM25 lane. The raw `era_tickets` corpus is inaccessible: denylisted in `gates.py` and excluded from the read-only role (`t2s_ro`).

Canonical DDL: `preprocessing/era_corpus_schema.sql` and `preprocessing/schema_kb_schema.sql`.

### Knowledge build (offline)

`era_corpus` is (re)built in two steps — see [`RETRIEVAL_V2.md`](RETRIEVAL_V2.md):
```bash
python -m preprocessing.build_era_embedding_corpus     # LLM distill xlsx → JSONL (one pass: canonical_need + synthetic_questions + keywords + key_filters), resumable, PII-redacted
python -m preprocessing.embed_and_ingest_corpus --recreate   # BGE-M3 dense + local BM25 sparse + rule-based tables/query_engine → Postgres
```
`--recreate` is required for a full rebuild (the `sparsevec` dim is locked at CREATE and grows with the corpus). Retrieval details and the analyst flow live in [`RETRIEVAL_V2.md`](RETRIEVAL_V2.md); the V1 `era_knowledge` pipeline is in [`RETRIEVAL.md`](RETRIEVAL.md).

### Authentication

Credentials flow through a three-hop OIDC chain: Azure Entra ID → bridge IAM role (`AWS_ROLE_ARN_BRIDGE`) → target Bedrock role (`AWS_ROLE_ARN_TARGET`). See `bedrock_session.py` for the implementation. The read-only DB role setup is documented in `docs/setup-readonly-role.md`.

### Environment Variables

Required in `.env`:

```
AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET  # Entra OIDC
AWS_ROLE_ARN_BRIDGE, AWS_ROLE_ARN_TARGET, AWS_REGION
BEDROCK_MODEL_ID        # default: openai.gpt-oss-120b-1:0
EMBED_URL, EMBED_TOKEN, EMBED_MODEL, EMBED_DIM
PG_HOST, PG_PORT, PG_DBNAME
PG_RO_USER, PG_RO_PASSWORD   # agent runtime
PG_USER, PG_PASSWORD          # build/ingest scripts only
```
