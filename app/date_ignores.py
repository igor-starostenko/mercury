"""Date-based ignore rules: ignore every transaction before / after a date
or within a date range.

A pure read-time layer, exactly like category rules: the ``date_ignores``
table stores the ranges, the ``effective_transactions`` view flags matching
rows (``date_ignored``), and every aggregate excludes them via
``NOT_IGNORED`` in analytics.py. Rows stay visible in the transaction
lists, flagged ``ignored``; a month or year whose transactions are all
ignored simply has no aggregate row, so it drops out of charts, period
lists and cards.

Boundary semantics (documented, symmetric):
- kind='before', start=X: ignore ``txn_date < X``   (X itself is kept)
- kind='after',  start=X: ignore ``txn_date > X``   (X itself is kept)
- kind='range',  start=X, end=Y: ignore ``X <= txn_date <= Y`` (both ends inclusive)
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional

import duckdb

CREATE_DATE_IGNORES_SQL = """
    CREATE TABLE IF NOT EXISTS date_ignores (
        id         INTEGER PRIMARY KEY,
        kind       VARCHAR,
        start_date DATE,
        end_date   DATE
    )
"""

KINDS = ("before", "after", "range")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class DateIgnore:
    id: int
    kind: str
    start: str
    end: Optional[str] = None


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_DATE_IGNORES_SQL)


def _parse_date(value, field: str) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format")
    value = value.strip()
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} is not a valid calendar date: {value}")
    return value


def validate(kind, start, end) -> tuple[str, str, Optional[str]]:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind must be one of: before, after, range")
    s = _parse_date(start, "start")
    if kind == "range":
        e = _parse_date(end, "end")
        if e < s:
            raise ValueError("end date must be on or after the start date")
    else:
        e = None
    return kind, s, e


def load(con: duckdb.DuckDBPyConnection) -> list[DateIgnore]:
    """All date ignores in id order (the UI list order)."""
    ensure_table(con)
    rows = con.execute(
        "SELECT id, kind, CAST(start_date AS VARCHAR), CAST(end_date AS VARCHAR)"
        " FROM date_ignores ORDER BY id"
    ).fetchall()
    return [DateIgnore(i, k, s, e) for i, k, s, e in rows]


def create(con: duckdb.DuckDBPyConnection, kind, start, end) -> DateIgnore:
    kind, s, e = validate(kind, start, end)
    ensure_table(con)
    for r in load(con):
        if (r.kind, r.start, r.end) == (kind, s, e):
            raise ValueError("an identical date ignore already exists (id "
                             f"{r.id}) — delete it first")
    new_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM date_ignores"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO date_ignores (id, kind, start_date, end_date)"
        " VALUES (?, ?, ?, ?)",
        [new_id, kind, s, e],
    )
    return DateIgnore(new_id, kind, s, e)


def delete(con: duckdb.DuckDBPyConnection, ignore_id: int) -> bool:
    ensure_table(con)
    cur = con.execute("DELETE FROM date_ignores WHERE id = ?", [ignore_id])
    return (cur.rowcount or 0) > 0


def ignored_counts(
    con: duckdb.DuckDBPyConnection, ignores: list[DateIgnore]
) -> dict[int, tuple[int, float]]:
    """Per rule: (n, net) over matching non-deleted rows, on the same
    effective view every report reads — so the numbers shown next to a
    rule are exactly what it excludes from the totals."""
    out = {r.id: (0, 0.0) for r in ignores}
    if not ignores:
        return out
    rows = con.execute(
        "SELECT CAST(txn_date AS VARCHAR), COALESCE(amount, 0.0)"
        " FROM effective_transactions"
    ).fetchall()
    for d, amount in rows:
        for r in ignores:
            hit = (r.kind == "before" and d < r.start) or \
                  (r.kind == "after" and d > r.start) or \
                  (r.kind == "range" and r.start <= d <= r.end)
            if hit:
                n, net = out[r.id]
                out[r.id] = (n + 1, net + amount)
    return out
