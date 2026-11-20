"""GET /api/overrides data layer: the search window must not hide matches.

The Categories page previously sent ``limit=1000`` for every request,
search included, so a term matching >1000 rows was silently truncated.
The client now raises the limit while a search term is active; these
tests pin the server behavior that fix relies on — filter + sort run
over the full history, LIMIT applies after, and ``total`` is the true
filtered count.
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
