# Text-to-SQL Retrieval V2 — ERA Corpus Reference

How the **`era_corpus`** knowledge base is built and queried. This is the V2 ERA
precedent KB — a redesign of `era_knowledge` (see [RETRIEVAL.md](RETRIEVAL.md))
that closes the **query↔document asymmetry** gap and adds enrichment that powers a
multi-case analyst flow combining ERA precedents with the schema KB
(`schema_tables` / `schema_columns`).

> **V2, not a replacement.** `era_knowledge` (V1) still exists. `era_corpus` is a
> parallel table built by a new, LLM-distilled pipeline. Both are hybrid
> (dense + sparse BM25) and follow the same [Query recipes](RETRIEVAL.md#query-recipes)
> shape; V2 adds a third retrieval lane (synthetic-question vectors) and richer
> per-ticket metadata.

_Source corpus: `preprocessing/ERA22_26_raw_cleaned.xlsx` (8799 tickets)._

---

## TL;DR

- **`era_corpus`** (~8799 rows, one per ticket): dense `vector(1024)` (BGE-M3 of
  `canonical_need`) + sparse `sparsevec(N)` (local BM25 of `search_text`), plus
  payload + structured metadata.
- **`era_corpus_qvec`**: one row per *synthetic question* — a second dense view of
  each ticket. This is the **multi-vector** lane; it boosts recall without diluting
  the primary vector.
- **`era_corpus_bm25` / `_bm25_meta`**: per-KB BM25 vocab (`token, idx, idf`) and
  params (`avgdl, k1=1.5, b=0.75, dim`). Tokenizer/stopwords are **imported from
  `text2sql.retrieval`** so the doc side and query side are identical.
- Query = embed the question (dense) + BM25-encode it (sparse) + match against
  synthetic-question vectors → fuse three lanes with RRF → collapse by ticket.
- Lives in schema **`public`** (alongside `era_knowledge`). PG 16 / **pgvector
  0.8.3** at `localhost:5432`. Read via least-privilege `t2s_ro`.

---

## Why V2 — the query↔document asymmetry

A user types a short need: *"Retail CIF Selindo dan Retail Giro Selindo terkini."*
The raw ticket `Description` is a 1200-char formal letter (salutations, PIC names,
phone numbers, letter refs). Embedding the raw letter buries the ~5 tokens of real
signal under boilerplate the user never types.

**V2 reshapes every ticket into something that looks like what a user asks:**

| Field | Role | Shaped like |
|---|---|---|
| `canonical_need` | primary **dense** target | a 1–2 sentence need statement |
| `synthetic_questions` | **multi-vector dense** (in `era_corpus_qvec`) | 3 paraphrased user questions |
| `search_text` | **sparse BM25** target | keyword-dense blob (need+questions+keywords+domain) |

Dense captures *meaning* (giro ≈ rekening giro); sparse locks *rare exact tokens*
(`SELINDO`, `TL506`); the synthetic-question vectors catch phrasings the single
canonical vector misses. **Answer artifacts (SQL/pseudocode) are never embedded** —
they are payload; a user query never contains SQL, so embedding it would break the
symmetry.

---

## Data pipeline

```
preprocessing/ERA22_26_raw_cleaned.xlsx   (8799 tickets: issue_key, Description,
        │                                   Langkah_Pengerjaan, Pseudocode,
        │                                   Query_Final, Data_Domain, Comment)
        │
        │  build_era_embedding_corpus.py   ← LLM (gpt-oss-120b via Bedrock/OIDC) distills
        │                                     each ticket in ONE pass; rule-based clean + PII
        ▼
preprocessing/era_embedding_corpus.jsonl  (canonical_need, synthetic_questions,
        │                                   keywords, key_filters, domain_tags, has_solution, payload)
        │  (+ era_embedding_corpus.csv for inspection)
        │
        │  embed_and_ingest_corpus.py      ← BGE-M3 dense + local BM25 sparse;
        │                                     rule-based tables/query_engine
        ▼
Postgres (public): era_corpus (+ era_corpus_qvec, era_corpus_bm25, _bm25_meta)
```

`key_filters` are produced inline by the build's single LLM call (the prompt sees the
final query, where parameters are most visible). The former standalone
`enrich_key_filters.py` is retired; ingest still merges a legacy `era_key_filters.jsonl`
sidecar if one exists, but inline values win.

