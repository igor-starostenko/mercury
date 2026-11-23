"""Rules: priority order, first-match matching, duplicate detection,
whitespace normalization, reorder, and the optional source/account scope."""

import duckdb
import pytest

from app import date_ignores, imports, ingest, merchants, rules


def _conn(tmp_path, name="rules.db"):
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


def _txn(con, source="Bank A", account="Checking", date="2025-01-15",
         amount=-10.0, description="WALMART", category="Uncategorized"):
    conid = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM transactions").fetchone()[0]
    con.execute(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)",
        [conid, source, account, date, amount, description, category])


def test_create_and_first_match(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "walmart", "Groceries")
    rules.create_rule(con, "u.s. bank", "Bank Fees")
    all_rules = rules.load_rules(con)
    con.close()

    # Case-insensitive, substring, whitespace-normalized on both sides.
    assert rules.match_category("WALMART", all_rules) == "Groceries"
    assert rules.match_category("payd to u.s. BANK        web", all_rules) \
        == "Bank Fees"
    assert rules.match_category("other store", all_rules) is None

    # matching_rules returns every hit in priority order (winner first).
    hits = rules.matching_rules("WALMART rewards to u.s. BANK        web",
                                all_rules)
    assert [r.pattern for r in hits] == ["walmart", "u.s. bank"]


def test_create_rule_position(tmp_path):
    con = _conn(tmp_path)
    r1 = rules.create_rule(con, "a", "X")
    r2 = rules.create_rule(con, "b", "Y")
    r3 = rules.create_rule(con, "c", "Z")
    assert (r1.position, r2.position, r3.position) == (1, 2, 3)
    assert [r.id for r in rules.load_rules(con)] == [1, 2, 3]
    con.close()


def test_reorder_rules(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "a", "X")
    rules.create_rule(con, "b", "Y")
    rules.create_rule(con, "c", "Z")
    # Move id 3 to the front: positions are rewritten densely.
    ordered = rules.reorder_rules(con, [3, 1, 2])
    assert [r.id for r in ordered] == [3, 1, 2]
    assert [r.position for r in ordered] == [0, 1, 2]
    assert [r.id for r in rules.load_rules(con)] == [3, 1, 2]
    con.close()


def test_reorder_rejects_bad_ids(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "a", "X")
    rules.create_rule(con, "b", "Y")
    for bad in ([1], [1, 1, 2], [1, 3], [99, 1, 2]):
        with pytest.raises(ValueError):
            rules.reorder_rules(con, bad)
    # Order untouched after the failed reorders.
    assert [r.id for r in rules.load_rules(con)] == [1, 2]
    con.close()


def test_duplicate_pattern_rejected(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "walmart", "Groceries")
    with pytest.raises(ValueError, match="already exists"):
        rules.create_rule(con, "WALMART", "Dining")
    rules.create_rule(con, "u.s. bank web", "Bank Fees")
    with pytest.raises(ValueError, match="already exists"):
        # Whitespace runs collapse to single spaces before comparison.
        rules.create_rule(con, "u.s. bank        web", "Other")
    con.close()


def test_duplicate_pattern_normalization(tmp_path):
    # A stored pattern may hold raw whitespace runs (created before
    # canonical storage); a new plain-spaced pattern must still collide.
    con = _conn(tmp_path)
    con.execute(
        "INSERT INTO category_rules (id, pattern, category, enabled, "
        "position) VALUES (1, 'U.S. BANK        WEB', 'Bank Fees', true, 1)")
    with pytest.raises(ValueError, match="already exists"):
        rules.create_rule(con, "u.s. bank web", "Other")
    con.close()


def test_validation(tmp_path):
    con = _conn(tmp_path)
    with pytest.raises(ValueError):
        rules.create_rule(con, "   ", "X")
    with pytest.raises(ValueError):
        rules.create_rule(con, "x", "   ")
    with pytest.raises(ValueError):
        rules.create_rule(con, "x" * 201, "X")
    with pytest.raises(ValueError):
        rules.create_rule(con, "x", "Y" * 65)
    con.close()


def test_pattern_whitespace_normalized_on_save(tmp_path):
    con = _conn(tmp_path)
    r = rules.create_rule(con, "  a   b  ", "X")
    assert r.pattern == "a b"
    stored = con.execute(
        "SELECT pattern FROM category_rules").fetchone()[0]
    assert stored == "a b"
    con.close()


def test_delete_rule(tmp_path):
    con = _conn(tmp_path)
    r = rules.create_rule(con, "walmart", "Groceries")
    assert rules.delete_rule(con, r.id) is True
    assert rules.delete_rule(con, r.id) is False
    assert rules.load_rules(con) == []
    con.close()


