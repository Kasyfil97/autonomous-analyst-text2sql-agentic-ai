#!/usr/bin/env python3
"""
Embed the distilled ERA corpus and ingest it into Postgres as a hybrid (dense +
sparse BM25) knowledge base, mirroring the era_knowledge / schema_* design so the
existing text2sql.retrieval.hybrid_search recipe works unchanged.

Input : preprocessing/era_embedding_corpus.jsonl  (from build_era_embedding_corpus.py)

Creates (schema `public`, alongside era_knowledge):
  era_corpus              one row per ticket
      id                  issue_key (PK)
      canonical_need      DENSE target (embedded)
      search_text         BM25/sparse target (canonical_need + questions + keywords + tags)
      synthetic_questions text[]   (payload/metadata)
      keywords            text[]   (GIN)
      domain_tags         text[]   (GIN)
      report_codes        text[]   (GIN)
      has_solution        bool
      solution            text     PAYLOAD — best-of-three (query_final>pseudocode>langkah)
      solution_source     text
      analyst_notes       text     PAYLOAD — langkah_pengerjaan + comment
      description_clean   text     PAYLOAD
      dense               vector(1024)   BGE-M3 of canonical_need
      sparse              sparsevec(DIM)  local BM25 of search_text
  era_corpus_bm25         BM25 vocab: token, idx (1-based), idf
  era_corpus_bm25_meta    key/value: avgdl, k1, b, dim
  era_corpus_qvec         one row per synthetic question (multi-vector dense recall)
      id (issue_key#i), issue_key, qtext, dense vector(1024)

Usage:
  python -m preprocessing.embed_and_ingest_corpus --limit 200        # test subset
  python -m preprocessing.embed_and_ingest_corpus --recreate         # full rebuild
  python -m preprocessing.embed_and_ingest_corpus --demo-query "Retail CIF Selindo terkini"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from text2sql.embedding_service import embed, pg_config          # noqa: E402
from text2sql.retrieval import tokenize, encode_query_sparse     # noqa: E402

HERE = Path(__file__).resolve().parent
IN_JSONL = HERE / "era_embedding_corpus.jsonl"

TABLE = "era_corpus"
K1 = 1.5
B = 0.75
DENSE_DIM = 1024

_REPORT_CODE = re.compile(r"\b(?:TL|DI|GI|GY|LI|DA|CR|GL|LN|SB)\d{2,}\b|\b[A-Z]{2}\d{3}\b")
_NO_SOLUTION = ("", "nan", "comment box", "on comment box")

# --- rule-based enrichment: tables + query_engine -------------------------
# Table references in SQL / SparkSQL / T-SQL. Bank tables may start with a digit
# (e.g. 10000_CR_TL506), so we allow leading digits then filter numeric noise.
_TBL_FROMJOIN = re.compile(r"\b(?:FROM|JOIN)\s+([\w.]+)", re.IGNORECASE)
_TBL_TSQL = re.compile(r"\[(\w+)\]\.\.\[(\w+)\]")          # [db]..[obj]
_TBL_TSQL_DBO = re.compile(r"\[?dbo\]?\.\[?(\w+)\]?", re.IGNORECASE)
_TBL_SPARK = re.compile(r"\.table\(\s*['\"]([\w.]+)['\"]", re.IGNORECASE)
_TBL_SPARK_READ = re.compile(r"spark\.read[^\n]*?['\"]([\w./]+)['\"]", re.IGNORECASE)

_SQL_NOISE = {
    "select", "where", "group", "order", "on", "as", "and", "or", "inner", "left",
    "right", "outer", "full", "cross", "join", "from", "by", "having", "union",
    "all", "distinct", "top", "with", "values", "set", "into", "dual", "lateral",
    "using", "when", "then", "else", "end", "case", "not", "in", "is", "null",
    "cte", "tmp", "temp", "sub", "base", "src", "final", "result",
}
# Python `from X import ...` lines leak into FROM/JOIN matches — drop library modules.
_PYMODULES = {
    "pyspark", "pandas", "numpy", "datetime", "pytz", "dateutil", "time", "os",
    "sys", "re", "json", "collections", "typing", "warnings", "functools", "math",
}

_ENGINE_SPARK = re.compile(
    r"pyspark|SparkSession|spark\.sql|spark\.read|spark\.table|F\.col|import\s+pyspark",
    re.IGNORECASE)
_ENGINE_TSQL = re.compile(
    r"\bEXEC\b|\]\.\.\[|\[dbo\]|GETDATE\(|\bNOLOCK\b|\bISNULL\(|\bSP_\w+|\bTOP\s+\d",
    re.IGNORECASE)


_CTE_DEF = re.compile(r"(?:\bWITH|,)\s+([A-Za-z_]\w*)\s+AS\s*\(", re.IGNORECASE)
_TBL_MISC_NOISE = {"jdbc", "table", "dbtable", "query", "path", "url"}


def extract_tables(*texts: str) -> list[str]:
    """Heuristic table/object extraction from SQL / PySpark / T-SQL bodies.

    Excludes CTE names (defined via ``WITH x AS (...)``) so only real, persisted
    tables/objects remain — those are the join points into schema_tables.
    """
    found = set()
    cte_names = set()
    for t in texts:
        t = t or ""
        for m in _TBL_FROMJOIN.findall(t):
            found.add(m)
        for db, obj in _TBL_TSQL.findall(t):
            found.add(obj)
        for m in _TBL_SPARK.findall(t):
            found.add(m)
        for m in _TBL_SPARK_READ.findall(t):
            found.add(m.split("/")[-1])
        cte_names.update(c.lower() for c in _CTE_DEF.findall(t))
    out = set()
    for name in found:
        name = name.strip().strip("(),;`\"'").rstrip(".").lower()
        if not name or name in _SQL_NOISE or name in _TBL_MISC_NOISE or name.isdigit():
            continue
        if name in cte_names:                       # skip WITH-defined CTEs
            continue
        if not re.match(r"^\w+(\.\w+)*$", name) or len(name) < 3:
            continue
        if name.split(".")[0] in _PYMODULES:
            continue
        out.add(name)
    return sorted(out)


def detect_query_engine(*texts: str) -> str:
    """Return 'SparkSQL' | 'SQLServer' | '' from solution/langkah text signals."""
    blob = "\n".join(t or "" for t in texts)
    spark = bool(_ENGINE_SPARK.search(blob))
    tsql = bool(_ENGINE_TSQL.search(blob))
    if spark and not tsql:
        return "SparkSQL"
    if tsql and not spark:
        return "SQLServer"
    if spark and tsql:
        # Both present (e.g. a PySpark wrapper calling an SP) — prefer the outer engine.
        return "SparkSQL"
    return ""


# ---------------------------------------------------------------------------
# Record shaping
# ---------------------------------------------------------------------------

def _clean(s) -> str:
    s = ("" if s is None else str(s)).strip()
    return "" if s.lower() == "nan" else s


def pick_solution(rec: dict) -> tuple[str, str]:
    """Best-of-three selection (NOT synthesis) — never rewrite the real query."""
    qf = _clean(rec.get("query_final"))
    if qf and qf.lower() not in _NO_SOLUTION:
        return qf, "query_final"
    pc = _clean(rec.get("pseudocode"))
    if pc:
        return pc, "pseudocode"
    lk = _clean(rec.get("langkah_pengerjaan"))
    if lk:
        return lk, "langkah"
    return "", "none"


def extract_report_codes(*texts: str) -> list[str]:
    out = set()
    for t in texts:
        for m in _REPORT_CODE.findall(t or ""):
            out.add(m.upper())
    return sorted(out)


def build_doc(rec: dict) -> dict:
    """Turn one JSONL record into the ingest document (text fields + metadata)."""
    canonical = _clean(rec.get("canonical_need"))
    questions = [q for q in (rec.get("synthetic_questions") or []) if _clean(q)]
    keywords = [k for k in (rec.get("keywords") or []) if _clean(k)]
    domain = [d for d in (rec.get("domain_tags") or []) if _clean(d)]

    # BM25/sparse target: pack all lexical signal (questions help lexically here
    # without the centroid dilution they'd cause if averaged into one dense vector).
    search_text = " ".join([canonical, *questions, *keywords, *domain])
    report_codes = extract_report_codes(search_text, canonical)

    solution, source = pick_solution(rec)
    notes = "\n".join(t for t in (_clean(rec.get("langkah_pengerjaan")),
                                  _clean(rec.get("comment"))) if t)

    # Rule-based enrichment (Case A/B support): where the query reads from + dialect.
    qf = _clean(rec.get("query_final"))
    pc = _clean(rec.get("pseudocode"))
    lk = _clean(rec.get("langkah_pengerjaan"))
    tables = extract_tables(qf, pc, lk)
    query_engine = detect_query_engine(qf, lk, pc)
    # key_filters: produced inline by build_era_embedding_corpus.py (single LLM pass).
    # A legacy sidecar (era_key_filters.jsonl) is still merged by the loader for corpora
    # built before the merge, but inline values take precedence.
    key_filters = [k for k in (rec.get("key_filters") or []) if _clean(k)]

    return {
        "id": _clean(rec.get("issue_key")),
        "canonical_need": canonical,
        "search_text": search_text,
        "synthetic_questions": questions,
        "keywords": keywords,
        "domain_tags": domain,
        "report_codes": report_codes,
        "tables": tables,
        "key_filters": key_filters,
        "query_engine": query_engine,
        "has_solution": bool(rec.get("has_solution")) and source == "query_final",
        "solution": solution,
        "solution_source": source,
        "analyst_notes": notes,
        "description_clean": _clean(rec.get("description_clean")),
    }


# ---------------------------------------------------------------------------
# Local BM25 (doc side) — matches retrieval.encode_query_sparse (query side).
# ---------------------------------------------------------------------------

def build_bm25(docs: list[dict]) -> tuple[dict, dict, float, list[str]]:
    """Return (vocab{token->idx}, idf{token->idf}, avgdl, doc_sparse_literals)."""
    tokenized = [tokenize(d["search_text"]) for d in docs]
    n = len(tokenized)
    df: Counter = Counter()
    for toks in tokenized:
        df.update(set(toks))
    vocab = {tok: i + 1 for i, tok in enumerate(sorted(df))}  # 1-based, ascending
    idf = {tok: math.log(1 + (n - df[tok] + 0.5) / (df[tok] + 0.5)) for tok in vocab}
    avgdl = (sum(len(t) for t in tokenized) / n) if n else 0.0

    literals = []
    dim = len(vocab)
    for toks in tokenized:
        dl = len(toks)
        tf = Counter(toks)
        weights = {}
        for tok, f in tf.items():
            if tok not in vocab:
                continue
            denom = f + K1 * (1 - B + B * (dl / avgdl if avgdl else 0))
            w = idf[tok] * (f * (K1 + 1)) / denom if denom else 0.0
            if w:
                weights[vocab[tok]] = w
        if weights:
            body = ",".join(f"{i}:{w:.6f}" for i, w in sorted(weights.items()))
            literals.append("{" + body + "}/" + str(dim))
        else:
            literals.append(f"{{1:0}}/{dim}")
    return vocab, idf, avgdl, literals


def dense_literal(vec) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# ---------------------------------------------------------------------------
# DDL + ingest
# ---------------------------------------------------------------------------

def create_schema(cur, dim: int, recreate: bool) -> None:
    # Canonical DDL for these tables also lives in `preprocessing/era_corpus_schema.sql`
    # (human/DBA-reviewable). Keep the two in sync — this function is the executor,
    # substituting the runtime `dim`; the .sql documents the same shape with a
    # :sparse_dim placeholder.
    if recreate:
        cur.execute(f"DROP TABLE IF EXISTS public.{TABLE} CASCADE")
        cur.execute(f"DROP TABLE IF EXISTS public.{TABLE}_bm25 CASCADE")
        cur.execute(f"DROP TABLE IF EXISTS public.{TABLE}_bm25_meta CASCADE")
        cur.execute(f"DROP TABLE IF EXISTS public.{TABLE}_qvec CASCADE")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS public.{TABLE} (
            id                  text PRIMARY KEY,
            canonical_need      text,
            search_text         text,
            synthetic_questions text[],
            keywords            text[],
            domain_tags         text[],
            report_codes        text[],
            tables              text[],
            key_filters         text[],
            query_engine        text,
            has_solution        boolean,
            solution            text,
            solution_source     text,
            analyst_notes       text,
            description_clean   text,
            dense               vector({DENSE_DIM}),
            sparse              sparsevec({dim})
        )""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS public.{TABLE}_bm25 (
            token text PRIMARY KEY, idx int UNIQUE, idf double precision)""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS public.{TABLE}_bm25_meta (
            key text PRIMARY KEY, value double precision)""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS public.{TABLE}_qvec (
            id text PRIMARY KEY, issue_key text, qtext text,
            dense vector({DENSE_DIM}))""")


