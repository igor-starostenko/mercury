"""Account colors: default bank primary colors, user overrides from
Manage Imports, effective color in /api/accounts and the imports list.
"""
import duckdb

from app import analytics, date_ignores, ingest, imports, merchants, rules


def _conn(tmp_path, name="colors.db"):
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
    imports.ensure_color_column(con)
    con.execute(merchants.CREATE_MERCHANT_RULES_SQL)
    merchants.ensure_rule_position(con)
    merchants.ensure_rule_scope(con)
    con.execute(merchants.CREATE_MERCHANT_OVERRIDES_SQL)
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
    return con


def _seed(con):
    imp_id = imports.resolve_import(con, "chase.csv", "Checking")
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, description, category, import_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "Chase", "Checking", "2026-01-05", -50.0, "WALMART", "Groceries", imp_id),
            (2, "Wells Fargo", "Savings", "2026-01-06", 100.0, "PAYROLL", "Income", None),
        ],
    )


def test_ensure_color_column_idempotent(tmp_path):
    con = _conn(tmp_path)
    imports.ensure_color_column(con)  # second call: no-op, no error
    cols = [r[1] for r in con.execute("PRAGMA table_info('csv_imports')").fetchall()]
    assert "color" in cols


def test_normalize_hex_color():
    assert imports.normalize_hex_color(None) is None
    assert imports.normalize_hex_color("") is None
    assert imports.normalize_hex_color("   ") is None
    assert imports.normalize_hex_color("#fff") == "#ffffff"
    assert imports.normalize_hex_color("#D71E28") == "#d71e28"
    assert imports.normalize_hex_color("#d71e28") == "#d71e28"
    import pytest
    with pytest.raises(ValueError):
        imports.normalize_hex_color("red")
    with pytest.raises(ValueError):
        imports.normalize_hex_color("#12345")
    with pytest.raises(ValueError):
        imports.normalize_hex_color("#1234567")


def test_effective_color_defaults_and_override():
    # Unknown bank falls back to the neutral grey.
    assert imports.effective_color(None, "Chase") == "#117aca"
    assert imports.effective_color(None, "Wells Fargo") == "#d71e28"
    assert imports.effective_color(None, "Apple Card") == "#55565a"
    assert imports.effective_color(None, "US Bank") == "#c8102e"
    assert imports.effective_color(None, "Some Other Bank") == "#5a6772"
    # A stored override always wins, whatever the bank.
    assert imports.effective_color("#123456", "Chase") == "#123456"


def test_list_imports_effective_color(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    rows = imports.list_imports(con)
    # The Chase import has no stored color → bank default.
    chase = [r for r in rows if r["source"] == "Chase"]
    assert len(chase) == 1
    assert chase[0]["color"] == "#117aca"
    assert chase[0]["custom_color"] is None


def test_set_and_clear_import_color(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    imp_id = imports.resolve_import(con, "chase.csv", "Checking")

    assert imports.set_import_color(con, imp_id, "#FF8800") == {
        "id": imp_id, "color": "#ff8800"}
    rows = imports.list_imports(con)
    chase = [r for r in rows if r["source"] == "Chase"]
    assert chase[0]["color"] == "#ff8800"
    assert chase[0]["custom_color"] == "#ff8800"

    # Clearing (None) restores the bank default.
    assert imports.set_import_color(con, imp_id, None) == {
        "id": imp_id, "color": None}
    rows = imports.list_imports(con)
    chase = [r for r in rows if r["source"] == "Chase"]
    assert chase[0]["color"] == "#117aca"
    assert chase[0]["custom_color"] is None

    # Unknown import → None, no error.
    assert imports.set_import_color(con, 9999, "#ff8800") is None


def test_accounts_include_color(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    rows = analytics.accounts(con)
    by_source = {r["source"]: r for r in rows}
    # Chase Checking: the import's account label; no stored color → default.
    assert by_source["Chase"]["color"] == "#117aca"
    # Wells Fargo Savings: no import (legacy row) → bank default.
    assert by_source["Wells Fargo"]["color"] == "#d71e28"

    # A stored override on the import flows through to /api/accounts.
    imp_id = imports.resolve_import(con, "chase.csv", "Checking")
    imports.set_import_color(con, imp_id, "#123456")
    rows = analytics.accounts(con)
    by_source = {r["source"]: r for r in rows}
    assert by_source["Chase"]["color"] == "#123456"
    # The other bank is untouched.
    assert by_source["Wells Fargo"]["color"] == "#d71e28"


def test_accounts_color_scoped_by_source(tmp_path):
    """Two banks can share an account label ("Checking") — a stored
    override on one bank's import must not leak into the other."""
    con = _conn(tmp_path)
    con.execute(
        "INSERT INTO csv_imports (id, filename, account, imported_at) "
        "VALUES (1, 'chase.csv', 'Checking', now())")
    con.execute(
        "INSERT INTO csv_imports (id, filename, account, imported_at) "
        "VALUES (2, 'wf.csv', 'Checking', now())")
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, description, category, import_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "Chase", "Checking", "2026-01-05", -50.0, "WALMART", "Groceries", 1),
            (2, "Wells Fargo", "Checking", "2026-01-06", -90.0, "PAYROLL", "Income", 2),
        ],
    )
    imports.set_import_color(con, 1, "#ff8800")
    rows = analytics.accounts(con)
    by_source = {r["source"]: r for r in rows}
    assert by_source["Chase"]["color"] == "#ff8800"
    assert by_source["Wells Fargo"]["color"] == "#d71e28"

    # Clearing the override restores the bank default for that bank only.
    imports.set_import_color(con, 1, None)
    rows = analytics.accounts(con)
    by_source = {r["source"]: r for r in rows}
    assert by_source["Chase"]["color"] == "#117aca"
    assert by_source["Wells Fargo"]["color"] == "#d71e28"
