"""U5 tests: fail-closed safety, grounding, policy, coverage, output scan."""
import pytest

from text2sql import gates as G


# -- validate_sql: happy + fail-closed safety --------------------------------

def test_valid_select_passes():
    safe, detail, tables, cols = G.validate_sql(
        "SELECT a, b FROM t WHERE status IN (2,4,8)", "spark")
    assert safe and "t" in tables and {"a", "b"} <= cols


@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "DELETE FROM t",
    "UPDATE t SET x = 1",
    "INSERT INTO t VALUES (1)",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
    "SELECT 1; DROP TABLE t",
    "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
    "SELECT a INTO admin_copy FROM accounts",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(10)",
])
def test_unsafe_sql_rejected(sql):
    safe, detail, _, _ = G.validate_sql(sql, "spark")
    assert not safe, f"should reject: {sql} ({detail})"


def test_unparseable_is_fail_closed():
    safe, detail, _, _ = G.validate_sql("SELECT FROM WHERE )(", "spark")
    assert not safe


def test_strict_functions_rejects_unknown():
    # an unknown UDF parses to Anonymous; strict mode declines
    safe, _, _, _ = G.validate_sql("SELECT my_weird_udf(x) FROM t", "spark",
                                   strict_functions=True)
    assert not safe


# -- grounding ---------------------------------------------------------------

def test_grounding_blocks_unknown_identifier():
    ok, missing = G.check_grounding({"real_table", "hallucinated"},
                                    {"real_table"}, warn=False)
    assert not ok and "hallucinated" in missing


def test_grounding_warn_mode_does_not_block():
    ok, missing = G.check_grounding({"hallucinated"}, set(), warn=True)
    assert ok and "hallucinated" in missing


# -- policy ------------------------------------------------------------------

def test_policy_declines_pii_column():
    ok, detail = G.policy_ok({"accounts"}, {"id", "ktp_number"})
    assert not ok and "ktp_number" in detail


def test_policy_declines_denylisted_table():
    ok, detail = G.policy_ok({"era_tickets"}, {"id"})
    assert not ok and "era_tickets" in detail


def test_policy_allows_clean_identifiers():
    ok, _ = G.policy_ok({"accounts"}, {"branch", "amount"})
    assert ok


# -- coverage ----------------------------------------------------------------

def test_coverage_declines_below_floor():
    ok, detail = G.coverage_ok(0.10, 0.90)
    assert not ok and "ERA precedent" in detail


def test_coverage_passes_above_floors():
    ok, _ = G.coverage_ok(0.80, 0.70)
    assert ok


# -- output scan -------------------------------------------------------------

def test_scan_output_flags_destructive_snippet():
    clean, detail = G.scan_output("Here is the query. Also you could DROP TABLE x.")
    assert not clean and "DROP" in detail


def test_scan_output_clean_select_ok():
    clean, _ = G.scan_output("This SELECT returns the teller totals per branch.")
    assert clean


# -- decide() composition (gates authoritative) ------------------------------

def test_decide_passes_clean_grounded_query():
    d = G.decide("SELECT branch FROM trx_teller", "spark",
                 era_top_cosine=0.8, schema_top_cosine=0.7,
                 known_tables={"trx_teller"})
    assert d.ok and "trx_teller" in d.referenced_tables


def test_decide_declines_ungrounded_even_if_model_claims_ok():
    # model produced a query referencing a table that was never in the catalog
    d = G.decide("SELECT x FROM ghost_table", "spark",
                 era_top_cosine=0.8, schema_top_cosine=0.7,
                 known_tables={"real_table"})
    assert not d.ok and d.reason == "grounding"


def test_decide_declines_on_low_coverage_first():
    d = G.decide("SELECT 1 FROM t", "spark",
                 era_top_cosine=0.1, schema_top_cosine=0.7, known_tables={"t"})
    assert not d.ok and d.reason == "coverage"


def test_decide_declines_unsafe_sql():
    d = G.decide("DROP TABLE t", "spark",
                 era_top_cosine=0.8, schema_top_cosine=0.7, known_tables={"t"})
    assert not d.ok and d.reason == "unsafe_sql"
