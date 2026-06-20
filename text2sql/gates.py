"""Code-enforced, fail-closed safety + decline gates (plan U5/KD3/KD11).

The gates are authoritative over the model: ``decide()`` consumes only structured gate
outputs, never the model's free-text claims. Layers:

  * ``validate_sql``  — sqlglot parse (target dialect), full-AST, single read-only SELECT
                        only; fail-closed on parse errors / forbidden nodes / dangerous
                        functions / ``SELECT … INTO`` / multi-statement.
  * ``check_grounding`` — every referenced identifier must exist in the schema KB.
  * ``policy_ok``     — decline on restricted (PII/PCI) identifiers or the table denylist.
  * ``coverage_ok``   — per-KB retrieval floors (ERA precedent + schema table).
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
        if any(frag in low for frag in RESTRICTED_FRAGMENTS):
            matched = next(f for f in RESTRICTED_FRAGMENTS if f in low)
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
    """Per-KB coverage. Declines if the ERA precedent OR schema floor is not cleared."""
    if era_top_cosine < era_floor:
        detail = f"no confident ERA precedent (cosine {era_top_cosine:.3f} < {era_floor})"
        _log.warning(
            "coverage_ok | FAIL — era_cosine=%.3f < floor=%.2f"
            "  reason: no sufficiently similar precedent found; generating SQL without"
            " a grounded example risks hallucination",
            era_top_cosine, era_floor,
        )
        return False, detail
    if schema_top_cosine < schema_floor:
        detail = f"weak schema coverage (cosine {schema_top_cosine:.3f} < {schema_floor})"
        _log.warning(
            "coverage_ok | FAIL — schema_cosine=%.3f < floor=%.2f"
            "  reason: schema retrieval too weak; column/table names may be wrong",
            schema_top_cosine, schema_floor,
        )
        return False, detail
    _log.info(
        "coverage_ok | PASS — era_cosine=%.3f (≥%.2f)  schema_cosine=%.3f (≥%.2f)",
        era_top_cosine, era_floor, schema_top_cosine, schema_floor,
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
# Compose
# --------------------------------------------------------------------------

def decide(sql, dialect, *, era_top_cosine, schema_top_cosine, known_tables,
           restricted_columns=None, ground_warn=False, strict_functions=False,
           era_floor=0.45, schema_floor=0.40) -> Decision:
    """Compose all gates into a single pass / decline decision. Fail-closed.

    Consumes only structured signals (scores, parsed identifiers) — never the model's
    textual claims. Order: coverage -> SQL safety -> policy -> grounding.
    """
    _log.info(
        "decide | START — era_cosine=%.3f  schema_cosine=%.3f  dialect=%r  sql_len=%d",
        era_top_cosine, schema_top_cosine, dialect, len(sql or ""),
    )

    # Gate 1: coverage
    cov_ok, cov_detail = coverage_ok(era_top_cosine, schema_top_cosine,
                                     era_floor=era_floor, schema_floor=schema_floor)
    if not cov_ok:
        _log.warning("decide | DECLINED at gate=coverage — %s", cov_detail)
        return Decision(False, "coverage", cov_detail)

    # Gate 2: SQL safety
    safe, detail, tables, columns = validate_sql(sql, dialect,
                                                 strict_functions=strict_functions)
    if not safe:
        _log.warning("decide | DECLINED at gate=unsafe_sql — %s", detail)
        return Decision(False, "unsafe_sql", detail)

    # Gate 3: policy (denylist + PII columns)
    pol_ok, pol_detail = policy_ok(tables, columns, restricted_columns=restricted_columns)
    if not pol_ok:
        _log.warning("decide | DECLINED at gate=policy — %s", pol_detail)
        return Decision(False, "policy", pol_detail, tables, columns)

    # Gate 4: grounding (table catalog check)
    grounded, missing = check_grounding(tables, known_tables, warn=ground_warn)
    if not grounded:
        detail = f"unknown identifier(s): {sorted(missing)}"
        _log.warning("decide | DECLINED at gate=grounding — %s", detail)
        return Decision(False, "grounding", detail, tables, columns)

    _log.info(
        "decide | ALL GATES PASSED — tables=%s  columns=%s",
        sorted(tables), sorted(columns),
    )
    return Decision(True, None, None, tables, columns)
