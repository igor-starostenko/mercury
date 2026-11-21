"""GET /api/overrides data layer: the search window must not hide matches.

The Categories page previously sent ``limit=1000`` for every request,
search included, so a term matching >1000 rows was silently truncated.
The client now raises the limit while a search term is active; these
tests pin the server behavior that fix relies on — filter + sort run
over the full history, LIMIT applies after, and ``total`` is the true
filtered count.

Search scope: with a search term and ``set_by='all'`` the query scans
the FULL history — not just the section's base set. Rows that only
carry the bank's CSV category (no override, no rule match) are not
listed by default but must be findable by search; they carry
``set_by='csv'`` (badge "bank"). Chip-scoped searches keep their
provenance scope, and a plain browse keeps the section's base set.
"""

import duckdb

from app import analytics, date_ignores, ingest, imports, rules

N = 2400  # comfortably above the old client-side 1000 window (1200 match)


def _conn(tmp_path, name="overrides.db"):
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


def _seed(con, n=N, desc="WALMART STORE"):
    """n uncategorized rows sharing one description substring."""
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)",
        [(i, "Bank A", "Checking", f"2025-{(i // 30) % 12 + 1:02d}-15",
          -(i % 97) - 1, desc if i % 2 == 0 else f"SHELL COMPANY {i}",
          "Uncategorized")
         for i in range(1, n + 1)])
    return sum(1 for i in range(1, n + 1) if i % 2 == 0)


def test_search_returns_all_matches_above_old_window(tmp_path):
    con = _conn(tmp_path)
    matches = _seed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True, q="WALMART")
    assert out["total"] == matches
    assert len(out["rows"]) == matches
    assert all(r["description"] == "WALMART STORE" for r in out["rows"])
    con.close()


def test_small_limit_truncates_but_reports_true_total(tmp_path):
    con = _conn(tmp_path)
    matches = _seed(con)
    out = analytics.override_rows(con, limit=1000,
                                  include_uncategorized=True, q="WALMART")
    # The old client behavior: silently capped at 1000, while total still
    # told the true filtered count (the "1000 of 1200 shown" hint).
    assert len(out["rows"]) == 1000
    assert out["total"] == matches
    con.close()


def test_sort_runs_before_limit(tmp_path):
    """LIMIT must pick the top of the SORTED set, not insertion order."""
    con = _conn(tmp_path)
    _seed(con)
    out = analytics.override_rows(con, limit=10, sort_by="amount",
                                  sort_dir="asc",
                                  include_uncategorized=True, q="WALMART")
    amounts = [r["amount"] for r in out["rows"]]
    assert amounts == sorted(amounts)
    # The most-negative amounts exist in the seed, so a top-10 by amount
    # ascending must start well below the last-inserted rows.
    assert amounts[0] <= -95
    con.close()


def _seed_mixed(con):
    """A spread of provenance shapes in one fresh schema:

    1  manual override (category re-picked by the user)
    2  llm override (auto-classify)
    3  legacy override, source NULL (predates source tracking)
    4  bank CSV category only, never touched        -> 'csv'
    5  bank CSV category only, never touched        -> 'csv'
    6  no CSV category at all                       -> 'uncategorized'
    7  rule match, no stored override               -> 'rule'
    """
    rows = [
        (1, "Bank A", "Checking", "2025-03-01", -4.5, "STARBUCKS COFFEE", "Food"),
        (2, "Bank A", "Checking", "2025-03-02", -25.0, "DOCTORS CLINIC", "Uncategorized"),
        (3, "Bank A", "Checking", "2025-03-03", -12.0, "OLDSCHOOL MARKET", "Groceries"),
        (4, "Bank A", "Checking", "2025-03-04", -20.3, "LYFT RIDE *ABC123", "Transportation"),
        (5, "Bank A", "Checking", "2025-03-05", -18.1, "LYFT RIDE *XYZ789", "Transportation"),
        (6, "Bank A", "Checking", "2025-03-06", -7.7, "MISC FEE", "Uncategorized"),
        (7, "Bank A", "Checking", "2025-03-07", -30.0, "AIRBNB *STAY", "Travel"),
    ]
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT INTO category_overrides (txn_id, category, source) "
        "VALUES (?,?,?)",
        [(1, "Dining", "manual"),
         (2, "Medical", "llm"),
         (3, "Groceries", None)])  # legacy: source tracking predates it
    con.execute(
        "INSERT INTO category_rules (id, pattern, category, enabled, "
        "position, source, account) VALUES (1, 'AIRBNB', 'Lodging', "
        "true, 0, NULL, NULL)")