### Key files
| File | Purpose |
|---|---|
| `preprocessing/build_era_embedding_corpus.py` | Distill xlsx → JSONL knowledge docs (LLM, one pass: need+questions+keywords+key_filters), resumable, PII-redacted |
| `preprocessing/embed_and_ingest_corpus.py` | Embed (dense+sparse) + rule-based enrichment → Postgres |
| `preprocessing/era_corpus_schema.sql` | Canonical DDL for era_corpus (+ qvec/bm25/meta) |
| `text2sql/embedding_service.py` | BGE-M3 client (`embed`, `embed_one`) + `pg_config()` |
| `text2sql/retrieval.py` | Canonical `tokenize()` / `encode_query_sparse()` (reused so doc↔query match) |

---

## Document schema (`era_corpus` columns)

| Column | Type | Role | Source |
|---|---|---|---|
| `id` | text PK | ticket id (e.g. `ERA26-700`) | `issue_key` |
| `canonical_need` | text | **dense target**; clean need statement | LLM distill of Description + Langkah |
| `search_text` | text | **BM25 sparse target** | concat: canonical + questions + keywords + domain |
| `synthetic_questions` | text[] | metadata; also embedded in `era_corpus_qvec` | LLM |
| `keywords` | text[] | GIN-able keyword tokens | LLM (uppercased), unioned with domain |
| `domain_tags` | text[] | filterable — GIN | parsed from `Data_Domain` |
| `report_codes` | text[] | filterable (TL506, DI314…) — GIN | regex on search_text/canonical |
| `tables` | text[] | filterable — GIN; **join point into `schema_tables`** | rule-based regex on solution |
| `key_filters` | text[] | **dimensions a query is parameterized on** | LLM (inline in `build_era_embedding_corpus.py`) |
| `query_engine` | text | `SparkSQL` \| `SQLServer` \| `''` | rule-based detection |
| `has_solution` | bool | true iff `solution_source = query_final` | derived |
| `solution` | text | **payload** — best-of-three | SELECT `query_final` > `pseudocode` > `langkah` |
| `solution_source` | text | provenance of `solution` | derived |
| `analyst_notes` | text | **payload** — tips | `Langkah_Pengerjaan` + `Comment` |
| `description_clean` | text | **payload** — de-noised, PII-redacted letter | rule-based |
| `dense` | `vector(1024)` | BGE-M3 of `canonical_need`, cosine | — |
| `sparse` | `sparsevec(N)` | BM25 of `search_text`, inner product | — |

> **`solution` is SELECTED, never SYNTHESIZED.** The verified `Query_Final` is reused
> verbatim; the LLM never rewrites SQL (that would risk silently corrupting table/column
> names). `solution_source` records which of the three levels was chosen.

### `era_corpus_qvec` columns (multi-vector dense lane)

| Column | Type | Role |
|---|---|---|
| `id` | text PK | `{issue_key}#{i}` |
| `issue_key` | text | FK back to `era_corpus.id` |
| `qtext` | text | the synthetic question |
| `dense` | `vector(1024)` | BGE-M3 of `qtext` |

---

## PII redaction

Specific account numbers / customer names never appear in a future user query, so
masking them removes both a **leak risk** and **useless rare tokens**. Applied to
`canonical_need`, `synthetic_questions`, `keywords`, and `description_clean` (payload
solution fields keep the raw query — redacted at serve time, as V1 does):

