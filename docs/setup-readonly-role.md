# Read-only Postgres role for the Text-to-SQL agent

The agent (tools + retrieval) must connect with a least-privilege, **read-only** role —
never the `postgres` superuser (plan R10/KD9). This role can `SELECT` only the knowledge
tables the agent needs, and has **no** access to the raw `era_tickets` corpus, **no**
`CREATE TEMP`, and no execute on dangerous functions.

Run as a superuser once (psql or `docker exec postgres_db psql -U postgres -d postgres`):

```sql
-- 1. Role
CREATE ROLE t2s_ro LOGIN PASSWORD 'CHANGE_ME_ro';

-- 2. Connect + schema usage
GRANT CONNECT ON DATABASE postgres TO t2s_ro;
GRANT USAGE ON SCHEMA public TO t2s_ro;

-- 3. SELECT only on the KB tables the agent reads
GRANT SELECT ON
  era_knowledge,
  era_knowledge_bm25, era_knowledge_bm25_meta,
  schema_tables, schema_tables_bm25, schema_tables_bm25_meta,
  schema_columns, schema_columns_bm25, schema_columns_bm25_meta
TO t2s_ro;

-- 4. Explicitly NO access to the raw corpus
REVOKE ALL ON era_tickets FROM t2s_ro;
REVOKE ALL ON era_tickets_vec FROM t2s_ro;
REVOKE ALL ON era_tickets_descpseudo FROM t2s_ro;

-- 5. Harden: no temp objects, no default privileges on future tables
REVOKE TEMPORARY ON DATABASE postgres FROM t2s_ro;
REVOKE CREATE ON SCHEMA public FROM t2s_ro;
```

Then add the read-only credentials to `.env`:

```dotenv
PG_RO_USER=t2s_ro
PG_RO_PASSWORD=CHANGE_ME_ro
# PG_HOST / PG_PORT / PG_DBNAME are shared with the existing config
```

The agent calls `pg_config(readonly=True)`, which **requires** `PG_RO_USER` to be set and
will raise rather than silently fall back to the superuser. Build/ingest scripts keep
using the default `pg_config()` (superuser) path.

> **Verify the boundary** after creating the role:
> ```sql
> SET ROLE t2s_ro;
> SELECT count(*) FROM era_knowledge;     -- works
> SELECT count(*) FROM era_tickets;       -- must error: permission denied
> RESET ROLE;
> ```