def create_indexes(cur, dim: int) -> None:
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_dense_hnsw "
                f"ON public.{TABLE} USING hnsw (dense vector_cosine_ops)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_sparse_hnsw "
                f"ON public.{TABLE} USING hnsw (sparse sparsevec_ip_ops)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_domain_gin "
                f"ON public.{TABLE} USING gin (domain_tags)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_codes_gin "
                f"ON public.{TABLE} USING gin (report_codes)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_tables_gin "
                f"ON public.{TABLE} USING gin (tables)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_qvec_dense_hnsw "
                f"ON public.{TABLE}_qvec USING hnsw (dense vector_cosine_ops)")


def grant_ro(cur) -> None:
    for t in (TABLE, f"{TABLE}_bm25", f"{TABLE}_bm25_meta", f"{TABLE}_qvec"):
        cur.execute(f"GRANT SELECT ON public.{t} TO t2s_ro")


def ingest(conn, docs: list[dict], vocab, idf, avgdl, sparse_literals,
           dense_vecs, batch=200) -> None:
    dim = len(vocab)
    cur = conn.cursor()

    # main rows
    rows = []
    for d, sp, dv in zip(docs, sparse_literals, dense_vecs):
        rows.append((
            d["id"], d["canonical_need"], d["search_text"], d["synthetic_questions"],
            d["keywords"], d["domain_tags"], d["report_codes"], d["tables"],
            d["key_filters"], d["query_engine"], d["has_solution"],
            d["solution"], d["solution_source"], d["analyst_notes"],
            d["description_clean"], dense_literal(dv), sp,
        ))
    execute_values(cur, f"""
        INSERT INTO public.{TABLE}
          (id, canonical_need, search_text, synthetic_questions, keywords,
           domain_tags, report_codes, tables, key_filters, query_engine,
           has_solution, solution, solution_source,
           analyst_notes, description_clean, dense, sparse)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
          canonical_need=EXCLUDED.canonical_need, search_text=EXCLUDED.search_text,
          synthetic_questions=EXCLUDED.synthetic_questions, keywords=EXCLUDED.keywords,
          domain_tags=EXCLUDED.domain_tags, report_codes=EXCLUDED.report_codes,
          tables=EXCLUDED.tables, key_filters=EXCLUDED.key_filters,
          query_engine=EXCLUDED.query_engine,
          has_solution=EXCLUDED.has_solution, solution=EXCLUDED.solution,
          solution_source=EXCLUDED.solution_source, analyst_notes=EXCLUDED.analyst_notes,
          description_clean=EXCLUDED.description_clean,
          dense=EXCLUDED.dense, sparse=EXCLUDED.sparse
        """, rows,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s::sparsevec)",
        page_size=batch)

    # bm25 vocab + meta
    cur.execute(f"TRUNCATE public.{TABLE}_bm25")
    execute_values(cur, f"INSERT INTO public.{TABLE}_bm25 (token, idx, idf) VALUES %s",
                   [(t, i, idf[t]) for t, i in vocab.items()], page_size=1000)
    cur.execute(f"TRUNCATE public.{TABLE}_bm25_meta")
    execute_values(cur, f"INSERT INTO public.{TABLE}_bm25_meta (key, value) VALUES %s",
                   [("avgdl", avgdl), ("k1", K1), ("b", B), ("dim", float(dim))])
    conn.commit()
    cur.close()


