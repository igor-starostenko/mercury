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

from . import rules

# Amount sign convention: negative = money out (spend), positive = money in.

# Row-level "ignore": a transaction whose effective category is the
# reserved 'Ignored' value (set by a read-time rule or by hand) is
# excluded from every aggregate below. It stays visible in
# /api/transactions and /api/overrides, flagged ignored=true — that is
# what ignoring means for transfers between accounts and the like.
#
# Date-level ignore: the date_ignores table (date_ignores.py) marks rows
# by date range in the view's date_ignored column — same treatment:
# excluded from every aggregate, still visible, flagged ignored. A month
# or year whose rows are all ignored has no aggregate row at all, so it
# drops out of charts, period lists and year cards.
NOT_IGNORED = ("COALESCE(category, '') <> 'Ignored' "
               "AND NOT COALESCE(date_ignored, false)")


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
        WHERE amount < 0 AND {NOT_IGNORED} {where}
        GROUP BY 1
        ORDER BY 1
    """, params).fetchall()
    return [{"month": m, "spend": round(-s, 2), "n": n} for m, s, n in rows]


def spend_by_year(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y') AS year,
            SUM(amount) AS spend
        FROM effective_transactions
        WHERE amount < 0 AND {NOT_IGNORED}
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
        WHERE amount < 0 AND {NOT_IGNORED} {where}
        GROUP BY 1, 2
        ORDER BY 1, 3 DESC
    """, params).fetchall()
    return [{"month": m, "category": c, "spend": round(-s, 2)}
            for m, c, s in rows]


def flow_by_category(con: duckdb.DuckDBPyConnection,
                     year: str | None = None,
                     month: str | None = None,
                     weekly: bool = False) -> list[dict[str, Any]]:
    """Spend + income per (bucket, category) for the stacked trend charts.

    ``bucket`` is 'YYYY-MM' by default, or the ISO-week Monday
    ('YYYY-MM-DD') when ``weekly=True`` — the same bucketing
    ``weekly_breakdown`` uses. One row per (bucket, category) that has
    activity; a bucket with no activity for a category carries no row
    (the client zero-fills). Ignored rows are excluded, like in every
    other aggregate.
    """
    bucket = ("date_trunc('week', txn_date)" if weekly
              else "strftime(txn_date, '%Y-%m')")
    where, params = _date_filter(year, month)
    rows = con.execute(f"""
        SELECT {bucket} AS bucket,
               COALESCE(category, 'Uncategorized') AS category,
               COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
               COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income
        FROM effective_transactions
        WHERE {NOT_IGNORED} {where}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """, params).fetchall()
    return [{"bucket": str(b)[:10], "category": c,
             "spend": round(-s, 2), "income": round(i, 2)}
            for b, c, s, i in rows]


def spend_by_category(con: duckdb.DuckDBPyConnection,
                      year: str | None = None,
                      month: str | None = None) -> list[dict[str, Any]]:
    """Per-category totals over the scope: spend (negative rows), refunds
    (positive rows) and net = spend - refunds. A category appears if it has
    any non-ignored non-zero row in scope, so refund-only categories surface
    as net credits (net < 0). Sorted by net descending (net spend first,
    net credits last)."""
    where, params = _date_filter(year, month)
    rows = con.execute(f"""
        SELECT
            COALESCE(category, 'Uncategorized') AS category,
            COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
            COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE {NOT_IGNORED} {where}
        GROUP BY 1
        HAVING COALESCE(SUM(amount) FILTER (amount != 0), 0) != 0
        ORDER BY spend + income ASC
    """, params).fetchall()
    return [{"category": c, "spend": round(-s, 2), "refunds": round(i, 2),
             "net": round(-s - i, 2), "n": n} for c, s, i, n in rows]


