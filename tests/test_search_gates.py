"""Unit tests for the search sub-agent gate (warn-don't-block). No DB required."""
from text2sql import gates as G


def test_table_like_mentions_extracts_catalog_tokens():
    found = G.table_like_mentions(
        "Tabel 1000_TRX_TELLER menyimpan data, lihat juga 2000_TRX_ATM.")
    assert found == {"1000_TRX_TELLER", "2000_TRX_ATM"}


def test_table_like_mentions_ignores_lowercase_columns():
    # lowercase column-style names must not be mistaken for tables
    assert G.table_like_mentions("kolom branch_code dan trx_amount") == set()


def test_decide_search_clean_grounded_answer_has_no_warnings():
    answer = "Table 1000_TRX_TELLER punya kolom branch dan amount."
    assert G.decide_search(answer, {"1000_TRX_TELLER"}) == []


def test_decide_search_warns_ungrounded_table():
    answer = "Ada table 9999_GHOST_TABLE yang relevan."
    warnings = G.decide_search(answer, {"1000_TRX_TELLER"})
    assert any("grounding" in w and "9999_GHOST_TABLE" in w for w in warnings)


def test_decide_search_warns_denylisted_table():
    answer = "Data itu ada di era_tickets."
    warnings = G.decide_search(answer, set())
    assert any("policy" in w and "era_tickets" in w for w in warnings)
    assert any("CRITICAL" in w for w in warnings)


def test_decide_search_warns_destructive_keyword():
    answer = "You could DROP TABLE x to clean up."
    warnings = G.decide_search(answer, set())
    assert any("unsafe output" in w and "DROP" in w for w in warnings)


def test_decide_search_accumulates_multiple_warnings():
    answer = "Lihat era_tickets dan table 9999_GHOST_TABLE."
    warnings = G.decide_search(answer, set())
    assert len(warnings) >= 2
