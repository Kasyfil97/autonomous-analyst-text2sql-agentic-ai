"""U7 tests: CLI formatting + REPL loop (stubbed generate, no live model)."""
from text2sql.agent import Text2SQLResult
from text2sql.cli import format_result, run_repl


# -- formatting --------------------------------------------------------------

def test_format_success_shows_sql_and_sources():
    r = Text2SQLResult(sql="-- UNVERIFIED\nSELECT 1", explanation="does x",
                       dialect="SparkSQL", precedent_ids=["ERA25-1685"],
                       tables_used=["t"])
    out = format_result(r)
    assert "SELECT 1" in out and "SparkSQL" in out
    assert "ERA25-1685" in out and "does x" in out


def test_format_decline_shows_missing_no_sql():
    r = Text2SQLResult(declined=True, missing="no precedent for request")
    out = format_result(r)
    assert "Cannot produce SQL" in out and "no precedent" in out
    assert "SQL:" not in out


# -- REPL loop ---------------------------------------------------------------

class _Inputs:
    def __init__(self, items):
        self._it = iter(items)

    def __call__(self, _prompt=""):
        return next(self._it)


def test_repl_runs_question_then_exits():
    calls = []
    captured = []

    def fake_generate(q):
        calls.append(q)
        return Text2SQLResult(sql="SELECT 1", dialect="SparkSQL")

    run_repl(generate=fake_generate,
             in_fn=_Inputs(["what is x?", "exit"]),
             out=captured.append)
    assert calls == ["what is x?"]
    assert any("SELECT 1" in c for c in captured)
    assert any("Bye!" in c for c in captured)


def test_repl_skips_empty_input():
    calls = []
    run_repl(generate=lambda q: calls.append(q) or Text2SQLResult(declined=True),
             in_fn=_Inputs(["", "  ", "quit"]),
             out=lambda *_: None)
    assert calls == []  # blank lines ignored, then quit


def test_repl_survives_generate_error():
    captured = []

    def boom(_q):
        raise RuntimeError("model down")

    run_repl(generate=boom, in_fn=_Inputs(["q", "exit"]), out=captured.append)
    assert any("model down" in c for c in captured)


def test_repl_handles_eof():
    captured = []

    def eof(_prompt=""):
        raise EOFError

    run_repl(generate=lambda q: None, in_fn=eof, out=captured.append)
    assert any("Bye!" in c for c in captured)
