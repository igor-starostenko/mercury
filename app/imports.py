"""CSV import tracking: which file each transaction row came from.

Side table (csv_imports) plus transactions.import_id, kept separate so
imported rows stay exactly as written:

- One record per uploaded CSV file (identity = filename). Re-uploading
  the same file finds the existing record, so its account label —
  including any user rename — survives re-imports and dedupe still
  matches under the current name.
- The record's `account` is the file's CURRENT account label. The
  effective_transactions view maps each row's display account to its
  import's label (COALESCE of import label, raw account), so renaming an
  import retroactively re-labels all of its rows in every report
  (accounts, summary, transactions). The raw rows are never rewritten.
- Rows get import_id set at insert time. At startup, rows imported
  before this table existed (import_id NULL) are backfilled into
  synthetic records grouped by (source, account) — shown in the UI as
  "earlier import" — so every row belongs to exactly one import.
- Deleting an import hard-deletes exactly the rows carrying that
  import_id (plus their per-row overrides and deleted-transaction
  tombstones). Category rules are never touched.
- Each import carries a nullable ``color`` — the account color used
  in the Accounts table and the Sankey charts. NULL means "use the
  bank's primary color" (the default below); a user-set value
  overrides it. The color lives on the record, so renames keep it
  and re-imports don't reset it.
"""
from __future__ import annotations

import re

import duckdb

# Primary brand colors per known bank (the default account color).
# The set is not exhaustive — unknown banks fall back to the neutral
# grey below, and any account can be recolored from Manage Imports.
BANK_COLORS = {
    "Wells Fargo": "#d71e28",
    "Chase": "#117aca",
    "Apple Card": "#55565a",
    "US Bank": "#c8102e",
}
DEFAULT_ACCOUNT_COLOR = "#5a6772"

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex_color(value):
    """Lowercase #rrggbb or None. Accepts #rgb and #rrggbb; anything
    else raises ValueError. None / blank / empty string clears the color."""
    if value is None:
        return None
    v = str(value).strip()
    if v == "":
        return None
    if not _HEX_RE.match(v):
        raise ValueError(f"not a hex color: {value!r}")
    if len(v) == 4:
        v = "#" + "".join(c * 2 for c in v[1:])
    return v.lower()


def effective_color(stored, source):
    """The account color to render: the import's stored override if
    set, else the bank's primary color (neutral grey for unknown banks)."""
    return stored or BANK_COLORS.get(source, DEFAULT_ACCOUNT_COLOR)


CREATE_IMPORTS_SQL = """
    CREATE TABLE IF NOT EXISTS csv_imports (
        id          INTEGER PRIMARY KEY,
        filename    VARCHAR,
        account     VARCHAR,
        imported_at TIMESTAMP,
        color       VARCHAR
    )
"""


def ensure_color_column(con: duckdb.DuckDBPyConnection) -> None:
    """csv_imports.color — nullable account color; NULL means the bank's
    primary color (see effective_color). Idempotent, like ensure_import_column."""
    cols = [r[1] for r in con.execute("PRAGMA table_info('csv_imports')").fetchall()]
    if "color" not in cols:
        con.execute("ALTER TABLE csv_imports ADD COLUMN color VARCHAR")


def _next_id(con: duckdb.DuckDBPyConnection) -> int:
    """Next explicit id. DuckDB has no autoincrement on INTEGER PRIMARY
    KEY (unlike SQLite); ids are allocated MAX(id)+1, exactly like
    merge_txns does for transactions.id. One server process writes."""
    return con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM csv_imports").fetchone()[0]


def ensure_import_column(con: duckdb.DuckDBPyConnection) -> None:
    """transactions.import_id — set at insert time, points at csv_imports.id."""
    cols = [r[1] for r in con.execute("PRAGMA table_info('transactions')").fetchall()]
    if "import_id" not in cols:
        con.execute("ALTER TABLE transactions ADD COLUMN import_id INTEGER")


def backfill_imports(con: duckdb.DuckDBPyConnection) -> int:
    """Group legacy rows (import_id NULL) by (source, account) into
    synthetic records, once. No-ops on every later startup (inserts always
    set import_id, so the NULL group is empty). Returns records created."""
    created = 0
    groups = con.execute(
        """
        SELECT source, account, COUNT(*)
        FROM transactions
        WHERE import_id IS NULL
        GROUP BY source, account
        """
    ).fetchall()
    if not groups:
        return 0
    con.execute("BEGIN TRANSACTION")
    try:
        for src, acct, _n in groups:
            imp_id = _next_id(con)
            con.execute(
                "INSERT INTO csv_imports (id, filename, account, imported_at) "
                "VALUES (?, NULL, ?, now())",
                [imp_id, acct],
            )
            con.execute(
                "UPDATE transactions SET import_id = ? "
                "WHERE import_id IS NULL AND source = ? AND account = ?",
                [imp_id, src, acct],
            )
            created += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return created


