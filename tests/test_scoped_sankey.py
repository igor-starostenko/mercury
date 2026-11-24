"""Year/month-scoped sankey flows (scoped_sankey) — the Year and Month
pages' Money Flow panel.

The scoped queries are identical to analytics.sankey_flow /
sankey_merchants_flow with a ?year=/?month= filter added; the Overview
path (no filter) stays unchanged. These tests seed transactions across
two years/months and assert the filter is applied and that an empty
scope returns no rows.
"""

import duckdb

from app import date_ignores, ingest, imports, merchants, rules, scoped_sankey


def _conn(tmp_path, name="scoped_sankey.db"):
    """The same schema bootstrap main.lifespan runs (fresh DB)."""
    con = duckdb.connect(str(tmp_path / name))
    con.execute(ingest.CREATE_TABLE_SQL)
    con.execute(ingest.CREATE_OVERRIDES_SQL)
    ingest.ensure_override_source(con)
    con.execute(ingest.CREATE_DELETED_SQL)
    con.execute(rules.CREATE_RULES_SQL)
    rules.ensure_rule_position(con)
    rules.ensure_rule_scope(con)
    con.execute(date_ignores.CREATE_DATE_IGNORES_SQL)
    con.execute(imports.CREATE_IMPORTS_SQL)
    imports.ensure_import_column(con)
    con.execute(merchants.CREATE_MERCHANT_RULES_SQL)
    merchants.ensure_rule_position(con)
    merchants.ensure_rule_scope(con)
    con.execute(merchants.CREATE_MERCHANT_OVERRIDES_SQL)
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
    return con


def _seed(con):
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)",
        [
            (1, "Wells Fargo", "Checking", "2025-01-05", -50.0,
             "WALMART", "Groceries"),
            (2, "Wells Fargo", "Checking", "2025-02-06", -20.0,
             "STARBUCKS", "Dining"),
            (3, "Chase", "Checking", "2025-02-07", 100.0,
             "PAYROLL", "Income"),
            (4, "Chase", "Checking", "2026-03-08", -30.0,
             "TARGET", "Shopping"),
            (5, "Wells Fargo", "Checking", "2026-03-09", -10.0,
             "WALMART 2", "Groceries"),
        ],
    )


def test_sankey_flow_scoped_by_year(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = scoped_sankey.sankey_flow(con, year="2025")
    rows = {(r["source"], r["account"], r["category"]): r for r in out}
    assert set(rows) == {
        ("Wells Fargo", "Checking", "Groceries"),
        ("Wells Fargo", "Checking", "Dining"),
        ("Chase", "Checking", "Income"),
    }
    assert rows[("Wells Fargo", "Checking", "Groceries")]["debit"] == 50.0
    assert rows[("Chase", "Checking", "Income")]["credit"] == 100.0
    # 2026 rows are excluded.
    assert all("2026" not in str(r) for r in out)


def test_sankey_flow_scoped_by_month(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = scoped_sankey.sankey_flow(con, month="2025-02")
    rows = {(r["source"], r["account"], r["category"]): r for r in out}
    assert set(rows) == {
        ("Wells Fargo", "Checking", "Dining"),
        ("Chase", "Checking", "Income"),
    }
    assert rows[("Wells Fargo", "Checking", "Dining")]["debit"] == 20.0


def test_sankey_flow_empty_scope_returns_no_rows(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    assert scoped_sankey.sankey_flow(con, year="1999") == []
    assert scoped_sankey.sankey_flow(con, month="1999-01") == []


def test_sankey_flow_unscoped_matches_all_time(tmp_path):
    """No year/month = the Overview path — identical to the full payload."""
    con = _conn(tmp_path)
    _seed(con)
    out = scoped_sankey.sankey_flow(con)
    rows = {(r["source"], r["account"], r["category"]): r for r in out}
    assert set(rows) == {
        ("Wells Fargo", "Checking", "Groceries"),
        ("Wells Fargo", "Checking", "Dining"),
        ("Chase", "Checking", "Income"),
        ("Chase", "Checking", "Shopping"),
    }
    # Both Groceries rows (2025 + 2026) aggregate into one.
    assert rows[("Wells Fargo", "Checking", "Groceries")]["debit"] == 60.0


def test_sankey_merchants_flow_scoped_by_month(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = scoped_sankey.sankey_merchants_flow(con, month="2025-02")
    # The 2025-02 rows: Dining (WF) + Income (Chase).
    rows = {(r["source"], r["account"], r["category"], r["merchant"]): r
            for r in out}
    assert set(rows) == {
        ("Wells Fargo", "Checking", "Dining", ""),
        ("Chase", "Checking", "Income", ""),
    }
    assert rows[("Wells Fargo", "Checking", "Dining", "")]["debit"] == 20.0


def test_sankey_merchants_flow_scoped_by_year(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = scoped_sankey.sankey_merchants_flow(con, year="2026")
    rows = {(r["source"], r["account"], r["category"], r["merchant"]): r
            for r in out}
    assert set(rows) == {
        ("Chase", "Checking", "Shopping", ""),
        ("Wells Fargo", "Checking", "Groceries", ""),
    }
    assert rows[("Wells Fargo", "Checking", "Groceries", "")]["debit"] == 10.0
