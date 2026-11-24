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
    con.execute(merchants.CREATE_MERCHANT_COLORS_SQL)
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


def test_distinct_merchants_stats_and_colors(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #1", 1),
                ("Apple Card", "Checking", "2026-01-11", -5.0, "WALMART #2", 1),
                ("Apple Card", "Checking", "2026-01-12", 100.0, "UBER TRIP", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    merchants.set_merchant(con, 101, "Walmart", source="manual")
    merchants.set_merchant(con, 102, "Uber", source="manual")
    out = merchants.distinct_merchants(con)
    by_name = {d["merchant"]: d for d in out}
    assert set(by_name) == {"Walmart", "Uber"}
    w = by_name["Walmart"]
    assert w["n"] == 2
    assert w["spend"] == 17.0
    assert w["net"] == -17.0
    assert w["color"].startswith("#") and len(w["color"]) == 7
    # color is stable across calls (persisted, not re-rolled)
    colors_first = {d["merchant"]: d["color"] for d in merchants.distinct_merchants(con)}
    colors_second = {d["merchant"]: d["color"] for d in merchants.distinct_merchants(con)}
    assert colors_first == colors_second == {"Walmart": w["color"],
                                             "Uber": by_name["Uber"]["color"]}
    # sorted by spend desc
    assert out[0]["merchant"] == "Walmart"


def test_set_color_roundtrip_and_validation(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #1", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    color = merchants.set_color(con, "Walmart", "#112233")
    assert color == "#112233"
    assert merchants.load_colors(con)["Walmart"] == "#112233"
    with pytest.raises(ValueError):
        merchants.set_color(con, "Walmart", "not-a-color")
    with pytest.raises(ValueError):
        merchants.set_color(con, "", "#112233")


def test_rename_merchant_retags_overrides_rules_and_color(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #1", 1),
                ("Apple Card", "Checking", "2026-01-11", -5.0, "UBER TRIP", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    merchants.create_rule(con, "uber", "Uber")
    merchants.set_color(con, "Uber", "#123456")
    result = merchants.rename_merchant(con, "Uber", "UberEats")
    assert result["rules"] == 1
    assert result["overrides"] == 0
    rules = merchants.load_rules(con, enabled_only=False)
    assert [r.merchant for r in rules] == ["UberEats"]
    # color inherits the old entry
    assert merchants.load_colors(con).get("UberEats") == "#123456"
    assert merchants.load_colors(con).get("Uber") is None
    # rename to an existing merchant: target color wins, old is dropped
    merchants.set_merchant(con, 101, "Walmart", source="manual")
    merchants.set_color(con, "Walmart", "#abcdef")
    merchants.rename_merchant(con, "UberEats", "Walmart")
    assert merchants.load_colors(con)["Walmart"] == "#abcdef"
    assert merchants.load_colors(con).get("UberEats") is None


def test_remove_merchant_drops_overrides_rules_color(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #1", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    merchants.create_rule(con, "walmart", "Walmart")
    merchants.set_color(con, "Walmart", "#123456")
    result = merchants.remove_merchant(con, "Walmart")
    assert result["overrides"] == 1
    assert result["rules"] == 1
    assert result["color"] == 1
    assert merchants.load_rules(con, enabled_only=False) == []
    assert merchants.load_colors(con) == {}
    r = con.execute(
        "SELECT merchant FROM effective_transactions WHERE id = 100").fetchone()
    assert r[0] is None


def test_transactions_rows_carry_effective_merchant(tmp_path):
    con = _conn(tmp_path)
    _seed(con, [("Apple Card", "Checking", "2026-01-10", -12.0, "WALMART #123", 1),
                ("Apple Card", "Checking", "2026-01-11", -5.0, "UBER TRIP", 1)])
    merchants.set_merchant(con, 100, "Walmart", source="manual")
    rows = analytics.transactions(con, merchant="Walmart", limit=10)
    assert len(rows) == 1
    assert rows[0]["merchant"] == "Walmart"
    assert rows[0]["merchant_source"] == "manual"
    # The merchant filter still selects on the effective merchant, and a
    # rule-driven merchant surfaces through the same field.
    merchants.create_rule(con, "uber", "Uber")
    rows = analytics.transactions(con, merchant="Uber", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == 101
    assert rows[0]["merchant_source"] == "rule"
