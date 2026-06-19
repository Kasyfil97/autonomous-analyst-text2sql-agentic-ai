"""U3 tests: hybrid retrieval against the live KB.

Requires the Postgres KB (localhost:5433) + the BGE-M3 embedding endpoint reachable.
Asserts the rankings RETRIEVAL.md verified, plus encoding edge cases.
"""
import psycopg2
import pytest

from embedding_service import pg_config
from text2sql import retrieval as R


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**pg_config(readonly=True))
    yield c
    c.close()


# -- pure encoding (no DB) ---------------------------------------------------

def test_tokenize_drops_stopwords_and_short():
    toks = R.tokenize("Mohon bantuan data rekening simpanan TL506")
    assert "mohon" not in toks and "data" not in toks  # stopwords
    assert "rekening" in toks and "simpanan" in toks and "tl506" in toks


def test_encode_query_sparse_empty_is_placeholder():
    lit = R.encode_query_sparse("zzz", {}, {}, 5810)
    assert lit == "{1:0}/5810"  # valid non-empty sparsevec literal


def test_encode_query_sparse_ascending_indices():
    vocab = {"alpha": 5, "beta": 2}
    idf = {"alpha": 1.0, "beta": 2.0}
    lit = R.encode_query_sparse("alpha beta", vocab, idf, 10)
    # 1-based ascending indices, dim suffix
    assert lit.endswith("/10")
    inner = lit[1:lit.index("}")]
    idxs = [int(p.split(":")[0]) for p in inner.split(",")]
    assert idxs == sorted(idxs)


# -- live KB integration -----------------------------------------------------

def test_dim_read_from_meta_matches_table(conn):
    # dim is the meta source of truth, consistent with the live sparse column; not a
    # hardcoded constant (sample values change on full-catalog re-ingest).
    _, _, dim_era = R._load_vocab(conn, "era_knowledge")
    with conn.cursor() as cur:
        cur.execute("SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'era_knowledge'::regclass AND attname = 'sparse'")
        # for sparsevec, the declared dimension is stored in atttypmod
        typmod = cur.fetchone()[0]
    assert typmod == dim_era
    # schema KBs have their own (different) dims
    _, _, dim_tbl = R._load_vocab(conn, "schema_tables")
    assert dim_tbl != dim_era


def test_hybrid_ranks_known_era_cases(conn):
    rows = R.hybrid_search("era_knowledge", "absensi pegawai Ciamis", limit=10, conn=conn)
    ids = [r["id"] for r in rows]
    assert "ERA26-241" in ids[:5]

    rows2 = R.hybrid_search("era_knowledge",
                            "penarikan data Report TL506 UKO Non Layanan Terbatas",
                            limit=10, conn=conn)
    assert "ERA25-1685" in [r["id"] for r in rows2][:5]


def test_hybrid_ranks_schema_table(conn):
    rows = R.hybrid_search("schema_tables", "rekening simpanan harian saldo nasabah",
                           limit=10, conn=conn)
    assert any("ddmast" in r["id"] for r in rows[:5])


def test_results_carry_scores(conn):
    rows = R.hybrid_search("era_knowledge", "data transaksi teller", limit=5, conn=conn)
    assert rows and "score" in rows[0]
    assert R.top_dense_cosine(rows) > 0.0