def ingest_qvec(conn, docs: list[dict], batch=64) -> int:
    """Embed each synthetic question and upsert into era_corpus_qvec."""
    pairs = []  # (id, issue_key, qtext)
    for d in docs:
        for i, q in enumerate(d["synthetic_questions"]):
            pairs.append((f"{d['id']}#{i}", d["id"], q))
    if not pairs:
        return 0
    vecs = embed([p[2] for p in pairs], batch_size=batch)
    rows = [(pid, ik, q, dense_literal(v)) for (pid, ik, q), v in zip(pairs, vecs)]
    cur = conn.cursor()
    execute_values(cur, f"""
        INSERT INTO public.{TABLE}_qvec (id, issue_key, qtext, dense)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
          qtext=EXCLUDED.qtext, dense=EXCLUDED.dense
        """, rows, template="(%s,%s,%s,%s::vector)", page_size=batch)
    conn.commit()
    cur.close()
    return len(rows)


# ---------------------------------------------------------------------------
# Demo query (self-test) — hybrid + multi-vector fusion, collapsed by ticket.
# ---------------------------------------------------------------------------

def demo_query(conn, question: str, limit: int = 5) -> None:
    cur = conn.cursor()
    cur.execute(f"SELECT token, idx, idf FROM public.{TABLE}_bm25")
    vocab, idf = {}, {}
    for tok, idx, idf_v in cur.fetchall():
        vocab[tok] = idx
        idf[tok] = idf_v
    cur.execute(f"SELECT value FROM public.{TABLE}_bm25_meta WHERE key='dim'")
    dim = int(cur.fetchone()[0])

    from text2sql.embedding_service import embed_one
    qd = dense_literal(embed_one(question))
    qs = encode_query_sparse(question, vocab, idf, dim)

    # RRF over (a) main dense, (b) main sparse, (c) best synthetic-question dense.
    sql = f"""
    WITH d AS (
        SELECT id, row_number() OVER (ORDER BY dense <=> %(qd)s::vector) rk,
               1-(dense <=> %(qd)s::vector) cosine
        FROM public.{TABLE} ORDER BY dense <=> %(qd)s::vector LIMIT 50),
    s AS (
        SELECT id, row_number() OVER (ORDER BY sparse::sparsevec <#> %(qs)s::sparsevec) rk,
               -(sparse::sparsevec <#> %(qs)s::sparsevec) bm25
        FROM public.{TABLE} ORDER BY sparse::sparsevec <#> %(qs)s::sparsevec LIMIT 50),
    q AS (
        SELECT issue_key AS id, min(dense <=> %(qd)s::vector) AS dist
        FROM public.{TABLE}_qvec GROUP BY issue_key
        ORDER BY dist LIMIT 50),
    qr AS (SELECT id, row_number() OVER (ORDER BY dist) rk, 1-dist AS qcos FROM q)
    SELECT COALESCE(d.id,s.id,qr.id) AS id,
           COALESCE(1.0/(60+d.rk),0)+COALESCE(1.0/(60+s.rk),0)+COALESCE(1.0/(60+qr.rk),0) AS score,
           d.cosine, s.bm25, qr.qcos
    FROM d FULL OUTER JOIN s ON d.id=s.id FULL OUTER JOIN qr ON COALESCE(d.id,s.id)=qr.id
    ORDER BY score DESC LIMIT %(limit)s;
    """
    cur.execute(sql, {"qd": qd, "qs": qs, "limit": limit})
    ids = []
    print(f"\n=== HYBRID RESULTS for {question!r} ===")
    results = cur.fetchall()
    for rid, score, cos, bm25, qcos in results:
        ids.append(rid)
        print(f"  {rid:14} rrf={score:.4f}  cos={cos or 0:.3f}  bm25={bm25 or 0:.3f}  qcos={qcos or 0:.3f}")
    if ids:
        cur.execute(f"""SELECT id, canonical_need, domain_tags, report_codes,
                        tables, key_filters, query_engine, has_solution, solution_source
                        FROM public.{TABLE} WHERE id = ANY(%s)""", (ids,))
        payload = {r[0]: r for r in cur.fetchall()}
        print("\n--- top payloads ---")
        for rid in ids:
            r = payload.get(rid)
            if r:
                print(f"  {rid}: {r[1][:90]!r}")
                print(f"       domain={r[2]} codes={r[3]} engine={r[6]} "
                      f"has_solution={r[7]} src={r[8]}")
                print(f"       tables={r[4]}")
                print(f"       key_filters={r[5]}")
    cur.close()


