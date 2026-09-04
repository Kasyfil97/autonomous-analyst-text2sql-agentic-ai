# Text-to-SQL Retrieval — Reference

> **ERA KB superseded:** the live agent now retrieves ERA precedents from **`era_corpus`
> (V2)** via a 3-lane hybrid (dense + sparse + synthetic-question vectors), not the
> `era_knowledge` table described below. See **[`RETRIEVAL_V2.md`](RETRIEVAL_V2.md)** for
> the current ERA KB and the analyst flow. This document remains the reference for the
> **schema KB** (`schema_tables` / `schema_columns`, unchanged) and the shared hybrid
> recipe; the `era_knowledge` sections are V1 history.

How the Text-to-SQL agent retrieves grounding knowledge to write new SQL. Read this
first when working on retrieval/knowledge in this repo. Two complementary knowledge
bases, both hybrid (dense + sparse) in Postgres:

1. **ERA knowledge** — past ticket solutions (query precedents). _"How was a similar
   request solved — which tables, what query shape?"_
2. **Schema knowledge** — table & column definitions. _"Which table holds X? What does
   column Y mean?"_ See [Schema Knowledge Base](#schema-knowledge-base).

See [Recommended agent flow](#recommended-agent-flow) for how they combine.

_Last built: ERA22–ERA26 corpus (4233 tickets); schema catalog (sample export)._

---

## TL;DR

- **ERA knowledge** lives in Postgres table **`era_knowledge`** (4233 rows): dense
  `vector(1024)` (BGE-M3) + sparse `sparsevec(5810)` (local BM25).
- **Schema knowledge** lives in **`schema_tables`** + **`schema_columns`**: same
  dense+sparse design, built from the catalog CSVs (no LLM).
- A separate, pre-existing table **`era_tickets`** (8811 rows, dense `embedding` +
  `ts` tsvector) is the **full raw corpus** and is **left untouched**. `era_knowledge`
  is the cleaned 4233-row subset with richer structured metadata.
- Query = embed the question (dense) + BM25-encode the question (sparse) → search
  both → fuse with RRF. See [Query recipes](#query-recipes).

---

## Data pipeline

```
ERA22_26_raw_cleaned.xlsx  (4233 raw tickets: issue_key, Description, Query_Final, ...)
        │
        │  (manual) added `embedding_context` column → ERA22_26_raw_final.xlsx
        ▼
build_knowledge.py          ← parses embedding_context + extracts metadata;
        │                      Azure OpenAI (gpt-4.1-mini) extracts key_filters
        ▼
ERA22_26_knowledge.jsonl    (4233 docs, one per ticket — the retrieval documents)
ERA22_26_knowledge.xlsx     (same, flattened for inspection)
        │
        ▼
embed_to_pg.py              ← BGE-M3 dense + local BM25 sparse → Postgres
        ▼
Postgres: era_knowledge (+ era_knowledge_bm25, era_knowledge_bm25_meta)
```

### Key files
| File | Purpose |
|---|---|
| `ERA22_26_raw_final.xlsx` | Source: raw tickets + curated `embedding_context` blob |
| `build_knowledge.py` | Transform xlsx → structured JSONL knowledge docs |
| `ERA22_26_knowledge.jsonl` | The 4233 retrieval documents (see schema below) |
| `embed_to_pg.py` | Embed (dense+sparse) and ingest into Postgres |
| `embedding_service.py` | Canonical BGE-M3 client + `pg_config()` (use this) |
| `.env` | Embedding endpoint + Postgres credentials |

---

## Document schema (`ERA22_26_knowledge.jsonl` → `era_knowledge` columns)

| Field / column | Type | Role | Source |
|---|---|---|---|
| `id` | text PK | ticket id (e.g. `ERA26-241`) | `issue_key` |
| `intent_text` | text | **dense embedding target**; distilled summary | parsed from `embedding_context` |
| `search_text` | text | **BM25 sparse target**; keyword-dense blob | concat of intent+domain+tables+codes+terms |
| `query_type` | text | metadata | `Request type:` line |
| `query_engine` | text | `SparkSQL` \| `SQLServer` | `Query engine:` line |
| `domain_tags` | text[] | filterable | `Data domain:` line / `Data_Domain` col |
| `tables` | text[] | filterable | `Tables:` line + regex on SQL |
| `procedures` | text[] | filterable | `Procedures:` line |
| `report_codes` | text[] | filterable (TL506, DI314, GI405…) | regex on SQL/intent |
| `key_filters` | text[] | **dimensions a query is parameterized on** | Azure OpenAI gpt-4.1-mini |
| `search_terms` | text[] | keywords | `Related terms:` line |
| `sql_query` | text | **payload** returned to agent | `Query_Final` col |
| `analyst_notes` | text | **payload**; analyst tips | `Langkah_Pengerjaan` + `Comment` |
| `dense` | `vector(1024)` | BGE-M3 of `intent_text`, cosine | — |
| `sparse` | `sparsevec(5810)` | BM25 of `search_text`, inner product | — |

`key_filters` examples: `account_number, start_date, end_date, file_type` (account
statement); `position_date, last_activity_days_min/max` (savings tiering);
`gl_account_number, loan_master_position_date` (loan/GL). 22 rows have empty
`key_filters` — legitimately doc-only/manual tickets with no SQL.

---

## Postgres objects

Connection: see `.env` (`PG_HOST=localhost PG_PORT=5433 PG_DBNAME=postgres
PG_USER=postgres`). PG 16.14, **pgvector 0.8.2**.

| Object | Rows | Notes |
|---|---|---|
| `era_knowledge` | 4233 | main table (schema above) |
| `era_knowledge_bm25` | 5810 | BM25 vocab: `token, idx (1-based), idf` |
| `era_knowledge_bm25_meta` | 4 | `avgdl, k1=1.5, b=0.75, dim=5810` |
| `era_tickets` | 8811 | **PRE-EXISTING, DO NOT TOUCH** — full raw corpus, dense+tsvector |

Indexes on `era_knowledge`: HNSW `dense vector_cosine_ops`, HNSW
`sparse sparsevec_ip_ops`, GIN on `domain_tags` and `tables`.

> **Why two tables?** `era_tickets` (8811) is the older/full pipeline using
> Postgres FTS (`ts` tsvector) for the lexical side. `era_knowledge` (4233) is the
> cleaned subset with a learned-ish BM25 `sparsevec` and structured array metadata
> (`key_filters`, etc.). Decision on record: keep them **separate**.

---

## Embedding service (BGE-M3)

OpenAI-compatible endpoint, **dense only** (sparse is NOT returned — that's why the
sparse side is computed locally as BM25).

```python
from embedding_service import embed_one, embed, pg_config
vec = embed_one("some text")        # -> 1024-dim list[float]
```
Config: `EMBED_URL`, `EMBED_TOKEN`, `EMBED_MODEL=/data/bge-m3`, `EMBED_DIM=1024`.

---

## Query recipes

### 1. Dense (semantic)
```sql
-- :qd = embed_one(question) formatted as '[v1,v2,...]'
SELECT id, query_type, 1 - (dense <=> :qd::vector) AS cosine
FROM era_knowledge ORDER BY dense <=> :qd::vector LIMIT 10;
```

### 2. Sparse (BM25 lexical)
Encode the question into the SAME sparse space using the persisted vocab:
```python
import re
STOP = {"yang","dan","untuk","dengan","dari","pada","ke","di","ini","itu","atau",
        "juga","akan","sudah","tidak","ada","data","dalam","saya","kami","mohon",
        "bantuan","terima","kasih","the","a","an","of","to","for","and","or","in",
        "on","at","is","are","be","by","with","as","from","this","that","we","i"}
def tokenize(t):
    return [w for w in re.findall(r"[a-z0-9]+",(t or "").lower())
            if len(w) >= 2 and w not in STOP]

# load vocab/idf/dim once from era_knowledge_bm25(_meta)
#   vocab: {token: idx}, idf: {token: idf}, dim: int
def encode_query_sparse(q, vocab, idf, dim):
    ws = {vocab[t]: idf[t] for t in set(tokenize(q)) if t in vocab}
    if not ws: return f"{{1:0}}/{dim}"
    return "{" + ",".join(f"{i}:{w:.6f}" for i,w in sorted(ws.items())) + "}/" + str(dim)
```
```sql
-- :qs = encode_query_sparse(question)  (note: <#> returns NEGATIVE inner product)
SELECT id, query_type, -(sparse <#> :qs::sparsevec) AS bm25
FROM era_knowledge ORDER BY sparse <#> :qs::sparsevec LIMIT 10;
```

### 3. Hybrid (RRF, recommended)
```sql
WITH d AS (SELECT id, row_number() OVER (ORDER BY dense  <=> :qd::vector)    rk
           FROM era_knowledge ORDER BY dense  <=> :qd::vector    LIMIT 50),
     s AS (SELECT id, row_number() OVER (ORDER BY sparse <#> :qs::sparsevec) rk
           FROM era_knowledge ORDER BY sparse <#> :qs::sparsevec LIMIT 50)
SELECT COALESCE(d.id, s.id) AS id,
       COALESCE(1.0/(60+d.rk),0) + COALESCE(1.0/(60+s.rk),0) AS score
FROM d FULL OUTER JOIN s ON d.id = s.id
ORDER BY score DESC LIMIT 10;
```
Then fetch payload: `SELECT sql_query, analyst_notes, intent_text, key_filters,
tables FROM era_knowledge WHERE id = ANY(:ids)`.

Optional metadata pre-filter (uses GIN): `WHERE 'PINJAMAN' = ANY(domain_tags)` or
`WHERE 'era_tickets' = ANY(tables)`.

### Verification (both ranked the correct ticket #1)
| Question target | Dense | Sparse | Hybrid |
|---|---|---|---|
| ERA26-241 (Ciamis absensi) | #1 | #1 | #1 |
| ERA25-1685 (TL506 report) | #1 | #1 | #1 |

---

## Schema Knowledge Base

Grounds SQL generation in the actual datalake schema — **table selection** + **exact
column names/meanings/coded values**. Complements the ERA KB (which gives query
precedents but not authoritative column definitions). **No LLM** in this pipeline.

### Pipeline & files
```
bribrain_mage_merged_tables_*.csv   ─┐  (one row per table)
bribrain_mage_merged_columns_*.csv  ─┘  (one row per column; join columns.table_id = tables.id)
        │
        ▼
build_schema_kb.py     ← deterministic reformat + cleaning (NO LLM)
        ▼
schema_tables.jsonl    (one doc per TABLE)
schema_columns.jsonl   (one doc per COLUMN)
        │
        ▼
embed_schema_to_pg.py  ← BGE-M3 dense + local BM25 sparse → Postgres
        ▼
Postgres: schema_tables, schema_columns (+ *_bm25, *_bm25_meta)
```

| File | Purpose |
|---|---|
| `bribrain_mage_merged_tables_*.csv` | Source: table catalog (`;`-delimited) |
| `bribrain_mage_merged_columns_*.csv` | Source: column catalog (join `table_id → tables.id`) |
| `build_schema_kb.py` | CSV → two JSONL knowledge sets (auto-detects newest CSVs) |
| `embed_schema_to_pg.py` | Embed (dense+sparse) → `schema_tables` / `schema_columns` |

### Two granularities
| Table | Unit | Answers | Payload |
|---|---|---|---|
| `schema_tables` | one per table | "Which table holds concept X?" | **full column dictionary** (`columns_dict`) — one hit gives everything to write the query |
| `schema_columns` | one per column | "What does field X mean? valid codes?" | `description` + `col_knowledge` |

### `schema_tables` columns
| col | role |
|---|---|
| `id` | `schema.table_name` (PK) |
| `dense_text` | **dense target**: humanized name + business title + description + column rundown |
| `search_text` | **BM25 target**: name + all field names + titles + domain |
| `table_name`, `source_schema`, `source_type` | metadata |
| `domain_tags` text[] | derived from description ("pada domain X …") — GIN |
| `column_names` text[] | every field name — GIN |
| `n_columns` int, `ai_generated` bool | metadata |
| `table_description`, `columns_dict` | **payload** (full dict) |
| `dense vector(1024)`, `sparse sparsevec(N)` | vectors |

### `schema_columns` columns
| col | role |
|---|---|
| `id` | `schema.table.field` (PK) |
| `dense_text` | **dense target**: field + business title + parent-table context + description + type |
| `search_text` | **BM25 target**: field + title + description + `col_knowledge` + table |
| `table_name`, `field_name`, `business_title`, `data_type`, `knowledge_source` | metadata |
| `domain_tags` text[] | inherited from parent table — GIN |
| `ai_generated` bool | metadata (true ≈ AI-written description, lower confidence) |
| `description`, `col_knowledge` | **payload** |
| `dense vector(1024)`, `sparse sparsevec(N)` | vectors |

### Cleaning rules baked into `build_schema_kb.py`
1. Strip literal `[AI]` markers from embedding text; keep as `ai_generated` metadata.
2. Remove repetitive boilerplate (e.g. "untuk kebutuhan pengolahan, pelaporan, dan
   rekonsiliasi data") from **embedding text only** — full text stays in payload — so
   dense vectors separate on real content.
3. Normalize `data_type` (`String/Text`→`string`, `Double/Number`→`double`, …).
4. Derive `domain_tags` from the description.
5. `col_knowledge` is **noisy/secondary** (some rows have mismatched definitions):
   used in payload + sparse, but dense leads with `description`.

### PG objects
| Object | Rows (sample) | Vectors |
|---|---|---|
| `schema_tables` | 200 | dense 1024 + sparse 684 |
| `schema_columns` | 200 | dense 1024 + sparse 698 |
| `schema_tables_bm25` / `_meta`, `schema_columns_bm25` / `_meta` | — | BM25 vocab (per KB) |

Indexes per table: HNSW `dense vector_cosine_ops`, HNSW `sparse sparsevec_ip_ops`,
GIN on `domain_tags` (and `column_names` for `schema_tables`).

### Querying
Same [Query recipes](#query-recipes) — swap the table name and read the BM25
vocab/`dim` from that KB's `*_bm25` / `*_bm25_meta`. Useful extra: once a table is
known, get its whole dictionary deterministically —
```sql
SELECT field_name, business_title, description, data_type
FROM schema_columns WHERE table_name = :t ORDER BY field_name;
```

Verification demo `"rekening simpanan harian saldo nasabah"` → `schema_tables`
returned `0000_staging_raw_as4_ddmast` (*"detail rekening simpanan dengan partisi
harian"*) at **#1 sparse / #2 dense**.

> **`tid<N>` id artifact:** in the partial sample export, some columns' parent tables
> aren't in the 200-table sample, so their ids fall back to `tid<N>.<field>`. On the
> **full export** these resolve to real `schema.table.field`. Build/embed scripts are
> unchanged at scale — just drop in the full CSVs and re-run.

---

## Recommended agent flow

1. **ERA KB** hybrid retrieve → candidate tables + query idioms (from `era_knowledge`:
   `tables`, `sql_query`, `key_filters`, `analyst_notes`).
2. **Schema KB** lookup for those candidate tables → exact columns + meanings + coded
   values (`schema_tables.columns_dict`, or `schema_columns WHERE table_name=…`). If
   the table is unknown, retrieve `schema_columns`/`schema_tables` by the concept first.
3. **Compose** SQL grounded in both. For the live profiling DB, the existing
   `profiling_*` tools (`profiling_describe_table`, `profiling_search_columns`,
   `profiling_search_values`) give real-time schema + value grounding.

Why both KBs: ERA shows *idioms* (e.g. `status not in (2,4,8)`, the `dla7` date math)
but not definitions; the schema KB *defines* columns/codes but not the idioms.

---

## Operational notes / gotchas

- **Rebuild knowledge:** `python build_knowledge.py --use-llm` (Azure OpenAI env
  vars required). Idempotent; caches LLM `key_filters` per-id in
  `.key_filters_cache.json`, retries only gaps on rerun. Use 2 workers — the
  gpt-4.1-mini deployment 429s under higher concurrency.
- **Re-ingest:** `python embed_to_pg.py` (full) or `--limit N` (test on a fresh
  table — sparse dim is locked at create, so DROP before changing corpus size).
  Upserts by `id`. Defaults to `KNOWLEDGE_TABLE=era_knowledge`; deliberately
  decoupled from `PG_TABLE` so it can never write into `era_tickets`.
- **Quick hybrid self-test:** `python embed_to_pg.py --demo-query "..."`.
- **Rebuild schema KB (no Azure):** `python build_schema_kb.py` then
  `python embed_schema_to_pg.py` (`--demo-query "..."` to self-test, `--limit N` for a
  subset). `embed_schema_to_pg.py` **DROPs + recreates** `schema_tables`/`schema_columns`
  each run (idempotent). For the full catalog, drop the full CSVs in and re-run unchanged.
- **Embedding service outages:** the BGE-M3 endpoint can return `503` transiently;
  `embed_dense` retries with backoff then exits. Health-check before a big run:
  POST `{"model": EMBED_MODEL, "input": ["probe"]}` to `EMBED_URL` and expect a
  1024-dim vector. Re-run the ingest once it's back (idempotent).
- **Background process gotcha (Windows):** these scripts run as **`python3.12.exe`**,
  not `python.exe`. To check if a run is alive, search Win32_Process by
  `CommandLine` across all image names — filtering on `Name='python.exe'` finds
  nothing and falsely looks "dead." Also pass `python -u` for live progress
  (stdout is block-buffered to the task file otherwise).
- **Sparse vector format:** pgvector `sparsevec` literal is `{idx:val,...}/dim`
  with **1-based, ascending** indices; cannot be empty (use `{1:0}/dim`).
