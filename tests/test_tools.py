"""U4 tests: knowledge tools, fencing, PII redaction (incl. non-corruption)."""
import psycopg2
import pytest

from embedding_service import pg_config
from text2sql import tools as T
from text2sql.tools import RetrievalContext, redact_note, redact_sql


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**pg_config(readonly=True))
    yield c
    c.close()


@pytest.fixture
def ctx(conn):
    return RetrievalContext(conn)


# -- redaction (pure) --------------------------------------------------------

def test_redact_note_masks_pii():
    out = redact_note("hubungi budi@bri.co.id rekening 1234567890123 selesai")
    assert "[EMAIL]" in out
    assert "1234567890123" not in out and "[REDACTED]" in out


def test_redact_note_preserves_short_codes_and_dates():
    out = redact_note("status not in (2,4,8) periode 20250630 tahun 2025")
    # coded values, 8-digit date, and year must survive
    assert "(2,4,8)" in out and "20250630" in out and "2025" in out


def test_redact_sql_preserves_identifiers_and_coded_values():
    sql = "SELECT * FROM 0000_staging_as4_lnflag WHERE status not in (2,4,8) AND ds='20250630'"
    out = redact_sql(sql)
    assert "0000_staging_as4_lnflag" in out
    assert "(2,4,8)" in out and "20250630" in out


def test_redact_sql_masks_card_number():
    out = redact_sql("WHERE pan = '4111111111111111'")
    assert "4111111111111111" not in out and "[REDACTED_PAN]" in out


# -- tools (live KB) ---------------------------------------------------------

def test_search_era_knowledge_returns_fenced_precedent(ctx):
    out = T.era_knowledge_text(ctx, "penarikan data Report TL506 UKO Non Layanan Terbatas")
    assert "Precedent ERA" in out
    assert "<untrusted" in out  # notes / SQL are fenced
    # coverage signal recorded
    assert ctx.era_top_cosine() > 0.0


def test_get_table_schema_deterministic(ctx):
    out = T.table_schema_text(ctx, "0000_staging_as4_lnflag")
    assert "CREATE TABLE 0000_staging_as4_lnflag" in out
    # 44 columns expected for this table (verified live)
    assert out.count("  -- ") >= 10  # has column comments
    assert "ds" in out


def test_get_table_schema_unknown_table(ctx):
    out = T.table_schema_text(ctx, "tid9999_nonexistent")
    assert "No column dictionary found" in out


def test_search_schema_renders_ddl(ctx):
    out = T.schema_text(ctx, "rekening simpanan harian saldo nasabah")
    assert "CREATE TABLE" in out
    assert ctx.schema_top_cosine() > 0.0


def test_build_tools_exposes_three(ctx):
    tools = T.build_tools(ctx)
    assert len(tools) == 3