# ---------------------------------------------------------------------------

KF_SIDECAR = HERE / "era_key_filters.jsonl"


def load_key_filters() -> dict:
    """Load {issue_key: [key_filters]} from the LEGACY sidecar, if present.

    key_filters are now produced inline by build_era_embedding_corpus.py; this sidecar
    exists only for corpora distilled before that merge. build_doc prefers inline values.
    """
    kf = {}
    if not KF_SIDECAR.exists():
        return kf
    with KF_SIDECAR.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                kf[rec["issue_key"]] = rec.get("key_filters", [])
            except (json.JSONDecodeError, KeyError):
                continue
    return kf


def load_docs(limit=None) -> list[dict]:
    docs, seen = [], set()
    kf_map = load_key_filters()
    if kf_map:
        print(f"  merged key_filters for {len(kf_map)} tickets from sidecar")
    with IN_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "key_filters" not in rec:
                rec["key_filters"] = kf_map.get(_clean(rec.get("issue_key")), [])
            d = build_doc(rec)
            if not d["id"] or d["id"] in seen or not d["canonical_need"]:
                continue
            seen.add(d["id"])
            docs.append(d)
            if limit and len(docs) >= limit:
                break
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="ingest only first N tickets")
    ap.add_argument("--recreate", action="store_true", help="DROP + recreate tables (needed if corpus size / sparse dim changed)")
    ap.add_argument("--no-qvec", action="store_true", help="skip synthetic-question dense table")
    ap.add_argument("--demo-query", type=str, default=None, help="run a hybrid self-test and exit")
    args = ap.parse_args()

    conn = psycopg2.connect(**pg_config())

    if args.demo_query:
        demo_query(conn, args.demo_query)
        conn.close()
        return

    docs = load_docs(args.limit)
    print(f"Loaded {len(docs)} tickets from {IN_JSONL.name}")
    if not docs:
        print("No docs — is the corpus build finished / non-empty?")
        return

    print("Building BM25 (doc side)...")
    vocab, idf, avgdl, sparse_literals = build_bm25(docs)
    print(f"  vocab dim={len(vocab)}  avgdl={avgdl:.2f}")

    print("Embedding canonical_need (dense)...")
    dense_vecs = embed([d["canonical_need"] for d in docs], batch_size=64)

    cur = conn.cursor()
    create_schema(cur, len(vocab), recreate=args.recreate)
    conn.commit()
    cur.close()

    print(f"Ingesting {len(docs)} rows into public.{TABLE}...")
    ingest(conn, docs, vocab, idf, avgdl, sparse_literals, dense_vecs)

    if not args.no_qvec:
        print("Embedding + ingesting synthetic questions (era_corpus_qvec)...")
        n = ingest_qvec(conn, docs)
        print(f"  qvec rows: {n}")

    cur = conn.cursor()
    print("Creating indexes...")
    create_indexes(cur, len(vocab))
    grant_ro(cur)
    conn.commit()
    cur.close()

    print(f"\nDone. public.{TABLE} ready ({len(docs)} tickets, sparse dim={len(vocab)}).")
    print(f'Self-test:  python -m preprocessing.embed_and_ingest_corpus --demo-query "Retail CIF Selindo terkini"')
    conn.close()


if __name__ == "__main__":
    main()
