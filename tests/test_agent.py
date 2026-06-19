"""U6 tests: result parsing + gates-authoritative decline logic (no live model)."""
import psycopg2
import pytest

from embedding_service import pg_config
from text2sql import agent as A
from text2sql.agent import Text2SQLResult, parse_result, apply_gates
from text2sql.tools import RetrievalContext


# -- parse_result ------------------------------------------------------------

def test_parse_plain_json():
    raw = '{"sql":"SELECT 1","explanation":"e","dialect":"SparkSQL","declined":false}'
    r = parse_result(raw)
    assert r.sql == "SELECT 1" and r.dialect == "SparkSQL" and not r.declined


def test_parse_json_with_reasoning_and_fence():
    raw = ('<reasoning>thinking</reasoning>```json\n'
           '{"sql":"SELECT a FROM t","declined":false}\n```')
    r = parse_result(raw)
    assert r.sql == "SELECT a FROM t"


def test_parse_unparseable_declines():
    r = parse_result("I cannot help with that.")
    assert r.declined


def test_parse_declined_passthrough():
    raw = '{"sql":"","declined":true,"missing":"no precedent"}'
    r = parse_result(raw)
    assert r.declined and r.missing == "no precedent"


# -- apply_gates (gates authoritative) ---------------------------------------

class FakeCtx:
    """Stand-in for RetrievalContext with controllable signals."""
    def __init__(self, era=0.8, schema=0.7, retrieved=None, era_id="ERA22-1000"):
        self._era = era
        self._schema = schema
        self.retrieved_tables = retrieved or set()
        self.calls = [{"kb": "era_knowledge", "ids": [era_id]}] if era_id else []

    def era_top_cosine(self):
        return self._era

    def schema_top_cosine(self):
        return self._schema


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**pg_config(readonly=True))
    yield c
    c.close()


def test_apply_gates_passes_grounded_retrieved_query(conn):
    # 1000_TRX_TELLER is a real table referenced by ERA22-1000
    ctx = FakeCtx(retrieved={"trx_teller"})
    r = Text2SQLResult(sql="SELECT branch FROM trx_teller", dialect="SparkSQL")
    # known_tables includes 'trx_teller' for this test via monkeypatch-free seeding:
    A._known_tables_cache = {"trx_teller"}
    out = apply_gates(r, ctx, conn, ground_warn=True)
    A._known_tables_cache = None
    assert not out.declined
    assert out.sql.startswith(A.gates.UNVERIFIED_MARKER)
    assert "trx_teller" in out.tables_used


def test_apply_gates_declines_table_never_retrieved(conn):
    # model claims a table it never looked up -> accumulator authority declines
    ctx = FakeCtx(retrieved=set())  # nothing retrieved
    A._known_tables_cache = {"ghost"}
    r = Text2SQLResult(sql="SELECT x FROM ghost", dialect="SparkSQL")
    out = apply_gates(r, ctx, conn, ground_warn=True)
    A._known_tables_cache = None
    assert out.declined and "never retrieved" in out.missing


def test_apply_gates_declines_low_coverage(conn):
    ctx = FakeCtx(era=0.1, schema=0.7, retrieved={"t"})
    r = Text2SQLResult(sql="SELECT 1 FROM t", dialect="SparkSQL")
    out = apply_gates(r, ctx, conn, ground_warn=True)
    assert out.declined and "coverage" in out.missing


def test_apply_gates_declines_unsafe_sql(conn):
    ctx = FakeCtx(retrieved={"t"})
    A._known_tables_cache = {"t"}
    r = Text2SQLResult(sql="DROP TABLE t", dialect="SparkSQL")
    out = apply_gates(r, ctx, conn, ground_warn=True)
    A._known_tables_cache = None
    assert out.declined and "unsafe_sql" in out.missing


def test_apply_gates_no_sql_declines(conn):
    ctx = FakeCtx(retrieved=set())
    out = apply_gates(Text2SQLResult(sql=None), ctx, conn)
    assert out.declined
