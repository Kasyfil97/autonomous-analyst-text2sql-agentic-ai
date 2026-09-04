-- ===========================================================================
-- era_corpus — ERA precedent hybrid KB (V2) schema
-- ===========================================================================
-- Canonical DDL for the tables that `preprocessing/embed_and_ingest_corpus.py`
-- builds and the text2sql agent reads (see RETRIEVAL_V2.md). This file mirrors that
-- script's create_schema()/create_indexes()/grant_ro() exactly; keep the two in sync.
--
-- DIMS
--   dense  vector(1024)      — BGE-M3, fixed.
--   sparse sparsevec(:sparse_dim) — local BM25 vocab size; GROWS with the corpus and
--                             is only known after the ingest computes the vocab. The
--                             ingest script substitutes the real value; here it is a
--                             psql variable so the file stays runnable, e.g.:
--                               psql -v sparse_dim=5810 -f era_corpus_schema.sql
--   Read the true value at any time from era_corpus_bm25_meta WHERE key='dim'.
--
-- ORDERING
--   Create tables → load data → THEN create indexes (HNSW builds far faster on a
--   populated table than being maintained per-insert). The ingest script follows this;
--   if you apply this whole file at once on an empty DB it still works, just slower.
--
-- Idempotency: the ingest DROPs these (…CASCADE) on --recreate. This file uses
-- CREATE … IF NOT EXISTS so it is safe to re-apply.
-- ===========================================================================

\set sparse_dim 5810   -- override with -v sparse_dim=<vocab size>; placeholder only

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector (>= 0.8): vector + sparsevec types

-- ---------------------------------------------------------------------------
-- Main table — one row per ERA ticket
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.era_corpus (
    id                  text PRIMARY KEY,          -- issue_key, e.g. ERA26-700
    canonical_need      text,                      -- DENSE target (embedded)
    search_text         text,                      -- BM25/sparse target
    synthetic_questions text[],                    -- also embedded in era_corpus_qvec
    keywords            text[],                    -- GIN-able keyword tokens
    domain_tags         text[],                    -- GIN
    report_codes        text[],                    -- GIN (TL506, DI314, …)
    tables              text[],                    -- GIN; join point into schema_tables
    key_filters         text[],                    -- parameterizable dimensions (Case B)
    query_engine        text,                      -- 'SparkSQL' | 'SQLServer' | ''
    has_solution        boolean,                   -- true iff solution_source='query_final'
    solution            text,                      -- PAYLOAD: best-of-three (selected, not rewritten)
    solution_source     text,                      -- 'query_final' | 'pseudocode' | 'langkah' | 'none'
    analyst_notes       text,                      -- PAYLOAD: langkah_pengerjaan + comment
    description_clean   text,                      -- PAYLOAD: de-noised, PII-redacted letter
    dense               vector(1024),              -- BGE-M3 of canonical_need (cosine)
    sparse              sparsevec(:sparse_dim)     -- BM25 of search_text (inner product)
);

-- ---------------------------------------------------------------------------
-- BM25 vocabulary + params (per-KB, read by text2sql.retrieval)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.era_corpus_bm25 (
    token text PRIMARY KEY,
    idx   int UNIQUE,                              -- 1-based sparsevec index
    idf   double precision
);

CREATE TABLE IF NOT EXISTS public.era_corpus_bm25_meta (
    key   text PRIMARY KEY,                        -- 'avgdl' | 'k1' | 'b' | 'dim'
    value double precision                         -- k1=1.5, b=0.75, dim=:sparse_dim
);

-- ---------------------------------------------------------------------------
-- Synthetic-question vectors — multi-vector dense lane (one row per paraphrase)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.era_corpus_qvec (
    id        text PRIMARY KEY,                    -- {issue_key}#{i}
    issue_key text,                                -- FK back to era_corpus.id
    qtext     text,                                -- the synthetic question
    dense     vector(1024)                         -- BGE-M3 of qtext
);

-- ---------------------------------------------------------------------------
-- Indexes — create AFTER loading data (see ORDERING note above)
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS era_corpus_dense_hnsw
    ON public.era_corpus USING hnsw (dense vector_cosine_ops);
CREATE INDEX IF NOT EXISTS era_corpus_sparse_hnsw
    ON public.era_corpus USING hnsw (sparse sparsevec_ip_ops);
CREATE INDEX IF NOT EXISTS era_corpus_domain_gin
    ON public.era_corpus USING gin (domain_tags);
CREATE INDEX IF NOT EXISTS era_corpus_codes_gin
    ON public.era_corpus USING gin (report_codes);
CREATE INDEX IF NOT EXISTS era_corpus_tables_gin
    ON public.era_corpus USING gin (tables);
CREATE INDEX IF NOT EXISTS era_corpus_qvec_dense_hnsw
    ON public.era_corpus_qvec USING hnsw (dense vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Least-privilege read access for the agent runtime role
-- ---------------------------------------------------------------------------
GRANT SELECT ON public.era_corpus          TO t2s_ro;
GRANT SELECT ON public.era_corpus_bm25     TO t2s_ro;
GRANT SELECT ON public.era_corpus_bm25_meta TO t2s_ro;
GRANT SELECT ON public.era_corpus_qvec     TO t2s_ro;
