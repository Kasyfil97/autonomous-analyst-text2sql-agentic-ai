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

Most tests require a running Postgres KB at `localhost:5433` and the BGE-M3 embedding endpoint. Unit-only tests (no DB) are in `tests/test_gates.py`.

**Run a single test file:**
```bash
pytest tests/test_gates.py -v
```

**Live Bedrock smoke test (requires real OIDC credentials):**
```bash
python tests/smoke_live_tool.py
```

## Architecture

The system converts natural-language questions (Indonesian or English) into draft SQL queries — it never executes SQL. It is a single-session CLI agent backed by a Postgres knowledge base.

### Request Flow

```
question
  → BedrockSession          — OIDC chain: Entra ID → bridge IAM role → target Bedrock role
  → Text2SqlBedrockModel    — custom Strands Model adapter (see below)
  → Strands Agent loop
       tool: search_era_knowledge  → hybrid search over era_knowledge (precedent SQL + notes)
       tool: search_schema         → hybrid search over schema_tables (DDL with columns)
       tool: get_table_schema      → deterministic lookup in schema_columns
  → parse_result()           — extracts JSON from model's final text (primary path)
  → apply_gates()            — fail-closed safety checks (see Gates section)
  → Text2SQLResult           — SQL prefixed with UNVERIFIED_DRAFT marker
  → CLI prints dialect, precedent IDs, tables, explanation, and SQL
```

### Key Modules

| File | Purpose |
|---|---|
| `bedrock_session.py` | OIDC federation and `BedrockSession.invoke()` seam; Mantle fallback |
| `embedding_service.py` | BGE-M3 dense embedding client; `pg_config()` factory (superuser vs. read-only) |
| `text2sql/bedrock_model.py` | Custom `Strands.Model` subclass — the critical workaround for `gpt-oss` |
| `text2sql/retrieval.py` | Hybrid dense + sparse BM25 RRF retrieval over KB tables |
| `text2sql/tools.py` | Three `@tool` functions; `RetrievalContext` (accumulates coverage signals); PII redaction |
| `text2sql/gates.py` | All safety gates: SQL validation, grounding, policy, coverage, output scan |
| `text2sql/agent.py` | Agent assembly, `build_agent()`, `generate_sql()`, `parse_result()`, `apply_gates()` |
| `text2sql/cli.py` | Interactive REPL entry point |

### Critical Workaround: `text2sql/bedrock_model.py`

The model `openai.gpt-oss-120b-1:0` does not support `ConverseStream` and returns `stopReason: end_turn` (not `tool_use`) even when tool calls are present. The default Strands `BedrockModel` breaks on this model. `Text2SqlBedrockModel` calls `invoke_model` directly and translates OpenAI `tool_calls` format ↔ Strands Bedrock-shaped event stream. Do not replace this with the standard `BedrockModel`.

### Gates (`text2sql/gates.py`)

Gates are authoritative and fail-closed — any failure returns a decline rather than an unsafe query:

1. **Coverage gate** — schema top cosine ≥ 0.40 is the authoritative bar. Precedent is **advisory**: a weak/absent ERA precedent (< 0.45) no longer declines — the agent drafts schema-first and surfaces its interpretation choices in `assumptions`. Semantic correctness in the no-precedent path relies on the human reviewer (drafts are never executed); table-level grounding/accumulator still apply. Column-level grounding is a planned fast-follow.
2. **SQL validation** — `sqlglot` AST parse; single `SELECT` only; no DDL/DML; no dangerous functions
3. **Policy gate** — no `TABLE_DENYLIST` tables; no PII/PCI column name fragments
4. **Grounding** — every table referenced in SQL must exist in schema KB AND have been retrieved during this session
5. **Output scan** — no destructive SQL keywords anywhere in the combined explanation + SQL text

### Postgres Knowledge Base

Three knowledge tables (pgvector 0.8.2, PG 16.14 at `localhost:5433`):
- `era_knowledge` — past ERA ticket solutions with precedent SQL
- `schema_tables` — table-level DDL catalog
- `schema_columns` — column-level DDL catalog

Each row has a 1024-dim BGE-M3 dense vector and a sparse BM25 vector. Retrieval uses RRF fusion. The raw `era_tickets` corpus (8811 rows) is inaccessible to the agent — it is denylisted in `gates.py` and the runtime uses a read-only DB role (`t2s_ro`) with no privileges on that table.

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
