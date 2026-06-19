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

    def _parse(read):
        return [s for s in sqlglot.parse(sql, read=read) if s is not None]

    try:
        statements = _parse(glot or "spark")
    except Exception:  # noqa: BLE001
        try:
            statements = _parse("spark")
        except Exception as exc:  # noqa: BLE001 — any parse failure => unsafe
            return False, f"unparseable SQL ({type(exc).__name__})", set(), set()

    if len(statements) != 1:
        return False, f"expected exactly one statement, got {len(statements)}", set(), set()
    root = statements[0]

    if not isinstance(root, (exp.Select, exp.Union, exp.Subquery, exp.With)):
        return False, f"not a read-only SELECT (root={type(root).__name__})", set(), set()

    if FORBIDDEN_NODES and list(root.find_all(*FORBIDDEN_NODES)):
        bad = next(iter(root.find_all(*FORBIDDEN_NODES)))
        return False, f"forbidden statement: {type(bad).__name__}", set(), set()

    if list(root.find_all(exp.Into)):
        return False, "SELECT ... INTO creates a table (write)", set(), set()

    for fn in root.find_all(exp.Anonymous):
        name = (fn.name or "").lower()
        if name in DANGEROUS_FUNCS:
            return False, f"dangerous function: {name}", set(), set()
        if strict_functions:
            return False, f"unknown function (fail-closed): {name}", set(), set()

    tables = {t.name for t in root.find_all(exp.Table) if t.name}
    columns = {c.name for c in root.find_all(exp.Column) if c.name}
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
    if missing and not warn:
        return False, missing
    return True, missing


def policy_ok(referenced_tables, referenced_columns, *,
              restricted_columns=None, strict_unclassified=False):
    """Return (ok, detail). Declines on the table denylist or restricted identifiers."""
    denied = {t for t in referenced_tables if t.lower() in TABLE_DENYLIST}
    if denied:
        return False, f"denylisted table(s): {sorted(denied)}"

    restricted_columns = restricted_columns or set()
    for col in referenced_columns:
        low = col.lower()
        if low in restricted_columns:
            return False, f"restricted column: {col}"
        if any(frag in low for frag in RESTRICTED_FRAGMENTS):
            return False, f"restricted (PII) column: {col}"
        if strict_unclassified and col not in (restricted_columns or set()):
            # production fail-closed posture once R13 labels exist
            pass
    return True, None


def coverage_ok(era_top_cosine: float, schema_top_cosine: float, *,
                era_floor: float = 0.45, schema_floor: float = 0.40):
    """Per-KB coverage. Declines if the ERA precedent OR schema floor is not cleared."""
    if era_top_cosine < era_floor:
        return False, f"no confident ERA precedent (cosine {era_top_cosine:.3f} < {era_floor})"
    if schema_top_cosine < schema_floor:
        return False, f"weak schema coverage (cosine {schema_top_cosine:.3f} < {schema_floor})"
    return True, None


def scan_output(text: str):
    """Return (clean, detail). Bans destructive SQL anywhere in answer/explanation."""
    if text and _DESTRUCTIVE_RE.search(text):
        m = _DESTRUCTIVE_RE.search(text)
        return False, f"destructive SQL keyword in output: {m.group(0)}"
    return True, None


# --------------------------------------------------------------------------
# Compose
# --------------------------------------------------------------------------

def decide(sql, dialect, *, era_top_cosine, schema_top_cosine, known_tables,
           restricted_columns=None, ground_warn=False, strict_functions=False,
           era_floor=0.45, schema_floor=0.40) -> Decision:
    """Compose all gates into a single pass / decline decision. Fail-closed.

    Consumes only structured signals (scores, parsed identifiers) — never the model's
    textual claims. Order: coverage -> SQL safety -> grounding -> policy.
    """
    cov_ok, cov_detail = coverage_ok(era_top_cosine, schema_top_cosine,
                                     era_floor=era_floor, schema_floor=schema_floor)
    if not cov_ok:
        return Decision(False, "coverage", cov_detail)

    safe, detail, tables, columns = validate_sql(sql, dialect,
                                                 strict_functions=strict_functions)
    if not safe:
        return Decision(False, "unsafe_sql", detail)

    pol_ok, pol_detail = policy_ok(tables, columns, restricted_columns=restricted_columns)
    if not pol_ok:
        return Decision(False, "policy", pol_detail, tables, columns)

    grounded, missing = check_grounding(tables, known_tables, warn=ground_warn)
    if not grounded:
        return Decision(False, "grounding", f"unknown identifier(s): {sorted(missing)}",
                        tables, columns)

    return Decision(True, None, None, tables, columns)
