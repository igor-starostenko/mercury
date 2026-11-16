"""Analytics queries over the transactions data.

All reads go through the ``effective_transactions`` view (ingest.py): it
joins ``category_overrides`` (user-set category, takes precedence over
the imported one) and filters out ``deleted_transactions`` (soft
deletes). The imported rows in ``transactions`` are never rewritten by
user actions.
"""
from __future__ import annotations

import duckdb
from typing import Any

# Amount sign convention: negative = money out (spend), positive = money in.


def monthly_spend(con: duckdb.DuckDBPyConnection,
                  year: str | None = None) -> list[dict[str, Any]]:
    """Spend (expenses only) aggregated by month. Optional year scope."""
    where, params = _date_filter(year, None)
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y-%m') AS month,
            SUM(amount) AS spend,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE amount < 0 {where}
        GROUP BY 1
        ORDER BY 1
    """, params).fetchall()
    return [{"month": m, "spend": round(-s, 2), "n": n} for m, s, n in rows]


def spend_by_year(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute("""
        SELECT
            strftime(txn_date, '%Y') AS year,
            SUM(amount) AS spend
        FROM effective_transactions
        WHERE amount < 0
        GROUP BY 1
        ORDER BY 1
    """).fetchall()
    return [{"year": y, "spend": round(-s, 2)} for y, s in rows]


def spend_by_category_month(con: duckdb.DuckDBPyConnection,
                            year: str | None = None) -> list[dict[str, Any]]:
    """Category x month matrix for stacked trend charts. Optional year scope."""
    where, params = _date_filter(year, None)
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y-%m') AS month,
            COALESCE(category, 'Uncategorized') AS category,
            SUM(amount) AS spend
        FROM effective_transactions
        WHERE amount < 0 {where}
        GROUP BY 1, 2
        ORDER BY 1, 3 DESC
    """, params).fetchall()
    return [{"month": m, "category": c, "spend": round(-s, 2)}
            for m, c, s in rows]


def spend_by_category(con: duckdb.DuckDBPyConnection,
                      year: str | None = None,
                      month: str | None = None) -> list[dict[str, Any]]:
    where, params = _date_filter(year, month)
    rows = con.execute(f"""
        SELECT
            COALESCE(category, 'Uncategorized') AS category,
            SUM(amount) AS spend,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE amount < 0 {where}
        GROUP BY 1
        ORDER BY 2 DESC
    """, params).fetchall()
    return [{"category": c, "spend": round(-s, 2), "n": n} for c, s, n in rows]


def income_by_month(con: duckdb.DuckDBPyConnection,
                    year: str | None = None) -> list[dict[str, Any]]:
    where, params = _date_filter(year, None)
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y-%m') AS month,
            SUM(amount) AS income
        FROM effective_transactions
        WHERE amount > 0 {where}
        GROUP BY 1
        ORDER BY 1
    """, params).fetchall()
    return [{"month": m, "income": round(s, 2)} for m, s in rows]


def year_totals(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Per-year income/spend/net totals for the home page year cards."""
    rows = con.execute("""
        SELECT
            strftime(txn_date, '%Y') AS year,
            COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
            COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
            COUNT(*) AS n
        FROM effective_transactions
        GROUP BY 1
        ORDER BY 1
    """).fetchall()
    return [{"year": y, "spend": round(-s, 2), "income": round(i, 2),
             "net": round(i + s, 2), "n": n} for y, s, i, n in rows]


def weekly_breakdown(con: duckdb.DuckDBPyConnection,
                     month: str) -> list[dict[str, Any]]:
    """Spend/income per calendar week (Mon–Sun, ISO) overlapping the month.

    ``month`` is 'YYYY-MM'. Returns one row per week that has transactions;
    weeks with activity outside the month are excluded by the filter, and
    the client zero-fills the calendar buckets.
    """
    rows = con.execute("""
        SELECT
            date_trunc('week', txn_date) AS week,
            COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
            COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE strftime(txn_date, '%Y-%m') = ?
        GROUP BY 1
        ORDER BY 1
    """, [month]).fetchall()
    return [{"week": str(w)[:10], "spend": round(-s, 2),
             "income": round(i, 2), "n": n} for w, s, i, n in rows]


def available_periods(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Distinct years and months present in the data (ascending)."""
    rows = con.execute("""
        SELECT strftime(txn_date, '%Y') AS year,
               strftime(txn_date, '%Y-%m') AS month
        FROM effective_transactions
        GROUP BY 1, 2
        ORDER BY 2
    """).fetchall()
    return {
        "years": sorted({y for y, _ in rows}),
        "months": sorted({m for _, m in rows}),
    }


def summary(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    total = con.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(amount),0) AS net,
               COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
               COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
               MIN(txn_date), MAX(txn_date),
               COUNT(DISTINCT source),
               COUNT(DISTINCT account)
        FROM effective_transactions
    """).fetchone()
    return {
        "n_transactions": total[0],
        "net": round(total[1], 2),
        "total_spend": round(-total[2], 2),
        "total_income": round(total[3], 2),
        "first_date": str(total[4]) if total[4] else None,
        "last_date": str(total[5]) if total[5] else None,
        "n_sources": total[6],
        "n_accounts": total[7],
    }