def test_search_surfaces_bank_csv_rows(tmp_path):
    """A typed search finds rows the list doesn't show by default."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True, q="lyft")
    assert out["total"] == 2
    assert {r["id"] for r in out["rows"]} == {4, 5}
    assert all(r["set_by"] == "csv" for r in out["rows"])
    assert all(r["category"] == "Transportation"
               and r["original_category"] == "Transportation"
               for r in out["rows"])
    con.close()


def test_plain_browse_keeps_section_scope(tmp_path):
    """No search: base set only — bank-CSV rows stay out of the list."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True)
    # 1 manual, 2 llm, 3 legacy, 6 pending, 7 rule — but NOT 4/5 (csv).
    assert out["total"] == 5
    assert {r["id"] for r in out["rows"]} == {1, 2, 3, 6, 7}
    con.close()


def test_search_total_matches_full_history(tmp_path):
    """total = every matching row, including the not-listed csv ones."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True, q="lyft")
    assert out["total"] == 2
    # The uncategorized counter is q-independent (full-history pending).
    assert out["uncategorized"] == 1
    con.close()


def test_chip_scoped_search_keeps_provenance(tmp_path):
    """Search inside "You" still means user-set rows only."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="manual", q="starbucks")
    assert out["total"] == 1
    assert out["rows"][0]["id"] == 1
    assert out["rows"][0]["set_by"] == "manual"
    # The lyft (csv) rows cannot leak into a manual-scoped search.
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="manual", q="lyft")
    assert out["total"] == 0 and out["rows"] == []
    con.close()


def test_set_by_values_per_row(tmp_path):
    """Every provenance shape keeps its badge, including legacy 'unknown'."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    browse = analytics.override_rows(con, limit=100000,
                                     include_uncategorized=True)
    by_id = {r["id"]: r["set_by"] for r in browse["rows"]}
    assert by_id == {1: "manual", 2: "llm", 3: "unknown",
                     6: "uncategorized", 7: "rule"}
    searched = analytics.override_rows(con, limit=100000,
                                       include_uncategorized=True,
                                       q="lyft")
    assert {r["id"]: r["set_by"] for r in searched["rows"]} == {4: "csv",
                                                                5: "csv"}
    con.close()


def test_set_by_csv_filters_bank_rows(tmp_path):
    """The 'Bank' filter lists rows whose category is the CSV original."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="csv")
    assert out["total"] == 2
    assert {r["id"] for r in out["rows"]} == {4, 5}
    assert all(r["set_by"] == "csv" for r in out["rows"])
    # Searches stay scoped to bank rows...
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="csv", q="lyft")
    assert {r["id"] for r in out["rows"]} == {4, 5}
    # ...and a user-set row sharing no CSV category can't leak in.
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="csv", q="starbucks")
    assert out["total"] == 0 and out["rows"] == []
    con.close()


def test_set_by_manual_excludes_legacy_null_source(tmp_path):
    """'You' means source='manual' — a legacy NULL-source override is
    'unknown', not a user edit."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  include_uncategorized=True,
                                  set_by="manual")
    assert {r["id"] for r in out["rows"]} == {1}
    con.close()


def test_uncategorized_filter_independent_of_include_flag(tmp_path):
    """'Unknown' selects pending rows on its own — the UI's include flag
    only controls whether they also appear in an 'all' list."""
    con = _conn(tmp_path)
    _seed_mixed(con)
    out = analytics.override_rows(con, limit=100000,
                                  set_by="uncategorized")
    assert {r["id"] for r in out["rows"]} == {6}
    con.close()
