"""Overview sankey data layer: /api/sankey.

Per (source, account, effective category) movement split by sign —
``debit`` = SUM(ABS(amount)) over negative (spend) rows, ``credit``
over positive (income) rows, ``amount`` = debit + credit (kept for
backward compatibility) — for the 3-column sankey chart: debit
categories left, accounts middle, credit categories right. Ignored
rows (rule or date level), soft-deleted rows and zero-amount rows
are excluded, like every other aggregate.
"""

import duckdb

from app import analytics, date_ignores, ingest, imports, rules


def _conn(tmp_path, name="sankey.db"):
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
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
    return con


def _seed(con):
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)",
        [
            # WF Checking: spend in two categories.
            (1, "Wells Fargo", "Checking", "2026-01-05", -50.0,
             "WALMART", "Groceries"),
            (2, "Wells Fargo", "Checking", "2026-01-06", -20.0,
             "STARBUCKS", "Dining"),
            (3, "Wells Fargo", "Checking", "2026-01-07", -15.0,
             "WALMART 2", "Groceries"),
            # Chase Checking (same account NAME, different source):
            # spend + income.
            (4, "Chase", "Checking", "2026-01-08", -30.0,
             "TARGET", "Shopping"),
            (5, "Chase", "Checking", "2026-01-09", 100.0,
             "PAYROLL", "Income"),
            # Apple Card: a category with mixed signs (spend + a
            # refund row) must sum by ABS.
            (6, "Apple Card", "Apple Card", "2026-01-10", -80.0,
             "APPLE.COM/BILL", "Subscriptions"),
            (7, "Apple Card", "Apple Card", "2026-01-11", 25.0,
             "REFUND APPLE.COM", "Subscriptions"),
            # Rows that must NOT appear:
            (8, "Wells Fargo", "Checking", "2026-01-12", 0.0,
             "ZERO AMOUNT", "Groceries"),
            (9, "Chase", "Checking", "2026-01-13", -40.0,
             "NO CAT", None),
            (10, "Chase", "Checking", "2026-01-14", -60.0,
             "TRANSFER OUT", "Ignored"),
        ],
    )


def test_flows_grouped_by_account_and_category(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = {((r["source"], r["account"], r["category"])): r
           for r in analytics.sankey_flow(con)}
    # amount == debit + credit on every row (the compat gross total)
    for r in out.values():
        assert abs(r["amount"] - (r["debit"] + r["credit"])) < 1e-9
        assert r["debit"] >= 0 and r["credit"] >= 0
    assert out[("Wells Fargo", "Checking", "Groceries")]["amount"] == 65.0
    assert out[("Wells Fargo", "Checking", "Dining")]["amount"] == 20.0
    assert out[("Chase", "Checking", "Shopping")]["amount"] == 30.0
    # income is a band like spend (3-column chart: right side)
    inc = out[("Chase", "Checking", "Income")]
    assert inc["amount"] == 100.0
    assert inc["debit"] == 0.0 and inc["credit"] == 100.0
    # mixed signs split by sign: spend left, refund right
    sub = out[("Apple Card", "Apple Card", "Subscriptions")]
    assert sub["amount"] == 105.0
    assert sub["debit"] == 80.0 and sub["credit"] == 25.0
    assert out[("Wells Fargo", "Checking", "Groceries")]["debit"] == 65.0
    assert out[("Wells Fargo", "Checking", "Groceries")]["credit"] == 0.0
    # no null-category leak
    assert not any(c is None or c == "" for *_, c in out)
    con.close()


def test_uncategorized_is_labeled_not_dropped(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = {((r["source"], r["account"], r["category"])): r["amount"]
           for r in analytics.sankey_flow(con)}
    assert out[("Chase", "Checking", "Uncategorized")] == 40.0
    con.close()


def test_ignored_and_zero_rows_excluded(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = {(r["category"]) for r in analytics.sankey_flow(con)}
    assert "Ignored" not in out
    # zero-amount row: Groceries total comes from ids 1+3 only
    grouped = {(r["source"], r["account"], r["category"]): r["amount"]
               for r in analytics.sankey_flow(con)}
    assert grouped[("Wells Fargo", "Checking", "Groceries")] == 65.0
    con.close()


def test_soft_deleted_rows_excluded(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    con.execute("INSERT INTO deleted_transactions (txn_id) VALUES (1)")
    grouped = {(r["source"], r["account"], r["category"]): r["amount"]
               for r in analytics.sankey_flow(con)}
    assert grouped[("Wells Fargo", "Checking", "Groceries")] == 15.0
    con.close()


def test_read_time_rule_repoints_the_band(tmp_path):
    """A rule is a read-time layer: the band follows the EFFECTIVE
    category (manual override > rule > LLM > CSV original)."""
    con = _conn(tmp_path)
    _seed(con)
    rules.create_rule(con, pattern="WALMART", category="Shopping")
    grouped = {(r["source"], r["account"], r["category"]): r["amount"]
               for r in analytics.sankey_flow(con)}
    # id 1+3 (65.0) move Groceries -> Shopping; id 2 stays Dining
    assert grouped[("Wells Fargo", "Checking", "Shopping")] == 65.0
    assert ("Wells Fargo", "Checking", "Groceries") not in grouped
    con.close()


def test_manual_override_beats_rule(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    rules.create_rule(con, pattern="WALMART", category="Shopping")
    analytics.set_category(con, txn_id=1, category="Groceries",
                           source="manual")
    grouped = {(r["source"], r["account"], r["category"]): r["amount"]
               for r in analytics.sankey_flow(con)}
    assert grouped[("Wells Fargo", "Checking", "Groceries")] == 50.0
    assert grouped[("Wells Fargo", "Checking", "Shopping")] == 15.0
    con.close()
