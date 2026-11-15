"""Analytics queries over the standardized transactions table."""
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
        FROM transactions
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
    con.execute(
        "UPDATE transactions SET category = ?, classified = true WHERE id = ?",
        [category, txn_id])


def set_category_by_key(con: duckdb.DuckDBPyConnection, key: str,
                        category: str) -> None:
    """Apply a category to every row sharing a key (same underlying txn)."""
    con.execute(
        "UPDATE transactions SET category = ?, classified = true WHERE key = ?",
        [category, key])


def transactions(con: duckdb.DuckDBPyConnection,
                 year: str | None = None,
                 month: str | None = None,
                 category: str | None = None,
                 limit: int = 1000) -> list[dict[str, Any]]:
    """Individual transactions for a date range (newest first).

    ``category`` matches the normalized name as displayed ('Uncategorized'
    for rows with no category).
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
        conds.append("COALESCE(category, 'Uncategorized') = ?")
        params.append(category)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = con.execute(f"""
        SELECT id, source, account, CAST(txn_date AS VARCHAR), amount,
               description, COALESCE(category, 'Uncategorized')
        FROM transactions
        {where}
        ORDER BY txn_date DESC, id DESC
        LIMIT ?
    """, [*params, limit]).fetchall()
    return [
        {"id": r[0], "source": r[1], "account": r[2], "date": r[3],
         "amount": r[4], "description": r[5], "category": r[6]}
        for r in rows
    ]


def _date_filter(year: str | None, month: str | None) -> tuple[str, list]:
    """Build a WHERE clause for year/month. month is 'YYYY-MM'."""
    if month:
        return ("AND strftime(txn_date, '%Y-%m') = ?", [month])
    if year:
        return ("AND strftime(txn_date, '%Y') = ?", [year])
    return "", []
