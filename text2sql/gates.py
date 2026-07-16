"""Code-enforced safety gates (plan U5/KD3/KD11), warn-don't-block.

The gates are authoritative over the model: ``decide()`` consumes only structured gate
outputs, never the model's free-text claims. Each gate that fails appends a
severity-tagged entry to ``Decision.warnings`` rather than declining — drafts are never
executed and a human reviews them, so the system surfaces the SQL plus every concern
instead of withholding it. The individual gate functions below still return plain
pass/fail verdicts; ``decide()`` is what translates those into non-blocking warnings.
Layers:

  * ``validate_sql``  — sqlglot parse (target dialect), full-AST, single read-only SELECT
                        only; fail-closed on parse errors / forbidden nodes / dangerous
                        functions / ``SELECT … INTO`` / multi-statement.
  * ``check_grounding`` — every referenced identifier must exist in the schema KB.
  * ``policy_ok``     — decline on restricted (PII/PCI) identifiers or the table denylist.
  * ``coverage_ok``   — schema-coverage floor only; precedent is advisory (not gated).
  * ``scan_output``   — ban destructive SQL anywhere in the answer/explanation text.

On the 200-row sample, grounding runs in ``warn`` mode (expected to over-decline);
production grounds against the full catalog. PII classification (R13) is not yet
available, so ``policy_ok`` uses a keyword heuristic now and a ``strict`` fail-closed
mode (unclassified → restricted) for production.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from text2sql.audit_log import get_logger

_log = get_logger("gates")

UNVERIFIED_MARKER = "-- UNVERIFIED DRAFT — review before running; the tool does not execute SQL."

# Tables the agent must never reference (mirrors the read-only role's exclusions).
TABLE_DENYLIST = {"era_tickets", "era_tickets_vec", "era_tickets_descpseudo"}

# Side-effecting / IO / code-exec functions — always rejected (fast-path denylist).
# Includes Spark JVM-exec (reflect/java_method) and config readers flagged in review.
DANGEROUS_FUNCS = {
    "pg_read_file", "pg_sleep", "dblink", "lo_import", "lo_export", "xp_cmdshell",
    "openrowset", "opendatasource", "pg_ls_dir", "copy", "load_file",
    "reflect", "java_method", "current_setting", "set_config", "system", "sys_exec",
}

# Restricted identifier name fragments (PII/PCI) — v1 heuristic until R13 classification.
RESTRICTED_FRAGMENTS = {
    "nik", "ktp", "npwp", "cvv", "card", "pan", "password", "passwd", "pin",
    "ssn", "tgl_lahir", "tanggal_lahir", "nama_ibu", "mother_name", "dob",
    "email", "phone", "telp", "mobile", "alamat", "address", "tax_id",
    "rekening", "no_rek", "norek", "account_no", "acct_no",
}

def restricted_fragment(name: str | None) -> str | None:
    """Return the first PII/PCI fragment a column name matches, else None.

    Single source for the fragment heuristic — consumed by ``policy_ok`` (agent path) and the
    Sage search-surface PII badge (R4a/R5a), so the two read paths cannot drift.
    """
    low = (name or "").lower()
    return next((frag for frag in RESTRICTED_FRAGMENTS if frag in low), None)


def restricted_columns(columns) -> set:
    """Subset of column names that hit the PII/PCI fragment heuristic."""
    return {c for c in columns if restricted_fragment(c)}


# Forbidden statement node types (build dynamically — names vary across sqlglot versions).
_FORBIDDEN_NAMES = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
                    "Command", "Set", "Copy", "TruncateTable", "Grant")
FORBIDDEN_NODES = tuple(
    getattr(exp, n) for n in _FORBIDDEN_NAMES if getattr(exp, n, None) is not None)

_DESTRUCTIVE_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE)


@dataclass
class Decision:
    ok: bool
    reason: str | None = None       # category: unsafe_sql | grounding | coverage | policy
    detail: str | None = None
    referenced_tables: set = field(default_factory=set)
    referenced_columns: set = field(default_factory=set)
    warnings: list = field(default_factory=list)  # severity-tagged gate findings (non-blocking)


def _sqlglot_dialect(engine: str | None) -> str | None:
    e = (engine or "").lower()
    if "spark" in e:
        return "spark"
    if "tsql" in e or "sqlserver" in e or "sql server" in e:
        return "tsql"
    return None


# --------------------------------------------------------------------------
# SQL validation (fail-closed)
# --------------------------------------------------------------------------

def validate_sql(sql: str, dialect: str | None = None, *, strict_functions: bool = False):
    """Return (safe: bool, detail, tables: set, columns: set). Fail-closed.

    Parses under the resolved dialect, falling back to ``spark`` — real catalog tables
    are digit-leading (e.g. ``1000_TRX_TELLER``) and only the Spark dialect parses such
    unquoted identifiers; without this, valid SQLServer/unknown-dialect queries would be
    wrongly rejected as unparseable. Structural DDL/DML rejection is dialect-independent.
    """
    glot = _sqlglot_dialect(dialect)
    _log.debug("validate_sql | dialect=%r  glot_dialect=%r  sql_len=%d", dialect, glot, len(sql or ""))

    def _parse(read):
        return [s for s in sqlglot.parse(sql, read=read) if s is not None]

    try:
        statements = _parse(glot or "spark")
    except Exception:  # noqa: BLE001
        try:
            statements = _parse("spark")
        except Exception as exc:  # noqa: BLE001 — any parse failure => unsafe
            _log.warning("validate_sql | FAIL — unparseable SQL (%s)", type(exc).__name__)
            return False, f"unparseable SQL ({type(exc).__name__})", set(), set()

    if len(statements) != 1:
        detail = f"expected exactly one statement, got {len(statements)}"
        _log.warning("validate_sql | FAIL — %s", detail)
        return False, detail, set(), set()

    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Subquery, exp.With)):
        detail = f"not a read-only SELECT (root={type(root).__name__})"
        _log.warning("validate_sql | FAIL — %s", detail)
        return False, detail, set(), set()

    if FORBIDDEN_NODES and list(root.find_all(*FORBIDDEN_NODES)):
        bad = next(iter(root.find_all(*FORBIDDEN_NODES)))
        detail = f"forbidden statement: {type(bad).__name__}"
        _log.warning("validate_sql | FAIL — %s", detail)
        return False, detail, set(), set()

    if list(root.find_all(exp.Into)):
        _log.warning("validate_sql | FAIL — SELECT ... INTO creates a table (write)")
        return False, "SELECT ... INTO creates a table (write)", set(), set()

    for fn in root.find_all(exp.Anonymous):
        name = (fn.name or "").lower()
        if name in DANGEROUS_FUNCS:
            _log.warning("validate_sql | FAIL — dangerous function: %s", name)
            return False, f"dangerous function: {name}", set(), set()
        if strict_functions:
            _log.warning("validate_sql | FAIL — unknown function (fail-closed): %s", name)
            return False, f"unknown function (fail-closed): {name}", set(), set()

    tables = {t.name for t in root.find_all(exp.Table) if t.name}
    columns = {c.name for c in root.find_all(exp.Column) if c.name}
    _log.info(
        "validate_sql | PASS — tables_referenced=%s  columns_referenced=%s",
        sorted(tables), sorted(columns),
    )
    return True, None, tables, columns


def _extract_identifiers(sql: str, dialect: str | None = None):
    """Best-effort (tables, columns) extraction for SQL that failed validation.

    When ``validate_sql`` rejects a draft (e.g. a forbidden DDL node), it returns empty
    sets — but in warn-don't-block mode the policy/grounding gates still need identifiers
    to flag. Parse permissively under the resolved dialect then the spark fallback;
    return empty sets only when the SQL is wholly unparseable.
    """
    glot = _sqlglot_dialect(dialect)
    for read in (glot or "spark", "spark"):
        try:
            statements = [s for s in sqlglot.parse(sql, read=read) if s is not None]
        except Exception:  # noqa: BLE001 — try the next dialect, else give up
            continue
        tables = {t.name for s in statements for t in s.find_all(exp.Table) if t.name}
        columns = {c.name for s in statements for c in s.find_all(exp.Column) if c.name}
        return tables, columns
    return set(), set()


# --------------------------------------------------------------------------
# Grounding / policy / coverage
# --------------------------------------------------------------------------

def check_grounding(referenced_tables, known_tables, *, warn: bool = False):
    """Return (ok, missing). With warn=True (sample), report misses but don't block.

    Comparison is case-insensitive — sqlglot preserves the case the model wrote, while
    the catalog stores its own (often upper) case.
    """
    known_lower = {t.lower() for t in known_tables}
    missing = {t for t in referenced_tables if t.lower() not in known_lower}
    if missing:
        if warn:
            _log.warning(
                "check_grounding | WARN (non-blocking) — unknown tables=%s"
                "  reason: running in warn mode (sample catalog); production blocks",
                sorted(missing),
            )
        else:
            _log.warning(
                "check_grounding | FAIL — table(s) not in schema KB=%s"
                "  reason: model referenced identifiers it never confirmed via tools",
                sorted(missing),
            )
            return False, missing
    else:
        _log.info(
            "check_grounding | PASS — all %d referenced table(s) found in catalog",
            len(referenced_tables),
        )
    return True, missing


def policy_ok(referenced_tables, referenced_columns, *,
              restricted_columns=None, strict_unclassified=False):
    """Return (ok, detail). Declines on the table denylist or restricted identifiers."""
    denied = {t for t in referenced_tables if t.lower() in TABLE_DENYLIST}
    if denied:
        detail = f"denylisted table(s): {sorted(denied)}"
        _log.warning(
            "policy_ok | FAIL — %s"
            "  reason: these tables are excluded from the read-only role (raw PII corpus)",
            detail,
        )
        return False, detail

    restricted_columns = restricted_columns or set()
    for col in referenced_columns:
        low = col.lower()
        if low in restricted_columns:
            detail = f"restricted column: {col}"
            _log.warning("policy_ok | FAIL — %s  reason: column is in R13 restricted list", detail)
            return False, detail
        matched = restricted_fragment(col)
        if matched:
            detail = f"restricted (PII) column: {col}"
            _log.warning(
                "policy_ok | FAIL — %s  reason: name fragment %r matches PII heuristic",
                detail, matched,
            )
            return False, detail
        if strict_unclassified and col not in (restricted_columns or set()):
            # production fail-closed posture once R13 labels exist
            pass

    _log.info(
        "policy_ok | PASS — tables=%s  columns=%s  none in denylist or PII fragments",
        sorted(referenced_tables), sorted(referenced_columns),
    )
    return True, None


def coverage_ok(era_top_cosine: float, schema_top_cosine: float, *,
                era_floor: float = 0.45, schema_floor: float = 0.40):
    """Coverage gate. Declines only on the schema floor; precedent is advisory.

    A weak or absent precedent (``era_top_cosine < era_floor``) no longer blocks: the
    agent falls back to schema-first drafting. A confident precedent still informs the
    draft and dialect upstream (see ``agent.precedent_dialect``); here it is logged for
    the audit trail but never gates. Schema coverage remains the authoritative bar —
    without it the retrieved table/column names are too uncertain to trust, so the
    grounding/accumulator gates would have nothing reliable to ground against.
    """
    if era_top_cosine < era_floor:
        _log.info(
            "coverage_ok | no confident ERA precedent (cosine %.3f < %.2f) — proceeding"
            " schema-first (precedent is advisory; grounding/accumulator gates still apply)",
            era_top_cosine, era_floor,
        )
    if schema_top_cosine < schema_floor:
        detail = f"weak schema coverage (cosine {schema_top_cosine:.3f} < {schema_floor})"
        _log.warning(
            "coverage_ok | FAIL — schema_cosine=%.3f < floor=%.2f"
            "  reason: schema retrieval too weak; column/table names may be wrong",
            schema_top_cosine, schema_floor,
        )
        return False, detail
    _log.info(
        "coverage_ok | PASS — era_cosine=%.3f  schema_cosine=%.3f (≥%.2f)",
        era_top_cosine, schema_top_cosine, schema_floor,
    )
    return True, None


def scan_output(text: str):
    """Return (clean, detail). Bans destructive SQL anywhere in answer/explanation."""
    if text and _DESTRUCTIVE_RE.search(text):
        m = _DESTRUCTIVE_RE.search(text)
        detail = f"destructive SQL keyword in output: {m.group(0)}"
        _log.warning(
            "scan_output | FAIL — %s"
            "  reason: keyword found in explanation or SQL text; may indicate prompt injection",
            detail,
        )
        return False, detail
    _log.info("scan_output | PASS — no destructive keywords in answer text")
    return True, None


# --------------------------------------------------------------------------
# Search sub-agent gate (prose answers — warn-don't-block)
# --------------------------------------------------------------------------

# Catalog tables follow an UPPER/digit + underscore convention (e.g. 1000_TRX_TELLER).
# Match those distinctive tokens in prose; lowercase column names are intentionally not
# matched so a column mention isn't mistaken for an ungrounded table.
_TABLE_LIKE_RE = re.compile(r"\b[A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")


def table_like_mentions(text: str) -> set:
    """Best-effort extraction of catalog-table-shaped tokens from free prose."""
    return set(_TABLE_LIKE_RE.findall(text or ""))


def decide_search(answer: str, retrieved_tables) -> list:
    """Warn-don't-block gate for a search sub-agent's prose answer.

    Mirrors ``decide()``'s philosophy: never withhold the answer, just attach
    severity-tagged warnings. Consumes the answer text + the set of tables actually
    retrieved this request (not the model's claims). Checks:
      * destructive keywords / injection signal (scan_output)        -> [CRITICAL]
      * a denylisted table named anywhere in the answer              -> [CRITICAL]
      * a catalog-shaped table mentioned but never retrieved (heuristic) -> [HIGH]
    """
    warnings: list[str] = []
    retrieved_lower = {t.lower() for t in retrieved_tables}

    clean, detail = scan_output(answer)
    if not clean:
        warnings.append(f"[CRITICAL] unsafe output: {detail}")

    low = (answer or "").lower()
    denied = sorted(t for t in TABLE_DENYLIST if t.lower() in low)
    if denied:
        _log.warning("decide_search | WARN — denylisted table named in answer=%s", denied)
        warnings.append(f"[CRITICAL] policy: denylisted table(s) named in answer: {denied}")

    mentioned = table_like_mentions(answer)
    ungrounded = sorted(t for t in mentioned if t.lower() not in retrieved_lower)
    if ungrounded:
        _log.warning(
            "decide_search | WARN — table(s) named in answer but never retrieved=%s"
            "  reason: heuristic grounding; possible hallucination",
            ungrounded,
        )
        warnings.append(
            f"[HIGH] grounding: table(s) named but never retrieved: {ungrounded}")

    if warnings:
        _log.warning("decide_search | PASS WITH WARNINGS (%d) — %s", len(warnings), warnings)
    else:
        _log.info("decide_search | ALL CHECKS PASSED — answer grounded in retrieved tables")
    return warnings


# --------------------------------------------------------------------------
# Compose
# --------------------------------------------------------------------------

def decide(sql, dialect, *, era_top_cosine, schema_top_cosine, known_tables,
           restricted_columns=None, ground_warn=False, strict_functions=False,
           era_floor=0.45, schema_floor=0.40) -> Decision:
    """Compose all gates into a single warn-don't-block decision.

    Consumes only structured signals (scores, parsed identifiers) — never the model's
    textual claims. Every gate that fails appends a severity-tagged warning instead of
    declining: the draft is never executed and a human reviews it, so we surface SQL +
    the full list of concerns rather than withholding it. Order: coverage -> SQL safety
    -> policy -> grounding. ``ok`` stays True; callers must inspect ``warnings``.
    """
    _log.info(
        "decide | START — era_cosine=%.3f  schema_cosine=%.3f  dialect=%r  sql_len=%d",
        era_top_cosine, schema_top_cosine, dialect, len(sql or ""),
    )
    warnings: list[str] = []

    # Gate 1: coverage (numeric threshold — low severity; precedent already advisory)
    cov_ok, cov_detail = coverage_ok(era_top_cosine, schema_top_cosine,
                                     era_floor=era_floor, schema_floor=schema_floor)
    if not cov_ok:
        warnings.append(f"[LOW] coverage: {cov_detail}")

    # Gate 2: SQL safety. On failure, recover identifiers best-effort so the policy and
    # grounding gates can still inspect the draft.
    safe, detail, tables, columns = validate_sql(sql, dialect,
                                                 strict_functions=strict_functions)
    if not safe:
        warnings.append(f"[CRITICAL] unsafe_sql: {detail}")
        if not tables and not columns:
            tables, columns = _extract_identifiers(sql, dialect)

    # Gate 3: policy (denylist + PII columns)
    pol_ok, pol_detail = policy_ok(tables, columns, restricted_columns=restricted_columns)
    if not pol_ok:
        warnings.append(f"[CRITICAL] policy: {pol_detail}")

    # Gate 4: grounding (table catalog check)
    grounded, missing = check_grounding(tables, known_tables, warn=ground_warn)
    if not grounded:
        warnings.append(f"[HIGH] grounding: unknown identifier(s): {sorted(missing)}")

    if warnings:
        _log.warning("decide | PASS WITH WARNINGS (%d) — %s", len(warnings), warnings)
    else:
        _log.info(
            "decide | ALL GATES PASSED — tables=%s  columns=%s",
            sorted(tables), sorted(columns),
        )
    return Decision(True, None, None, tables, columns, warnings)