def income_by_month(con: duckdb.DuckDBPyConnection,
                    year: str | None = None) -> list[dict[str, Any]]:
    where, params = _date_filter(year, None)
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y-%m') AS month,
            SUM(amount) AS income
        FROM effective_transactions
        WHERE amount > 0 AND {NOT_IGNORED} {where}
        GROUP BY 1
        ORDER BY 1
    """, params).fetchall()
    return [{"month": m, "income": round(s, 2)} for m, s in rows]


def year_totals(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Per-year income/spend/net totals for the home page year cards."""
    rows = con.execute(f"""
        SELECT
            strftime(txn_date, '%Y') AS year,
            COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
            COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE {NOT_IGNORED}
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
    rows = con.execute(f"""
        SELECT
            date_trunc('week', txn_date) AS week,
            COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
            COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
            COUNT(*) AS n
        FROM effective_transactions
        WHERE strftime(txn_date, '%Y-%m') = ? AND {NOT_IGNORED}
        GROUP BY 1
        ORDER BY 1
    """, [month]).fetchall()
    return [{"week": str(w)[:10], "spend": round(-s, 2),
             "income": round(i, 2), "n": n} for w, s, i, n in rows]


def available_periods(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Distinct years and months present in the data (ascending)."""
    rows = con.execute(f"""
        SELECT strftime(txn_date, '%Y') AS year,
               strftime(txn_date, '%Y-%m') AS month
        FROM effective_transactions
        WHERE {NOT_IGNORED}
        GROUP BY 1, 2
        ORDER BY 2
    """).fetchall()
    return {
        "years": sorted({y for y, _ in rows}),
        "months": sorted({m for _, m in rows}),
    }


def summary(con: duckdb.DuckDBPyConnection,
            year: str | None = None,
            month: str | None = None) -> dict[str, Any]:
    """Whole-database totals, optionally scoped to a year or month.

    ``n_accounts`` counts distinct (source, account) pairs, not account
    names — two banks that both call their account "Checking" are two
    accounts.
    """
    where, params = _date_filter(year, month)
    extra = " AND " + where[4:].strip() if where else ""
    total = con.execute(f"""
        SELECT COUNT(*),
               COALESCE(SUM(amount),0) AS net,
               COALESCE(SUM(amount) FILTER (amount < 0), 0) AS spend,
               COALESCE(SUM(amount) FILTER (amount > 0), 0) AS income,
               MIN(txn_date), MAX(txn_date),
               COUNT(DISTINCT source),
               COUNT(DISTINCT (source, account))
        FROM effective_transactions
        WHERE {NOT_IGNORED} {extra}
    """, params).fetchone()
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
    rows = con.execute(f"""
        SELECT source, account, COUNT(*), COALESCE(SUM(amount),0)
        FROM effective_transactions
        WHERE {NOT_IGNORED}
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
                 category: str | None, source: str = "manual") -> None:
    """Layer a user-chosen category on top of the original import.

    The imported row keeps its CSV-provided category untouched; the
    override table takes precedence in every read. ``category=None``
    removes the override, reverting the row to its original category.
    ``source`` records who set it: 'manual' (user edit) or 'llm'
    (auto-classify). 'rule' appears only in legacy overrides written
    before rules became a read-time layer.
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
            "INSERT INTO category_overrides VALUES (?, ?, ?) "
            "ON CONFLICT (txn_id) DO UPDATE SET category = excluded.category, "
            "source = excluded.source",
            [txn_id, str(category).strip(), source])
        con.execute("UPDATE transactions SET classified = true WHERE id = ?",
                    [txn_id])


def set_category_by_key(con: duckdb.DuckDBPyConnection, key: str,
                        category: str) -> None:
    """Apply a category to every row sharing a key (same underlying txn)."""
    for (txn_id,) in con.execute(
            "SELECT id FROM transactions WHERE key = ?", [key]).fetchall():
        set_category(con, txn_id, category)


_OVERRIDE_SET_BY = ("all", "manual", "llm", "rule", "csv", "uncategorized")
_OVERRIDE_SORTS = {
    "date": "v.txn_date",
    # Case-insensitive plain order — DuckDB 2.0 has no numeric-aware
    # collation, so this deviates from the old client-side
    # localeCompare(numeric:true) only for digit runs of differing
    # length ("TXN 2" vs "TXN 10") and case.
    "description": "lower(v.description)",
    "amount": "v.amount",
}
_OVERRIDE_SORT_DIR_DEFAULT = {"date": "desc", "description": "asc",
                              "amount": "desc"}


def override_rows(con: duckdb.DuckDBPyConnection,
                  limit: int = 200,
                  include_uncategorized: bool = False,
                  set_by: str = "all",
                  sort_by: str = "date",
                  sort_dir: str | None = None,
                  q: str | None = None) -> dict[str, Any]:
    """Transactions whose category is set — explicit or by rule.

    Built on the effective_transactions view, so rows re-categorized by a
    live rule are included with ``set_by`` = 'rule' even though no
    override row was written for them. Each row carries ``category``
    (effective) and ``original_category`` (the CSV import, untouched);
    ``set_by`` is 'manual', 'llm' or 'rule' — or 'unknown' for
    overrides that predate source tracking.

    With ``include_uncategorized``, rows that have no override, no rule
    match and were missing a category in the CSV import are included as
    well, with ``set_by`` = 'uncategorized' (still uncategorized).

    Without a search the rows are the section's base set above. With a
    ``q`` and ``set_by='all'`` the search scans the FULL history
    instead — a typed search must find every matching transaction,
    including rows that only carry the bank's CSV category and are not
    listed by default; those carry ``set_by`` = 'csv' (badge "bank").
    Chip-scoped searches keep their provenance scope. Filtering and
    sorting run in SQL over the full history, not over a truncated page
    — ``LIMIT`` is applied after the server-side filter and sort.
    Parameters:

    - ``set_by``: 'all' (default), 'manual', 'llm', 'rule', 'csv'
      (rows whose category is the bank's CSV original — no override,
      no live rule, and the CSV carried a category) or
      'uncategorized' (rows still missing a category; the legacy
      'unknown' badge has the same NULL-source shape and is included).
    - ``sort_by``: 'date' (default), 'description' or 'amount';
      ``sort_dir`` 'asc'/'desc' (per-key default: date desc,
      description asc, amount desc). Ties break on ascending id, same
      as the old client-side sort.
    - ``q``: case-insensitive substring on the whitespace-normalized
      description — the same matching the UI search and category rules
      use, so the preview stays a true rule dry-run.

    Returns ``{"rows": [...], "total": N, "uncategorized": M}``: the
    top ``limit`` rows of the filtered + sorted full history, the total
    number of rows matching the filter (so the UI can say "1000 of
    4821"), and the full-history count of rows still uncategorized
    (drives the "N need a category" badge and the Classify button —
    correct even when the list window is smaller than the history).
    """
    if set_by not in _OVERRIDE_SET_BY:
        raise ValueError(f"set_by must be one of: {', '.join(_OVERRIDE_SET_BY)}")
    if sort_by not in _OVERRIDE_SORTS:
        raise ValueError(f"sort_by must be one of: {', '.join(_OVERRIDE_SORTS)}")
    if sort_dir is None:
        sort_dir = _OVERRIDE_SORT_DIR_DEFAULT[sort_by]
    if sort_dir not in ("asc", "desc"):
        raise ValueError("sort_dir must be 'asc' or 'desc'")

    q_norm = " ".join((q or "").split())
    if set_by in ("manual", "llm", "rule"):
        # Provenance scope: rows whose EFFECTIVE category was set by
        # that actor (stored override for manual/llm — the view's CASE
        # makes a stored override win, so override_source is its source;
        # live rule match, or a legacy stored source='rule' override,
        # for rule). Each such row is in the base set by construction,
        # so no base-set AND is needed.
        where: list[str] = ["v.override_source = ?"]
        params: list[Any] = [set_by]
    elif set_by == "csv":
        # Bank-provided category: no stored override, no live rule, and
        # the CSV actually carried a category (rows missing one are
        # 'uncategorized', not bank-set).
        where = ["v.override_source IS NULL AND o2.txn_id IS NULL"
                 " AND NOT (t.category IS NULL OR t.category = ''"
                 " OR t.category = 'Uncategorized')"]
        params = []
    elif set_by == "uncategorized":
        # Truly pending rows: no stored override (a legacy override's
        # NULL source is NOT "uncategorized" — it has a category), no
        # live rule, and no CSV category. The filter selects these rows
        # on its own — include_uncategorized only controls whether they
        # also appear in an 'all' list.
        where = ["v.override_source IS NULL AND o2.txn_id IS NULL"
                 " AND (t.category IS NULL OR t.category = ''"
                 " OR t.category = 'Uncategorized')"]
        params = []
    elif q_norm:
        # A typed search runs over the FULL history — not just this
        # section's base set. The list shows rows a user / the LLM / a
        # rule categorized and rows still missing a category; rows that
        # carry only the bank's CSV category are not listed by default,
        # but a search must still find them (and a rule with that
        # pattern would catch them — the search is the rule dry-run).
        # Provenance-scoped searches keep their scope via the branches
        # above.
        where = []
        params = []
    else:
        # Base set: rows with a stored override, rows re-categorized by a
        # live rule, and (opt-in) rows still missing a category. The
        # override check is EXISTS, not `override_source IS NOT NULL` —
        # a legacy override's source column can be NULL, so the resolved
        # source alone would hide it from the list.
        where = ["(o2.txn_id IS NOT NULL OR v.override_source = 'rule'"
                 " OR (? = 1 AND (t.category IS NULL OR t.category = ''"
                 " OR t.category = 'Uncategorized')))"]
        params = [int(include_uncategorized)]
    if q_norm:
        # Whitespace-normalized substring, exactly like the UI search
        # and the category-rule matching.
        where.append(
            "instr(lower(regexp_replace(v.description, '\\s+', ' ', 'g')),"
            " lower(?)) > 0")
        params.append(q_norm)
    where_sql = " AND ".join(where)

    total, = con.execute(f"""
        SELECT COUNT(*)
        FROM effective_transactions AS v
        JOIN transactions AS t ON t.id = v.id
        LEFT JOIN category_overrides AS o2 ON o2.txn_id = v.id
        WHERE {where_sql}
    """, params).fetchone()

    pending = 0
    if include_uncategorized:
        pending, = con.execute("""
            SELECT COUNT(*)
            FROM effective_transactions AS v
            JOIN transactions AS t ON t.id = v.id
            WHERE v.override_source IS NULL
              AND (t.category IS NULL OR t.category = ''
                   OR t.category = 'Uncategorized')
        """).fetchone()

    rows = con.execute(f"""
        SELECT v.id, v.source, v.account, CAST(v.txn_date AS VARCHAR),
               v.amount, v.description, v.category, t.category,
               v.override_source,
               o2.txn_id IS NOT NULL,
               (t.category IS NULL OR t.category = ''
                OR t.category = 'Uncategorized') AS missing_csv,
               (COALESCE(v.category, '') = 'Ignored'
                OR COALESCE(v.date_ignored, false)) AS ignored
        FROM effective_transactions AS v
        JOIN transactions AS t ON t.id = v.id
        LEFT JOIN category_overrides AS o2 ON o2.txn_id = v.id
        WHERE {where_sql}
        ORDER BY {_OVERRIDE_SORTS[sort_by]} {sort_dir}, v.id ASC
        LIMIT ?
    """, [*params, int(limit)]).fetchall()
    enabled = rules.load_rules(con)
    out = []
    for r in rows:
        # Rule dry-run verdicts: which enabled rule this description
        # matches (first in priority order = the one the view applies)
        # and how many more would be shadowed by it. Same matching as
        # the effective_transactions SQL, so the UI can show, test and
        # verify rules before/without applying anything.
        matched = rules.matching_rules(r[5], enabled,
                                       source=r[1], account=r[2])
        # r[9] = a stored override exists (True/False) — the source column
        # itself can be NULL on legacy rows, so existence decides.
        resolved, has_override, missing = r[8], r[9], r[10]
        if has_override:
            # Stored override: source tracked (manual/llm/rule) or a
            # legacy override from before source tracking (NULL source).
            set_by = resolved or "unknown"
        elif resolved is not None:
            # No stored override — the view resolved 'rule' (live match).
            set_by = resolved
        elif missing:
            # No override, no rule, and no CSV category: pending.
            set_by = "uncategorized"
        else:
            # No override, no rule — the category is the bank's CSV
            # original. These rows are not listed by default; a search
            # surfaces them, so the badge says where the category came
            # from instead of claiming "unknown".
            set_by = "csv"
        out.append({
            "id": r[0], "source": r[1], "account": r[2], "date": r[3],
            "amount": r[4], "description": r[5],
            "category": _display_category(r[6]),
            "original_category": r[7],
            "set_by": set_by,
            "ignored": bool(r[11]),
            "rule_match": ({"pattern": matched[0].pattern,
                            "category": matched[0].category,
                            "position": matched[0].position}
                           if matched else None),
            "rule_matches": len(matched),
        })
    return {"rows": out, "total": int(total), "uncategorized": int(pending)}


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
    for rows with no category). The effective category comes from the
    effective_transactions view, so the live rule layer is included;
    ``original_category`` is the CSV-provided category (what the import
    stored, before any assignment or rule). Soft-deleted rows are
    excluded.
    """
    conds: list[str] = []
    params: list = []
    if month:
        conds.append("strftime(v.txn_date, '%Y-%m') = ?")
        params.append(month)
    elif year:
        conds.append("strftime(v.txn_date, '%Y') = ?")
        params.append(year)
    if category:
        conds.append("COALESCE(v.category, 'Uncategorized') = ?")
        params.append(category)
    # The view carries the effective category (override + rule layer);
    # transactions is joined only to expose the untouched CSV original.
    # Ignored rows are NOT filtered out here — they stay visible, with
    # an explicit ignored flag for the UI.
    rows = con.execute(f"""
        SELECT v.id, v.source, v.account, CAST(v.txn_date AS VARCHAR),
               v.amount, v.description, v.category, t.category,
               v.override_source,
               (COALESCE(v.category, '') = 'Ignored'
                OR COALESCE(v.date_ignored, false)) AS ignored
        FROM effective_transactions AS v
        JOIN transactions AS t ON t.id = v.id
        {("WHERE " + " AND ".join(conds)) if conds else ""}
        ORDER BY v.txn_date DESC, v.id DESC
        LIMIT ?
    """, [*params, limit]).fetchall()
    enabled = rules.load_rules(con)
    out = []
    for r in rows:
        matched = rules.matching_rules(r[5], enabled,
                                       source=r[1], account=r[2])
        # Effective category source for this row: override_source is
        # already the view's resolution (manual > rule > llm); fall back
        # to the CSV value when nothing else claims the row.
        if r[8]:
            set_by = r[8]
        elif matched:
            set_by = "rule"
        elif r[7] is not None and str(r[7]).strip() not in ("", "Uncategorized"):
            set_by = "csv"
        else:
            set_by = "none"
        out.append({
            "id": r[0], "source": r[1], "account": r[2], "date": r[3],
            "amount": r[4], "description": r[5],
            "category": _display_category(r[6]),
            "original_category": r[7],
            "ignored": bool(r[9]),
            "set_by": set_by,
            # Rule dry-run verdicts, same matching as the SQL view.
            "rule_match": ({"pattern": matched[0].pattern,
                            "category": matched[0].category,
                            "position": matched[0].position}
                           if matched else None),
            "rule_matches": len(matched),
        })
    return out


def _date_filter(year: str | None, month: str | None) -> tuple[str, list]:
    """Build a WHERE clause for year/month. month is 'YYYY-MM'."""
    if month:
        return ("AND strftime(txn_date, '%Y-%m') = ?", [month])
    if year:
        return ("AND strftime(txn_date, '%Y') = ?", [year])
    return "", []
