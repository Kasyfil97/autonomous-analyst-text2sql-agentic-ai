# Text-to-SQL Agent

An agent that turns a natural-language question (Indonesian or English) into a **draft
SQL query** — it does **not** execute SQL. Answers are grounded in two Postgres knowledge
bases (see [`RETRIEVAL.md`](RETRIEVAL.md)): ERA ticket precedents and the datalake schema
catalog. Built on the [Strands Agents SDK](https://strandsagents.com) driving
**gpt-oss-120b on AWS Bedrock** through a federated OIDC session.

## How it works

```
question ─▶ Strands Agent (custom Bedrock provider, gpt-oss-120b)
              ├─ search_era_knowledge   (how similar cases were solved)
              ├─ search_schema          (candidate tables/columns, as DDL)
              └─ get_table_schema       (authoritative column dictionary)
            ─▶ JSON draft ─▶ gates (coverage · SELECT-only · grounding · policy · scan)
            ─▶ SQL + reasoning + sources + dialect   (or an honest decline)
```

The model never reaches a SQL execution path; retrieved content is treated as untrusted
and PII-redacted before it enters the prompt; the gates are authoritative over the model
(a failed gate becomes a decline). See
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
precedent ticket id(s), and dialect — or a decline naming what knowledge was missing.
Set `T2S_DEBUG=1` for verbose auth output.

### Web chat UI

```bash
python -m text2sql.web
```

Open `http://127.0.0.1:8000` in a browser. The UI provides a modern chat interface over
the same orchestrator as the CLI, including SQL drafts, warnings, assumptions, source
tables, ERA precedent IDs, and KB search answers. The web server does not execute SQL.

## Tests

```bash
pytest                              # unit + live-KB integration (80 tests)
python tests/smoke_live_tool.py     # U2.5 live gate: gpt-oss tool-calling via Bedrock
```

## Status / limitations

- v1 runs against the **200-row sample** schema export, so it over-declines by design;
  product metrics (analyst accept rate / edit distance) require the **full catalog**.
- **Production prerequisite:** the PII data-classification audit (R13) that the policy
  gate and redaction key off — see the plan's Open Questions.
