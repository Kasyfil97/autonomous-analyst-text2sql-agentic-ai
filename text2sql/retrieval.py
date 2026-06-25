"""Hybrid (dense + sparse BM25, RRF-fused) retrieval over the KB tables (plan U3/KD4).

Implements the recipe documented in ``RETRIEVAL.md`` §2/§3, parameterized per KB so the
same helper serves ``era_knowledge``, ``schema_tables`` and ``schema_columns`` — each of
which has its own ``*_bm25`` / ``*_bm25_meta`` vocab with a distinct sparse dimension
(read from ``*_bm25_meta``, never hardcoded). Dense vectors come from the BGE-M3
embedding service; the sparse side is encoded locally from the persisted idf vocab.

Uses the least-privilege read-only Postgres role (``pg_config(readonly=True)``).
"""
from __future__ import annotations

import re
import time

import psycopg2

from text2sql.embedding_service import embed_one, pg_config
from text2sql.audit_log import get_logger

_log = get_logger("retrieval")

# Indonesian + English stopwords (verbatim from RETRIEVAL.md so query tokenization
# matches the indexed side).
STOP = {
    "yang", "dan", "untuk", "dengan", "dari", "pada", "ke", "di", "ini", "itu", "atau",
    "juga", "akan", "sudah", "tidak", "ada", "data", "dalam", "saya", "kami", "mohon",
    "bantuan", "terima", "kasih", "the", "a", "an", "of", "to", "for", "and", "or", "in",
    "on", "at", "is", "are", "be", "by", "with", "as", "from", "this", "that", "we", "i",
}

_vocab_cache: dict[str, tuple[dict, dict, int]] = {}


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 2 and w not in STOP]


def _load_vocab(conn, kb: str) -> tuple[dict, dict, int]:
    """Return (vocab{token->idx}, idf{token->idf}, dim) for a KB, cached."""
    if kb in _vocab_cache:
        return _vocab_cache[kb]
    with conn.cursor() as cur:
        cur.execute(f"SELECT token, idx, idf FROM {kb}_bm25")
        vocab, idf = {}, {}
        for token, idx, idf_v in cur.fetchall():
            vocab[token] = idx
            idf[token] = idf_v
        cur.execute(f"SELECT value FROM {kb}_bm25_meta WHERE key = 'dim'")
        dim = int(cur.fetchone()[0])
    _vocab_cache[kb] = (vocab, idf, dim)
    return _vocab_cache[kb]


def encode_query_sparse(question: str, vocab: dict, idf: dict, dim: int) -> str:
    """Encode a question into a pgvector ``sparsevec`` literal ``{idx:w,...}/dim``.

    Query weights are the persisted idf (RETRIEVAL.md). Indices are 1-based ascending;
    an all-out-of-vocab query yields the non-empty placeholder ``{1:0}/dim``.
    """
    weights = {vocab[t]: idf[t] for t in set(tokenize(question)) if t in vocab}
    if not weights:
        return f"{{1:0}}/{dim}"
    body = ",".join(f"{i}:{w:.6f}" for i, w in sorted(weights.items()))
    return "{" + body + "}/" + str(dim)


def _dense_literal(vec) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# Only these KB tables may be searched — guards the f-string table interpolation.
ALLOWED_KBS = {"era_knowledge", "schema_tables", "schema_columns"}


def hybrid_search(kb: str, question: str, *, limit: int = 10, pool: int = 50, conn=None):
    """Hybrid-retrieve from ``kb`` and return ranked rows.

    Returns a list of dicts ``{id, score (RRF), dense_cosine, bm25}`` ordered by RRF
    score desc. ``dense_cosine``/``bm25`` are None when a row was only found by the
    other modality.

    ``kb`` must be one of ``ALLOWED_KBS`` (it is f-string-interpolated into the query);
    no free-text predicate is accepted, so no caller can inject SQL here.
    """
    if kb not in ALLOWED_KBS:
        raise ValueError(f"unknown KB: {kb!r}")

    _log.info("START  | kb=%-20s limit=%d  question=%r", kb, limit, question[:100])
    t0 = time.perf_counter()

    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**pg_config(readonly=True))
    try:
        vocab, idf, dim = _load_vocab(conn, kb)

        tokens = tokenize(question)
        in_vocab = [t for t in set(tokens) if t in vocab]
        _log.debug(
            "sparse  | tokens=%s  in_vocab=%d/%d  dim=%d",
            tokens, len(in_vocab), len(set(tokens)), dim,
        )
        if not in_vocab:
            _log.warning(
                "sparse  | kb=%s — all query tokens outside BM25 vocab; "
                "sparse lane will contribute nothing to RRF ranking",
                kb,
            )

        t_embed = time.perf_counter()
        qd = _dense_literal(embed_one(question))
        _log.debug("embed   | elapsed=%.3fs", time.perf_counter() - t_embed)

        qs = encode_query_sparse(question, vocab, idf, dim)

        sql = f"""
        WITH d AS (
            SELECT id, row_number() OVER (ORDER BY dense <=> %(qd)s::vector) AS rk,
                   1 - (dense <=> %(qd)s::vector) AS cosine
            FROM {kb}
            ORDER BY dense <=> %(qd)s::vector LIMIT %(pool)s),
        s AS (
            SELECT id, row_number() OVER (ORDER BY sparse::sparsevec <#> %(qs)s::sparsevec) AS rk,
                   -(sparse::sparsevec <#> %(qs)s::sparsevec) AS bm25
            FROM {kb}
            ORDER BY sparse::sparsevec <#> %(qs)s::sparsevec LIMIT %(pool)s)
        SELECT COALESCE(d.id, s.id) AS id,
               COALESCE(1.0/(60+d.rk), 0) + COALESCE(1.0/(60+s.rk), 0) AS score,
               d.cosine AS dense_cosine,
               s.bm25 AS bm25
        FROM d FULL OUTER JOIN s ON d.id = s.id
        ORDER BY score DESC LIMIT %(limit)s;
        """
        with conn.cursor() as cur:
            cur.execute(sql, {"qd": qd, "qs": qs, "pool": pool, "limit": limit})
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        elapsed = time.perf_counter() - t0
        if rows:
            top = rows[0]
            _log.info(
                "DONE   | kb=%-20s rows=%d  top_cosine=%.3f  top_bm25=%.4f  "
                "top_rrf=%.4f  elapsed=%.3fs",
                kb, len(rows),
                top.get("dense_cosine") or 0.0,
                top.get("bm25") or 0.0,
                top.get("score") or 0.0,
                elapsed,
            )
        else:
            _log.warning(
                "DONE   | kb=%-20s rows=0 (no results)  elapsed=%.3fs  "
                "→ top_cosine will be 0.0 for this KB",
                kb, elapsed,
            )

        return rows
    finally:
        if own_conn:
            conn.close()


def top_dense_cosine(rows) -> float:
    """Max dense cosine across rows (the coverage signal); 0.0 if none."""
    cosines = [r["dense_cosine"] for r in rows if r.get("dense_cosine") is not None]
    return max(cosines) if cosines else 0.0
