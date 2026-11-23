"""Merchant layer: rules, overrides, precedence, provenance, views.

The merchant layer mirrors the category layer: a read-time rule
subquery (first match wins, position order) plus user overrides, and
the effective_transactions view applies user > rule > LLM > (nothing —
the imported rows carry no merchant).
"""
import duckdb
import pytest

from app import analytics, date_ignores, ingest, imports, merchants, rules


def _conn(tmp_path, name="merchants.db"):
    con = duckdb.connect(str(tmp_path / name))
    con.execute(ingest.CREATE_TABLE_SQL)
    con.execute(ingest.CREATE_OVERRIDES_SQL)
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


def _seed(con, rows):
    """rows: (source, account, date, amount, description, import_id)"""
    for i, (src, acct, d, amt, desc, iid) in enumerate(rows):
        con.execute(
            "INSERT INTO transactions "
            "(id, source, account, txn_date, amount, description, "
            " category, raw_category, currency, key, classified, import_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [100 + i, src, acct, d, amt, desc, "", "", "USD", "k" + str(i),
              False, iid])


def test_rule_assigns_merchant(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1),
                ("Apple Card", "Checking", "2026-01-11", -5.0, "UBER TRIP", 1)])
    merchants.create_rule(con, "walmart", "Walmart")
    view = {r[0]: r for r in con.execute(
        "SELECT id, merchant, merchant_source FROM effective_transactions").fetchall()}
    assert view[100][1] == "Walmart"
    assert view[100][2] == "rule"
    assert view[101][1] is None


def test_user_override_beats_rule(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.set_merchant(con, 100, "Home Depot", source="manual")
    r = con.execute(
        "SELECT merchant, merchant_source FROM effective_transactions "
        "WHERE id = 100").fetchone()
    assert r[0] == "Home Depot"
    assert r[1] == "manual"


def test_llm_override_loses_to_rule(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.set_merchant(con, 100, "Amazon", source="llm")
    r = con.execute(
        "SELECT merchant, merchant_source FROM effective_transactions "
        "WHERE id = 100").fetchone()
    assert r[0] == "Walmart"
    assert r[1] == "rule"


def test_llm_override_beats_nothing(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="llm")
    r = con.execute(
        "SELECT merchant, merchant_source FROM effective_transactions "
        "WHERE id = 100").fetchone()
    assert r[0] == "Walmart"
    assert r[1] == "llm"


def test_reorder_first_match_wins(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.create_rule(con, "walmart #123", "Walmart #123")
    # Positions: first-created is lower position? ensure_rule_position
    # backfills position=id; create_rule appends with max+1. So rule 2
    # ("walmart #123") was created later -> higher position -> lower
    # priority. The broader rule ("walmart") wins.
    r = con.execute(
        "SELECT merchant FROM effective_transactions WHERE id = 100").fetchone()
    assert r[0] == "Walmart"


def test_rule_scope(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.create_rule(con, "walmart", "Walmart",
                          source="Chase", account="Savings")
    r = con.execute(
        "SELECT merchant FROM effective_transactions WHERE id = 100").fetchone()
    assert r[0] is None


def test_merchant_rows_set_by(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1),
                ("Apple Card", "Checking", "2026-01-11", -5.0, "UBER TRIP", 1)])
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.set_merchant(con, 101, "Uber", source="manual")
    res = analytics.merchant_rows(con, include_uncategorized=True, set_by="all")
    by_id = {r["id"]: r for r in res["rows"]}
    assert by_id[100]["merchant"] == "Walmart"
    assert by_id[100]["set_by"] == "rule"
    assert by_id[101]["merchant"] == "Uber"
    assert by_id[101]["set_by"] == "manual"
    # pending = no merchant
    res2 = analytics.merchant_rows(con, include_uncategorized=True,
                                   set_by="uncategorized")
    assert res2["uncategorized"] == 0  # both rows have merchants
    res3 = analytics.merchant_rows(con, include_uncategorized=True,
                                   set_by="rule")
    assert [r["id"] for r in res3["rows"]] == [100]


def test_clear_override(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    merchants.set_merchant(con, 100, None)
    r = con.execute(
        "SELECT merchant FROM effective_transactions WHERE id = 100").fetchone()
    assert r[0] is None
