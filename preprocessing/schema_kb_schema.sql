-- ===========================================================================
-- schema_tables / schema_columns — datalake Schema Knowledge Base (hybrid KB)
-- ===========================================================================
-- Grounds SQL generation in the real datalake catalog: which table holds concept X
-- (schema_tables) and the exact column names / meanings / coded values
-- (schema_columns). See RETRIEVAL.md §"Schema Knowledge Base".
--
-- PROVENANCE: these tables are built by an EXTERNAL pipeline (build_schema_kb.py +
-- embed_schema_to_pg.py, per RETRIEVAL.md) that is NOT in this repo. This DDL is
-- reverse-engineered from the live database for documentation / reproduction /
-- review. If you have the build scripts, they remain the source of truth.
--
-- SCHEMAS: identical structures exist in both `public` and `adhoc`. The agent reads
-- whichever is on its search_path (PG_KB_SCHEMA, default `adhoc`). Choose the target
-- with :kb_schema below.
--
-- DIMS
--   dense  vector(1024)                — BGE-M3, fixed.
--   sparse sparsevec(:st_sparse_dim)   — schema_tables  BM25 vocab size (content-derived)
--   sparse sparsevec(:sc_sparse_dim)   — schema_columns BM25 vocab size (content-derived;
--                                        LARGER than schema_tables — more distinct tokens).
--   These grow with the catalog; the true values live in <kb>_bm25_meta WHERE key='dim'.
--   Observed on the current `adhoc` DB: schema_tables=28648, schema_columns=31314.
--   NOTE: unlike era_knowledge, the schema-KB *_bm25_meta holds only b, k1, dim (NO
--   avgdl). Retrieval only needs `dim` at query time, so this is fine.
--
-- ORDERING: create tables → load data → then indexes (HNSW builds faster on a
-- populated table). Idempotent (IF NOT EXISTS); safe to re-apply.
-- ===========================================================================

\set kb_schema      adhoc     -- target schema: `adhoc` (agent runtime) or `public`
\set st_sparse_dim  28648     -- schema_tables  sparse dim  (override: -v st_sparse_dim=<N>)
\set sc_sparse_dim  31314     -- schema_columns sparse dim  (override: -v sc_sparse_dim=<N>)

CREATE EXTENSION IF NOT EXISTS vector;
SET search_path TO :kb_schema, public;

-- ---------------------------------------------------------------------------
-- schema_tables — one row per catalog TABLE
--   dense_text = DENSE target; search_text = BM25/sparse target; columns_dict =
--   PAYLOAD (full column dictionary so one hit gives everything to write the query).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_tables (
    id                text PRIMARY KEY,            -- schema.table_name
    table_name        text,
    source_schema     text,
    source_type       text,
    domain_tags       text[],                      -- GIN
    column_names      text[],                      -- GIN (every field name)
    n_columns         int,
    ai_generated      boolean,
    dense_text        text,                        -- DENSE target
    search_text       text,                        -- BM25/sparse target
    table_description text,                        -- PAYLOAD
    columns_dict      text,                        -- PAYLOAD (full column dictionary)
    dense             vector(1024),
    sparse            sparsevec(:st_sparse_dim)
);

-- ---------------------------------------------------------------------------
-- schema_columns — one row per catalog COLUMN
--   dense_text = DENSE target; search_text = BM25/sparse target; description +
--   col_knowledge = PAYLOAD (col_knowledge is noisy/secondary).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_columns (
    id               text PRIMARY KEY,             -- schema.table.field
    table_name       text,
    field_name       text,
    business_title   text,
    data_type        text,                         -- normalized: string/double/…
    knowledge_source text,
    domain_tags      text[],                       -- GIN (inherited from parent table)
    ai_generated     boolean,                      -- true ≈ AI-written description
    dense_text       text,                         -- DENSE target
    search_text      text,                         -- BM25/sparse target
    description      text,                         -- PAYLOAD
    col_knowledge    text,                         -- PAYLOAD (noisy/secondary)
    dense            vector(1024),
    sparse           sparsevec(:sc_sparse_dim)
);

-- ---------------------------------------------------------------------------
-- Per-KB BM25 vocab + params (read by text2sql.retrieval._load_vocab)
--   *_bm25_meta holds b=0.75, k1=1.5, dim=<sparse dim>  (no avgdl for the schema KB)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_tables_bm25 (
    token text PRIMARY KEY,
    idx   int,                                     -- 1-based sparsevec index
    idf   double precision
);
CREATE TABLE IF NOT EXISTS schema_tables_bm25_meta (
    key   text PRIMARY KEY,
    value double precision
);

CREATE TABLE IF NOT EXISTS schema_columns_bm25 (
    token text PRIMARY KEY,
    idx   int,
    idf   double precision
);
CREATE TABLE IF NOT EXISTS schema_columns_bm25_meta (
    key   text PRIMARY KEY,
    value double precision
);

-- ---------------------------------------------------------------------------
-- Indexes — create AFTER loading data. (Names mirror the live `adhoc` DB.)
-- ---------------------------------------------------------------------------
-- schema_tables: dense HNSW + GIN(domain_tags, column_names). NOTE: the live DB has
-- NO sparse index on schema_tables — its sparse lane falls back to a sequential scan
-- (fine at a few thousand rows). Uncomment the optional index below to accelerate it.
CREATE INDEX IF NOT EXISTS schema_tables_dense_idx
    ON schema_tables USING hnsw (dense vector_cosine_ops);
CREATE INDEX IF NOT EXISTS schema_tables_domain_tags_idx
    ON schema_tables USING gin (domain_tags);
CREATE INDEX IF NOT EXISTS schema_tables_column_names_idx
    ON schema_tables USING gin (column_names);
-- CREATE INDEX IF NOT EXISTS schema_tables_sparse_idx
--     ON schema_tables USING hnsw (sparse sparsevec_ip_ops);   -- optional (absent in current DB)

-- schema_columns: dense HNSW + sparse HNSW + GIN(domain_tags).
CREATE INDEX IF NOT EXISTS schema_columns_dense_idx
    ON schema_columns USING hnsw (dense vector_cosine_ops);
CREATE INDEX IF NOT EXISTS schema_columns_sparse_idx
    ON schema_columns USING hnsw (sparse sparsevec_ip_ops);
CREATE INDEX IF NOT EXISTS schema_columns_domain_tags_idx
    ON schema_columns USING gin (domain_tags);

-- ---------------------------------------------------------------------------
-- Least-privilege read access for the agent runtime role
-- ---------------------------------------------------------------------------
GRANT SELECT ON schema_tables            TO t2s_ro;
GRANT SELECT ON schema_tables_bm25       TO t2s_ro;
GRANT SELECT ON schema_tables_bm25_meta  TO t2s_ro;
GRANT SELECT ON schema_columns           TO t2s_ro;
GRANT SELECT ON schema_columns_bm25      TO t2s_ro;
GRANT SELECT ON schema_columns_bm25_meta TO t2s_ro;
