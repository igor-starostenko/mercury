"""Year/month-scoped sankey flows.

The Overview page's /api/sankey and /api/sankey-merchants return the
full all-time payload; the Year and Month pages need the same 3-column
flows restricted to the selected year (or month). The queries are
identical to app.analytics.sankey_flow / sankey_merchants_flow with a
date filter added — kept in a separate module (not a parameter on the
base functions) so the Overview path and its callers stay untouched.
"""

from __future__ import annotations

from typing import Any

import duckdb

from .analytics import NOT_IGNORED, _date_filter


def sankey_flow(con: duckdb.DuckDBPyConnection,
                year: str | None = None,
                month: str | None = None) -> list[dict[str, Any]]:
    """Per-(account, category) sign-split movement for the Sankey,
    scoped to a year or month (no filter when both are None — identical
    to analytics.sankey_flow)."""
    where, params = _date_filter(year, month)
    rows = con.execute(f"""
        SELECT * FROM (
            SELECT source, account,
                   COALESCE(category, 'Uncategorized') AS category,
                   COALESCE(SUM(ABS(CASE WHEN amount < 0 THEN amount END)), 0) AS debit,
                   COALESCE(SUM(ABS(CASE WHEN amount > 0 THEN amount END)), 0) AS credit
            FROM effective_transactions
            WHERE amount != 0 AND {NOT_IGNORED} {where}
            GROUP BY 1, 2, 3
        ) t
        WHERE debit + credit > 0
        ORDER BY source, account, debit + credit DESC
    """, params).fetchall()
    return [{"source": s, "account": a, "category": c,
             "debit": round(d, 2), "credit": round(cr, 2),
             "amount": round(d + cr, 2)}
            for s, a, c, d, cr in rows]


def sankey_merchants_flow(con: duckdb.DuckDBPyConnection,
                          year: str | None = None,
                          month: str | None = None) -> list[dict[str, Any]]:
    """Per-(account, category, merchant) sign-split movement, scoped to
    a year or month (no filter when both are None — identical to
    analytics.sankey_merchants_flow)."""
    where, params = _date_filter(year, month)
    rows = con.execute(f"""
        SELECT * FROM (
            SELECT source, account,
                   COALESCE(category, 'Uncategorized') AS category,
                   COALESCE(merchant, '') AS merchant,
                   COALESCE(SUM(ABS(CASE WHEN amount < 0 THEN amount END)), 0) AS debit,
                   COALESCE(SUM(ABS(CASE WHEN amount > 0 THEN amount END)), 0) AS credit
            FROM effective_transactions
            WHERE amount != 0 AND {NOT_IGNORED} {where}
            GROUP BY 1, 2, 3, 4
        ) t
        WHERE debit + credit > 0
        ORDER BY source, account, debit + credit DESC
    """, params).fetchall()
    return [{"source": s, "account": a, "category": c, "merchant": m,
             "debit": round(d, 2), "credit": round(cr, 2),
             "amount": round(d + cr, 2)}
            for s, a, c, m, d, cr in rows]
