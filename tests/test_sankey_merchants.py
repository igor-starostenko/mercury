"""Merchant column of the Overview sankey: /api/sankey-merchants.

Per (source, account, effective category, effective merchant)
movement split by sign — ``debit`` = SUM(ABS(amount)) over negative
(spend) rows, ``credit`` over positive (income) rows, ``amount`` =
debit + credit — for the 4-column sankey chart (credit cats left,
accounts middle, debit cats right, merchants far right). The merchant
is the effective merchant (user/legacy-rule override > rule layer >
LLM > nothing); rows with no effective merchant carry merchant=''.
Ignored rows, soft-deleted rows and zero-amount rows are excluded,
like every other aggregate.
"""

import duckdb

from app import analytics, date_ignores, ingest, imports, merchants, rules


def _conn(tmp_path, name="sankey_merchants.db"):
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
            # WF Checking: spend, one WALMART-ish row (rule-assignable),
            # one unmatchable row.
            (1, "Wells Fargo", "Checking", "2026-01-05", -50.0,
             "WALMART #123", "Groceries"),
            (2, "Wells Fargo", "Checking", "2026-01-06", -20.0,
             "STARBUCKS", "Dining"),
            # Chase Checking: spend + income, unmatchable.
            (3, "Chase", "Checking", "2026-01-08", -30.0,
             "TARGET", "Shopping"),
            (4, "Chase", "Checking", "2026-01-09", 100.0,
             "PAYROLL", "Income"),
            # Ignored row: must not appear.
            (5, "Chase", "Checking", "2026-01-13", -40.0,
             "IGNORED ROW", "Ignored"),
        ],
    )


def _grouped(rows):
    return {(r["source"], r["account"], r["category"], r["merchant"]): r
            for r in rows}


def test_rows_carry_merchant_and_sign_split(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    merchants.create_rule(con, "walmart", "Walmart")
    out = _grouped(analytics.sankey_merchants_flow(con))
    # every row keeps amount == debit + credit, signs non-negative
    for r in out.values():
        assert abs(r["amount"] - (r["debit"] + r["credit"])) < 1e-9
        assert r["debit"] >= 0 and r["credit"] >= 0
    # the WALMART row splits under its merchant; the rest carry ''
    groceries = out[("Wells Fargo", "Checking", "Groceries", "Walmart")]
    assert groceries["debit"] == 50.0 and groceries["credit"] == 0.0
    assert out[("Wells Fargo", "Checking", "Dining", "")]["debit"] == 20.0
    assert out[("Chase", "Checking", "Shopping", "")]["debit"] == 30.0
    assert out[("Chase", "Checking", "Income", "")]["credit"] == 100.0
    # no rule match -> no merchant row with '' ... it DOES: the
    # unmatchable rows carry merchant='' (the category still renders)
    assert ("Chase", "Checking", "Income", "") in out
    # ignored rows excluded
    assert not any("Ignored" in k for k in out)
    con.close()


def test_override_beats_rule(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.set_merchant(con, 1, "Home Depot", source="manual")
    out = _grouped(analytics.sankey_merchants_flow(con))
    assert ("Wells Fargo", "Checking", "Groceries", "Home Depot") in out
    assert ("Wells Fargo", "Checking", "Groceries", "Walmart") not in out
    con.close()


def test_soft_deleted_rows_excluded(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    merchants.create_rule(con, "walmart", "Walmart")
    con.execute("INSERT INTO deleted_transactions (txn_id) VALUES (1)")
    out = _grouped(analytics.sankey_merchants_flow(con))
    assert not any(k[3] == "Walmart" for k in out)
    con.close()
