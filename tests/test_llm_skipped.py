"""llm_skipped flag: skipped rows are excluded from future classify runs
and the flag clears whenever the row is categorized or tagged."""

import duckdb

from app import analytics, date_ignores, ingest, imports, merchants, rules


def _con():
    con = duckdb.connect(":memory:")
    con.execute(ingest.CREATE_TABLE_SQL)
    con.execute(ingest.CREATE_OVERRIDES_SQL)
    ingest.ensure_override_source(con)
    ingest.ensure_llm_skipped_column(con)
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


def _txn(con, tid=1, desc="mystery merchant", category=None):
    con.execute(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category, raw_category, currency, key, classified) "
        "VALUES (?, 'test', 'acc', '2026-01-01', 1.0, ?, ?, NULL, 'USD', ?, false)",
        [tid, desc, category, f"key-{tid}"])


def test_ensure_llm_skipped_column_adds_column_to_legacy_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, source VARCHAR, account VARCHAR,
            txn_date DATE, amount DOUBLE, description VARCHAR,
            category VARCHAR, raw_category VARCHAR, currency VARCHAR,
            key VARCHAR, classified BOOLEAN DEFAULT false
        )""")
    ingest.ensure_llm_skipped_column(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info('transactions')").fetchall()}
    assert "llm_skipped" in cols


def test_unknown_categories_excludes_skipped():
    con = _con()
    _txn(con, 1, "unknown a")
    _txn(con, 2, "unknown b")
    con.execute("UPDATE transactions SET llm_skipped = true WHERE id = 2")
    rows = analytics.unknown_categories(con)
    assert [r["id"] for r in rows] == [1]


def test_set_category_clears_skipped_flag():
    con = _con()
    _txn(con, 1, "unknown a")
    con.execute("UPDATE transactions SET llm_skipped = true WHERE id = 1")
    analytics.set_category(con, 1, "Food", source="llm")
    flag, = con.execute("SELECT llm_skipped FROM transactions WHERE id = 1").fetchone()
    assert flag is False


def test_set_merchant_clears_skipped_flag():
    con = _con()
    _txn(con, 1, "unknown a")
    merchants.ensure_overrides_table(con)
    con.execute("UPDATE transactions SET llm_skipped = true WHERE id = 1")
    merchants.set_merchant(con, 1, "Walmart", source="llm")
    flag, = con.execute("SELECT llm_skipped FROM transactions WHERE id = 1").fetchone()
    assert flag is False