def test_update_rule_category(tmp_path):
    con = _conn(tmp_path, name="update.db")
    r = rules.create_rule(con, "walmart", "Groceries")
    r2 = rules.create_rule(con, "shell", "Food")
    _txn(con, description="WALMART")

    updated = rules.update_rule_category(con, r.id, "Beauty")
    assert updated.category == "Beauty"
    # Only the category moves: pattern, scope, and priority are
    # untouched — the rule still wins from the same slot.
    assert (updated.pattern, updated.source, updated.account) == \
        ("walmart", None, None)
    assert updated.position == r.position
    assert rules.load_rules(con)[0].category == "Beauty"
    # Read-time layer reshapes immediately; the other rule is untouched.
    cat = con.execute(
        "SELECT category FROM effective_transactions").fetchone()[0]
    assert cat == "Beauty"
    assert rules.load_rules(con)[1].category == "Food"

    # Reserved "Ignored" is a valid target (panel move), and setting the
    # same category again is a no-op that still returns the rule.
    assert rules.update_rule_category(con, r2.id, "Ignored").category \
        == "Ignored"
    assert rules.update_rule_category(con, r2.id, "Ignored").category \
        == "Ignored"
    # Unknown id -> None (404 upstream).
    assert rules.update_rule_category(con, 99, "Dining") is None
    # Bad categories -> ValueError (400 upstream).
    with pytest.raises(ValueError):
        rules.update_rule_category(con, r.id, "   ")
    with pytest.raises(ValueError):
        rules.update_rule_category(con, r.id, "Y" * 65)
    con.close()


def test_disabled_rule_skipped(tmp_path):
    con = _conn(tmp_path)
    r1 = rules.create_rule(con, "walmart", "Groceries")
    r2 = rules.create_rule(con, "walmart store", "Dining")
    con.execute("UPDATE category_rules SET enabled = false WHERE id = ?",
                [r1.id])
    enabled = rules.load_rules(con)
    assert [r.id for r in enabled] == [r2.id]
    assert rules.match_category("WALMART store", enabled) == "Dining"
    all_rules = rules.load_rules(con, enabled_only=False)
    assert [r.id for r in all_rules] == [r1.id, r2.id]
    # View applies the first matching ENABLED rule: with the
    # higher-priority one off, rows fall through to the lower one.
    # ("WALMART STORE" contains both patterns; r1 = "walmart" wins while
    # enabled, r2 = "walmart store" wins once r1 is disabled.)
    _txn(con, description="WALMART STORE")
    cat = con.execute(
        "SELECT category FROM effective_transactions").fetchone()[0]
    assert cat == "Dining"
    con.close()


# --------------------------------------------------------------------- scope

def test_scope_columns_migrated(tmp_path):
    """Old-schema table (pre-scope) gains source/account; NULL = All."""
    con = _conn(tmp_path, name="scope.db")
    con.execute("ALTER TABLE category_rules DROP COLUMN source")
    con.execute("ALTER TABLE category_rules DROP COLUMN account")
    con.execute("CREATE OR REPLACE VIEW effective_transactions AS "
                "SELECT id FROM transactions")  # placeholder; rebuilt below
    con.execute("INSERT INTO category_rules (id, pattern, category, "
                "enabled, position) VALUES (1, 'walmart', 'Groceries', "
                "true, 1)")
    rules.ensure_rule_scope(con)
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)  # needs the new columns
    con.close()
    con = duckdb.connect(str(tmp_path / "scope.db"))
    r = rules.load_rules(con)[0]
    assert r.source is None and r.account is None
    con.close()


def test_scope_matches_matrix():
    m = rules.scope_matches
    assert m(None, None, "Bank A", "Checking")        # All -> anything
    assert m("Bank A", None, "Bank A", "Checking")    # source only
    assert not m("Bank A", None, "Bank B", "Checking")
    assert m(None, "Checking", "Bank B", "Checking")  # account only
    assert not m(None, "Checking", "Bank B", "Savings")
    assert m("Bank A", "Checking", "Bank A", "Checking")
    assert not m("Bank A", "Checking", "Bank A", "Savings")
    assert m(None, None, None, None)