- account numbers / long digit runs → `<NOREK>`
- NIK / NPWP / card (grouped ≥12 digits) → `<ID>`
- account-holder names (`an CV …`, `PT …`) → `<NASABAH>`
- emails → `<EMAIL>`

Both the LLM (instructed via system prompt) and a regex post-pass apply this
(belt-and-suspenders). Placeholder tokens are dropped from `keywords`.

---

## Postgres objects

Connection (`.env`): `PG_HOST=localhost PG_PORT=5432 PG_DBNAME=postgres
PG_USER=admin`, `PG_KB_SCHEMA=adhoc` (→ `search_path=adhoc,public`). PG 16,
**pgvector 0.8.3**.

| Object | Notes |
|---|---|
| `public.era_corpus` | main table (schema above) |
| `public.era_corpus_qvec` | synthetic-question dense vectors |
| `public.era_corpus_bm25` | BM25 vocab: `token, idx (1-based), idf` |
| `public.era_corpus_bm25_meta` | `avgdl, k1=1.5, b=0.75, dim` |

Indexes: HNSW `dense vector_cosine_ops`; HNSW `sparse sparsevec_ip_ops`; GIN on
`domain_tags`, `report_codes`, `tables`; HNSW on `era_corpus_qvec.dense`.
`GRANT SELECT` to `t2s_ro` on all four tables.

### BM25 (local, doc side)

Computed in `embed_and_ingest_corpus.build_bm25()` over the `search_text` corpus,
consistent with the query side in `text2sql.retrieval`:

- **tokenize** — `text2sql.retrieval.tokenize` (regex `[a-z0-9]+`, len ≥ 2, minus
  the shared ID+EN stopword set). *Imported*, not reimplemented.
- **idf** — `ln(1 + (N − df + 0.5)/(df + 0.5))`, persisted per token in `_bm25`.
- **doc weight** — `idf · (tf·(k1+1)) / (tf + k1·(1 − b + b·dl/avgdl))`, `k1=1.5`,
  `b=0.75`.
- **query weight** — the persisted `idf` only (see `encode_query_sparse`).
- **`dim`** = vocab size, **read from `_bm25_meta`, never hardcoded** (it grows with
  corpus size — the `sparsevec(N)` column is locked at CREATE, so a corpus-size
  change requires `--recreate`).
- Sparse literal: `{idx:val,...}/dim`, 1-based ascending, non-empty (`{1:0}/dim`).

---

## Query recipes