def accounts(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute("""
        SELECT source, account, COUNT(*), COALESCE(SUM(amount),0)
        FROM effective_transactions
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).fetchall()
    return [{"source": s, "account": a, "n": n, "net": round(net, 2)}
            for s, a, n, net in rows]


def unknown_categories(con: duckdb.DuckDBPyConnection,
                       limit: int = 200) -> list[dict[str, Any]]:
    rows = con.execute("""
        SELECT id, source, account, CAST(txn_date AS VARCHAR), amount,
               description, raw_category
        FROM effective_transactions
        WHERE category IS NULL OR category = '' OR category = 'Uncategorized'
        ORDER BY txn_date DESC
        LIMIT ?
    """, [limit]).fetchall()
    return [
        {"id": r[0], "source": r[1], "account": r[2], "date": r[3],
         "amount": r[4], "description": r[5], "raw_category": r[6]}
        for r in rows
    ]


def set_category(con: duckdb.DuckDBPyConnection, txn_id: int,
                 category: str | None) -> None:
    """Layer a user-chosen category on top of the original import.

    The imported row keeps its CSV-provided category untouched; the
    override table takes precedence in every read. ``category=None``
    removes the override, reverting the row to its original category.
    """
    if category is None or str(category).strip() == "":
        con.execute("DELETE FROM category_overrides WHERE txn_id = ?",
                    [txn_id])
        con.execute(
            "UPDATE transactions SET classified = "
            "(category IS NOT NULL AND category != '') WHERE id = ?",
            [txn_id])
    else:
        con.execute(
            "INSERT INTO category_overrides VALUES (?, ?) "
            "ON CONFLICT (txn_id) DO UPDATE SET category = excluded.category",
            [txn_id, str(category).strip()])
        con.execute("UPDATE transactions SET classified = true WHERE id = ?",
                    [txn_id])


def set_category_by_key(con: duckdb.DuckDBPyConnection, key: str,
                        category: str) -> None:
    """Apply a category to every row sharing a key (same underlying txn)."""
    for (txn_id,) in con.execute(
            "SELECT id FROM transactions WHERE key = ?", [key]).fetchall():
        set_category(con, txn_id, category)


def soft_delete(con: duckdb.DuckDBPyConnection, txn_id: int) -> bool:
    """Soft-delete: hide the transaction from every read.

    The imported row itself is not touched (originals stay unmodified);
    a tombstone in deleted_transactions filters it out. Idempotent.
    Returns False if the transaction does not exist.
    """
    if not con.execute(
            "SELECT 1 FROM transactions WHERE id = ?", [txn_id]).fetchone():
        return False
    con.execute(
        "INSERT INTO deleted_transactions VALUES (?) "
        "ON CONFLICT (txn_id) DO NOTHING", [txn_id])
    return True


def restore(con: duckdb.DuckDBPyConnection, txn_id: int) -> bool:
    """Remove a soft-delete tombstone. True if the row was deleted."""
    cur = con.execute(
        "DELETE FROM deleted_transactions WHERE txn_id = ?", [txn_id])
    return (cur.rowcount or 0) > 0


def _display_category(category: Any) -> str:
    """Normalized name as displayed in the UI."""
    return category if category else "Uncategorized"


def transactions(con: duckdb.DuckDBPyConnection,
                 year: str | None = None,
                 month: str | None = None,
                 category: str | None = None,
                 limit: int = 1000) -> list[dict[str, Any]]:
    """Individual transactions for a date range (newest first).

    ``category`` matches the normalized name as displayed ('Uncategorized'
    for rows with no category). ``original_category`` is the
    CSV-provided category (what the import stored, before any user
    override). Soft-deleted rows are excluded.
    """
    conds: list[str] = []
    params: list = []
    if month:
        conds.append("strftime(txn_date, '%Y-%m') = ?")
        params.append(month)
    elif year:
        conds.append("strftime(txn_date, '%Y') = ?")
        params.append(year)
    if category:
        conds.append("COALESCE(o.category, t.category, 'Uncategorized') = ?")
        params.append(category)
    # Explicit join (not the view) because the original, pre-override
    # category is exposed alongside the effective one.
    rows = con.execute(f"""
        SELECT t.id, t.source, t.account, CAST(t.txn_date AS VARCHAR),
               t.amount, t.description,
               COALESCE(o.category, t.category),
               t.category
        FROM transactions AS t
        LEFT JOIN category_overrides AS o ON o.txn_id = t.id
        WHERE t.id NOT IN (SELECT txn_id FROM deleted_transactions)
        {("AND " + " AND ".join(conds)) if conds else ""}
        ORDER BY t.txn_date DESC, t.id DESC
        LIMIT ?
    """, [*params, limit]).fetchall()
    return [
        {"id": r[0], "source": r[1], "account": r[2], "date": r[3],
         "amount": r[4], "description": r[5],
         "category": _display_category(r[6]),
         "original_category": r[7]}
        for r in rows
    ]


def _date_filter(year: str | None, month: str | None) -> tuple[str, list]:
    """Build a WHERE clause for year/month. month is 'YYYY-MM'."""
    if month:
        return ("AND strftime(txn_date, '%Y-%m') = ?", [month])
    if year:
        return ("AND strftime(txn_date, '%Y') = ?", [year])
    return "", []