def test_create_rule_scoped(tmp_path):
    con = _conn(tmp_path)
    r = rules.create_rule(con, "walmart", "Groceries",
                          source="Bank A", account="Checking")
    assert r.source == "Bank A" and r.account == "Checking"
    # Same pattern, different scope: allowed (per-bank merchant mapping).
    r2 = rules.create_rule(con, "walmart", "Dining", source="Bank B")
    assert r2.source == "Bank B" and r2.account is None
    # Round-trip through the table.
    stored = {r_.id: r_ for r_ in rules.load_rules(con)}
    assert (stored[r.id].source, stored[r.id].account) == \
        ("Bank A", "Checking")
    assert (stored[r2.id].source, stored[r2.id].account) == ("Bank B", None)
    con.close()


def test_duplicate_scope_rejected(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "walmart", "Groceries", source="Bank A")
    with pytest.raises(ValueError, match="already exists"):
        rules.create_rule(con, "walmart", "Dining", source="Bank A")
    rules.create_rule(con, "u.s. bank web", "Bank Fees", source="Bank A")
    with pytest.raises(ValueError, match="already exists"):
        # Whitespace normalization still applies inside a scope.
        rules.create_rule(con, "u.s. bank        web", "Other",
                          source="Bank A")
    # Same pattern, different scopes: fine (per-bank mapping).
    rules.create_rule(con, "walmart", "Dining", source="Bank B")
    rules.create_rule(con, "walmart", "Entertainment")  # unscoped
    con.close()


def test_validate_scope():
    p, c, s, a = rules.validate("x", "y", source="   ", account=None)
    assert (s, a) == (None, None)
    p, c, s, a = rules.validate("x", "y", source=" Bank A ",
                                account="Checking")
    assert s == "Bank A" and a == "Checking"
    with pytest.raises(ValueError):
        rules.validate("x", "y", source="B" * 65)
    with pytest.raises(ValueError):
        rules.validate("x", "y", account="C" * 101)


def test_matching_rules_respects_scope(tmp_path):
    con = _conn(tmp_path)
    rules.create_rule(con, "walmart", "Groceries", source="Bank A")
    rules.create_rule(con, "walmart", "Dining", source="Bank B")
    rules.create_rule(con, "shell", "Food")  # unscoped
    all_rules = rules.load_rules(con)
    con.close()

    assert rules.match_category("walmart pickup", all_rules,
                                source="Bank A") == "Groceries"
    assert rules.match_category("walmart pickup", all_rules,
                                source="Bank B") == "Dining"
    assert rules.match_category("walmart pickup", all_rules,
                                source="Bank C") is None
    # Unscoped rule matches rows from any bank…
    assert rules.match_category("shell company check", all_rules,
                                source="Bank A") == "Food"
    # …and only unscoped rules can match when the row is unknown.
    assert rules.match_category("shell company check", all_rules) == "Food"
    assert rules.match_category("walmart pickup", all_rules) is None


def test_view_applies_rule_scope(tmp_path):
    """The effective_transactions view honors source/account scope."""
    con = _conn(tmp_path, name="view.db")
    _txn(con, source="Bank A", account="Checking")       # id 1: in scope
    _txn(con, source="Bank A", account="Savings")        # id 2: wrong acct
    _txn(con, source="Bank B", account="Checking")       # id 3: wrong src
    rules.create_rule(con, "walmart", "Groceries",
                      source="Bank A", account="Checking")
    cats = dict(con.execute(
        "SELECT id, category FROM effective_transactions").fetchall())
    assert cats[1] == "Groceries"
    assert cats[2] == "Uncategorized"
    assert cats[3] == "Uncategorized"
    srcs = dict(con.execute(
        "SELECT id, override_source FROM effective_transactions"
    ).fetchall())
    assert srcs[1] == "rule"
    assert srcs[2] is None and srcs[3] is None
    con.close()


def test_match_counts_and_net_respect_scope(tmp_path):
    con = _conn(tmp_path, name="net.db")
    _txn(con, source="Bank A", account="Checking", amount=-10.0)  # in scope
    _txn(con, source="Bank B", account="Checking", amount=-5.0)   # out
    _txn(con, source="Bank A", account="Savings", amount=-20.0)   # out
    rules.create_rule(con, "walmart", "Groceries",
                      source="Bank A", account="Checking")
    [r] = rules.load_rules(con)
    assert rules.match_counts(con, [r]) == {r.id: 1}
    assert rules.net_by_rule(con, [r]) == {r.id: -10.0}

    # Unscoped, the same pattern sees all three rows.
    con.execute("UPDATE category_rules SET source = NULL, account = NULL")
    [ru] = rules.load_rules(con)
    assert rules.match_counts(con, [ru]) == {ru.id: 3}
    assert rules.net_by_rule(con, [ru]) == {ru.id: -35.0}
    con.close()
