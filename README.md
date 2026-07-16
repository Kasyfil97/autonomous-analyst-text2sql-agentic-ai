# Text-to-SQL Agent

An agent that turns a natural-language question (Indonesian or English) into a **draft
SQL query** or a **prose KB answer** — it does **not** execute SQL. Answers are grounded
in two Postgres knowledge bases (see [`RETRIEVAL.md`](RETRIEVAL.md)): ERA ticket
precedents and the datalake schema catalog. Built on the
[Strands Agents SDK](https://strandsagents.com) driving **gpt-oss-120b on AWS Bedrock**
through a federated OIDC session.

## How it works

```
question ─▶ Orchestrator (forced tool-call intent router)
              ├─ intent=sql    ─▶ Text2SQL agent (Strands, gpt-oss-120b)
              │                     ├─ search_era_knowledge   (precedent SQL + notes)
              │                     ├─ search_schema          (candidate tables/columns, DDL)
              │                     └─ get_table_schema       (authoritative column dict)
              │                   ─▶ JSON draft ─▶ gates (coverage · SELECT-only · grounding · policy · scan)
              │                   ─▶ SQL + warnings + assumptions + sources + dialect
              ├─ intent=search ─▶ Search agent (same tools, prose output)
              │                   ─▶ grounded KB answer + source tables/ERA IDs + warnings
              └─ intent=other  ─▶ bilingual out-of-scope message (no sub-agent invoked)
```

The model never reaches a SQL execution path; retrieved content is treated as untrusted
and PII-redacted before it enters the prompt. Gates are **warn-don't-block**: a failing
gate attaches a severity-tagged warning to the result instead of declining, so the draft
still reaches the human reviewer. The only hard declines are no-parseable-answer and
no-SQL, where there is nothing to return. See
[`docs/plans/2026-06-19-001-feat-text2sql-agent-plan.md`](docs/plans/2026-06-19-001-feat-text2sql-agent-plan.md).

## Setup

```bash
pip install -r requirements.txt
```

Configure `.env` (see existing keys for the embedding endpoint, Postgres, and the
Bedrock/Entra federation). Then create the least-privilege read-only DB role:

- Follow [`docs/setup-readonly-role.md`](docs/setup-readonly-role.md) and set
  `PG_RO_USER` / `PG_RO_PASSWORD`.

## Run

```bash
python -m text2sql.cli
```

Type a question; get a draft SQL query (marked `UNVERIFIED`) with its reasoning,
precedent ticket id(s), and dialect — or a KB answer in prose — depending on intent.
Set `T2S_DEBUG=1` for verbose auth output. Set `T2S_AUDIT_LEVEL=DEBUG` for gate/retrieval
internals.

### Sage web API

```bash
python -m text2sql.api
python -m text2sql.api --host 0.0.0.0 --port 9000   # optional bind overrides
```

Serves the Sage FastAPI backend (semantic table search + draft-SQL agent) that the
frontend console talks to. It displays warnings, assumptions, source tables, and ERA
precedent IDs, and never executes SQL.

Optional env: `SAGE_API_TOKEN` (shared bearer token; unset = open gate on the
network-restricted host) and `SAGE_FRONTEND_ORIGIN` (exact CORS origin, default
`http://localhost:3000`). Both keep a one-release dual-read fallback to the legacy
`BRISA_API_TOKEN` / `BRISA_FRONTEND_ORIGIN` names. The frontend's
`NEXT_PUBLIC_API_TOKEN` must match `SAGE_API_TOKEN`.

## Tests

```bash
pytest                              # unit + live-KB integration (80 tests)
python tests/smoke_live_tool.py     # U2.5 live gate: gpt-oss tool-calling via Bedrock
```

## Key modules

| File | Purpose |
|---|---|
| `text2sql/orchestrator.py` | Intent router — forced tool-call classification → sql / search / other |
| `text2sql/agent.py` | Text2SQL sub-agent: Strands loop + `parse_result()` + `apply_gates()` |
| `text2sql/search_agent.py` | Search sub-agent: KB-exploration questions answered in prose |
| `text2sql/gates.py` | Safety gates (warn-don't-block): SQL validation, grounding, policy, coverage, output scan |
| `text2sql/tools.py` | Three `@tool` functions; `RetrievalContext`; PII redaction |
| `text2sql/retrieval.py` | Hybrid dense + sparse BM25 RRF retrieval over KB tables |
| `text2sql/bedrock_model.py` | Custom `Strands.Model` for gpt-oss (ConverseStream workaround) |
| `text2sql/audit_log.py` | Per-request structured logger; request IDs via contextvars |
| `text2sql/prompt_loader.py` | Loads named prompts from `prompts/prompts.md` (parsed once, cached) |
| `bedrock_session.py` | OIDC federation and `BedrockSession.invoke()` seam; Mantle fallback |
| `text2sql/cli.py` | Interactive REPL entry point |
| `text2sql/web.py` | Single-page chat UI over stdlib `ThreadingHTTPServer`; delegates to orchestrator |

## Status / limitations

- v1 runs against the **200-row sample** schema export, so grounding warnings are expected;
  production accuracy requires the **full catalog**.
- **Production prerequisite:** the PII data-classification audit (R13) that the policy
  gate and redaction key off — see the plan's Open Questions.
- Gates are currently **warn-don't-block**; in production the grounding and policy gates
  should move back to fail-closed once the full catalog and R13 classification are in place.