Dense/sparse single-lane recipes are identical to
[RETRIEVAL.md §Query recipes](RETRIEVAL.md#query-recipes) — swap the table name and
read the vocab/`dim` from `era_corpus_bm25` / `_bm25_meta`. V2 adds a **third lane**.

### Hybrid 3-lane RRF (recommended)

```
qd = embed_one(question)                          -- dense literal
qs = encode_query_sparse(question, vocab, idf, dim)
```
```sql
WITH d AS (   -- lane 1: main dense (canonical_need)
    SELECT id, row_number() OVER (ORDER BY dense <=> :qd::vector) rk
    FROM public.era_corpus ORDER BY dense <=> :qd::vector LIMIT 50),
s AS (        -- lane 2: main sparse (BM25 of search_text)
    SELECT id, row_number() OVER (ORDER BY sparse::sparsevec <#> :qs::sparsevec) rk
    FROM public.era_corpus ORDER BY sparse::sparsevec <#> :qs::sparsevec LIMIT 50),
q AS (        -- lane 3: best synthetic-question vector per ticket
    SELECT issue_key AS id, min(dense <=> :qd::vector) AS dist
    FROM public.era_corpus_qvec GROUP BY issue_key ORDER BY dist LIMIT 50),
qr AS (SELECT id, row_number() OVER (ORDER BY dist) rk FROM q)
SELECT COALESCE(d.id, s.id, qr.id) AS id,
       COALESCE(1.0/(60+d.rk),0) + COALESCE(1.0/(60+s.rk),0) + COALESCE(1.0/(60+qr.rk),0) AS score
FROM d FULL OUTER JOIN s ON d.id=s.id
       FULL OUTER JOIN qr ON COALESCE(d.id,s.id)=qr.id
ORDER BY score DESC LIMIT 10;
```
Then fetch payload:
```sql
SELECT solution, solution_source, analyst_notes, canonical_need,
       tables, key_filters, query_engine, report_codes, domain_tags
FROM public.era_corpus WHERE id = ANY(:ids);
```

Lane 3 collapses `qvec` to one score per ticket (`min` distance) **before** ranking,
so a ticket with 3 matching paraphrases counts once. RRF `k=60`.

Optional metadata pre-filter (GIN): `WHERE 'GIRO' = ANY(domain_tags)`,
`WHERE 'TL506' = ANY(report_codes)`, `WHERE 'datalake.asrs_fact_savingmaster' = ANY(tables)`.

**Reference implementation + self-test:** `embed_and_ingest_corpus.demo_query()` —
```bash
python -m preprocessing.embed_and_ingest_corpus --demo-query "Retail CIF Selindo terkini"
```

---

## Analyst flow (ERA + schema KB) — implemented

> **Status:** implemented. `era_corpus` is wired into the live agent
> (`retrieval.hybrid_search_era_corpus`, `tools.py`, `agent.precedent_dialect`), and the
> decompose → per-sub-need → reconcile orchestration is in `orchestrator.generate_sql_orchestrated`
> (`decompose.py` + `reconcile.py`), returning a `MultiDraftResult` for multi-part requests.

The unifying move: **decompose the request → build a coverage matrix → route each gap.**

1. **Decompose** the request into sub-needs (entities, segment, scope, metric, grain,
   time, filters). E.g. *"Retail CIF Selindo"* + *"Retail Giro Selindo"* = 2 sub-needs.
2. **Retrieve precedents** — 3-lane hybrid over `era_corpus`, per sub-need and whole
   request. Collect `solution`, `tables`, `key_filters`, `query_engine`.
3. **Ground in schema** — for each precedent's `tables`, look up `schema_tables`
   (`columns_dict`) and `schema_columns` (exact names, coded values). Also search the
   schema KB directly by concept for anything precedents miss.
4. **Coverage / gap analysis** — for each sub-need: covered by a precedent? grounded
   in schema? This branches into three cases (below).
5. **Compose, self-check, annotate** — assemble SQL in the right dialect
   (`query_engine`), run gates (single SELECT, table grounding, denylist/PII), attach
   precedent ids + `assumptions`. **Never execute** — `UNVERIFIED_DRAFT` for human review.

The loop is agentic: gaps feed targeted re-retrieval (*"have CIF, still need Giro"*).

### Case A — multiple tickets needed (composition)
Each sub-need matches a *different* precedent. Extract the relevant fragment from
each `solution`, **reconcile** (align join keys, unify scope/segment/`terkini`
filters, normalize idioms), decide the shape (JOIN on CIF / UNION / separate sets),
ground via `tables` → schema KB, and surface the combination choice as an assumption.
`tables` is the enrichment that makes this possible.

### Case B — ticket gives only partial info
- **B1 (missing a sub-need):** use the precedent as scaffold; draft the uncovered part
  **schema-first** from the schema KB.
- **B2 (right dataset, different parameters):** reuse the precedent's tables + idioms,
  adapt the filters using **`key_filters`** (they name exactly what to re-parameterize:
  `position_date`, `branch_code`, …). Mark precedent-backed vs schema-inferred parts.

### Case C — no ticket / no usable ticket (schema-first)
Precedent is **advisory**: a weak/absent ERA match does not decline (see
`text2sql/gates.py`). Go schema-first: search `schema_tables`/`schema_columns` by
concept. If schema coverage is strong (schema top cosine ≥ 0.40) draft from schema
alone with prominent assumptions; if schema is *also* weak → decline + ask one
clarifying question. This is the only true "cannot answer".

---

## Enrichment fields (what powers A/B)

| Field | How | Enables |
|---|---|---|
| `tables` | rule-based regex (`FROM`/`JOIN`, `[db]..[obj]`, `spark.table/read`); excludes CTE names + Python modules | Case A compose, schema grounding |
| `query_engine` | rule-based signals (`pyspark`/`spark.sql` vs `EXEC`/`..[`/`GETDATE`) | output dialect |
| `key_filters` | LLM, inline in `build_era_embedding_corpus.py` (same pass as the distillation) | Case B parameter adaptation |

`tables` / `query_engine` are recomputed at ingest from the payload already in the
JSONL (zero extra cost). `key_filters` are produced by the build's LLM call and written
into the corpus JSONL directly; a legacy sidecar is merged only if present.

---

## Operational notes

### Build order (2 steps)
```bash
# 1. Distill corpus (LLM, resumable) → era_embedding_corpus.jsonl
#    One pass yields canonical_need + synthetic_questions + keywords + key_filters.
python -m preprocessing.build_era_embedding_corpus

# 2. Embed + ingest → Postgres
python -m preprocessing.embed_and_ingest_corpus --recreate
```
`key_filters` now come inline from step 1 (the former `enrich_key_filters.py` backfill is
retired). `tables`/`query_engine` are rule-based, computed at ingest. Ingest is idempotent.

### Idempotency & flags
- `era_corpus` / `era_corpus_qvec`: `INSERT … ON CONFLICT (id) DO UPDATE` (upsert).
  `era_corpus_bm25` / `_meta`: `TRUNCATE` + insert. **Re-running never duplicates rows.**
- **Always use `--recreate` for a full rebuild** — the `sparsevec(N)` dim is locked at
  CREATE and the full-corpus vocab differs from any test subset; `--recreate` also
  clears stale rows/qvec variants that upsert would leave behind.
- `--limit N` — ingest only the first N tickets (test on a fresh table).
- `--no-qvec` — skip the synthetic-question dense lane.
- `--demo-query "…"` — run the 3-lane hybrid self-test and exit.

### Resumability
Both LLM scripts append to their JSONL and skip already-done `issue_key`s on restart.
On failure (credential expiry, embedding `503`), just re-run the same command.

### Gotchas
- Distillation LLM = `openai.gpt-oss-120b-1:0` via the OIDC chain in
  `bedrock_session.py`; the embedding endpoint is BGE-M3 (`EMBED_URL`, 1024-dim).
- `tables` extraction is heuristic: schema-qualified names (`datalake.*`, `datamart.*`)
  are reliable; a few unqualified CTE aliases may leak. The GIN filter on `tables` is
  advisory, so residual noise is low-impact.
- `id` fallback / dim values are build-dependent — read `dim`/`avgdl` from
  `era_corpus_bm25_meta`, never hardcode.

---

## Integration in the agent (done)

`era_corpus` is live in the agent:
- `retrieval.ALLOWED_KBS` includes `"era_corpus"`, and **`retrieval.hybrid_search_era_corpus`**
  implements the 3-lane fusion (main dense + main sparse + best-per-ticket `era_corpus_qvec`),
  mirroring `embed_and_ingest_corpus.demo_query()`.
- `tools.py` (`_fetch_era_payloads`, `era_knowledge_text`) reads `era_corpus` — rendering
  `solution` (+ `has_solution`/`solution_source`), `key_filters`, `tables`, `query_engine`.
  `RetrievalContext.era_top_cosine` and `agent.precedent_dialect` key off the `era_corpus`
  call label; `search_agent._derive_sources` too.
- The `search_era_knowledge` **tool name is unchanged** (external contract); only its
  backing table moved from `era_knowledge` (V1) to `era_corpus` (V2).

See [[era-corpus-pipeline]] and [RETRIEVAL.md](RETRIEVAL.md) (V1) for the shared recipe shape.