def resolve_import(con: duckdb.DuckDBPyConnection, filename: str, account: str) -> int:
    """Record for this filename; created on first import. Re-uploads keep
    the record's current label (a rename is not overwritten here), so
    re-importing a renamed file dedupes under the new name."""
    row = con.execute("SELECT id FROM csv_imports WHERE filename = ?", [filename]).fetchone()
    if row:
        return row[0]
    imp_id = _next_id(con)
    con.execute(
        "INSERT INTO csv_imports (id, filename, account, imported_at) VALUES (?, ?, ?, now())",
        [imp_id, filename, account],
    )
    return imp_id


def set_import_color(con: duckdb.DuckDBPyConnection, import_id: int, color: str | None) -> dict | None:
    """Set (or clear, with None) an import's account color override.
    Raises ValueError on a malformed hex color. Returns the updated row
    ({id, color}) or None when the import is unknown."""
    normalized = normalize_hex_color(color)
    cur = con.execute(
        "UPDATE csv_imports SET color = ? WHERE id = ?",
        [normalized, import_id],
    )
    if cur.rowcount == 0:
        return None
    return {"id": import_id, "color": normalized}


def rename_import(con: duckdb.DuckDBPyConnection, import_id: int, new_account: str) -> int | None:
    """Re-label an import's account for all its rows (label lives on the
    record; the view maps it in). None if the id is unknown."""
    cur = con.execute("UPDATE csv_imports SET account = ? WHERE id = ?", [new_account, import_id])
    if cur.rowcount == 0:
        return None
    return cur.rowcount


def list_imports(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """One row per (import, source) — a file whose rows ever landed under
    two banks (same filename re-imported with a different source_map pick)
    shows one row per source, all sharing the file's account label."""
    rows = con.execute(
        """
        SELECT ci.id,
               ci.filename,
               ci.account,
               ci.imported_at,
               ci.color,
               t.source,
               COUNT(*) AS n,
               MIN(t.txn_date) AS first_date,
               MAX(t.txn_date) AS last_date,
               COALESCE(SUM(t.amount), 0) AS net
        FROM csv_imports AS ci
        JOIN transactions AS t ON t.import_id = ci.id
        GROUP BY ci.id, ci.filename, ci.account, ci.imported_at, ci.color, t.source
        ORDER BY ci.imported_at, ci.id, t.source
        """
    ).fetchall()
    return [
        {
            "id": r[0],
            "file": r[1],
            "account": r[2],
            "imported_at": r[3],
            "source": r[5],
            "n": r[6],
            "first_date": r[7],
            "last_date": r[8],
            "net": r[9],
            "custom_color": r[4],
            "color": effective_color(r[4], r[5]),
        }
        for r in rows
    ]


def account_color(con: duckdb.DuckDBPyConnection, account: str, source: str) -> str:
    """Effective color for an (source, account) pair: the earliest import
    carrying this account label's stored override, else the bank's
    primary color (neutral grey for unknown banks)."""
    row = con.execute(
        "SELECT color FROM csv_imports WHERE account = ? ORDER BY id ASC LIMIT 1",
        [account],
    ).fetchone()
    return effective_color(row[0] if row else None, source)


def _count_and_delete(con, table: str, where: str, params: list) -> int:
    n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
    con.execute(f"DELETE FROM {table} WHERE {where}", params)
    return n


def delete_import(con: duckdb.DuckDBPyConnection, import_id: int, source: str | None = None) -> dict:
    """Hard-delete the import's rows (+ their overrides/tombstones) and,
    once no rows remain under it, the record itself. Rules untouched.
    `source` scopes the delete to one bank's rows of the file."""
    where = "import_id = ?"
    params: list = [import_id]
    if source is not None:
        where += " AND source = ?"
        params.append(source)
    ids = [str(r[0]) for r in con.execute(f"SELECT id FROM transactions WHERE {where}", params).fetchall()]
    out = {"rows": 0, "overrides": 0, "tombstones": 0}
    if ids:
        id_list = ",".join(ids)  # ints from the DB — safe to splice
        out["overrides"] = _count_and_delete(con, "category_overrides", f"txn_id IN ({id_list})", [])
        out["tombstones"] = _count_and_delete(con, "deleted_transactions", f"txn_id IN ({id_list})", [])
        out["rows"] = _count_and_delete(con, "transactions", where, params)
    if con.execute("SELECT COUNT(*) FROM transactions WHERE import_id = ?", [import_id]).fetchone()[0] == 0:
        con.execute("DELETE FROM csv_imports WHERE id = ?", [import_id])
    return out
