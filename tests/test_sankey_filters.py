"""Server-side invariants for the Overview Sankey "All other" bucketing.

The category picker lists EVERY payload category as an individual
option plus one "All other" entry (v1.6.0: v1.5.0 capped the menu at
the top 8 by spend — the first-visit DEFAULT is still the top 8):
the client folds every remaining payload category into a single
"All other" node, computed purely from the /api/sankey payload
(bucket = payload categories − individual selection, one aggregated
flow per account). These tests pin what that client math depends on:
every flow carries a usable account + category + amount, "Ignored"
never reaches the payload, the menu is exactly the payload category
set (spend-desc ranking input), the reserved "All other" name never
collides with a real category, and the per-account aggregation the
client does sums exactly to the raw flows — nothing lost, nothing
double-counted.
"""

import duckdb

from app import analytics, date_ignores, ingest, imports, rules


def _conn(tmp_path, name="sankey_filters.db"):
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
    # Same shapes test_sankey uses: spend, income, a refund, an
    # uncategorized row and an Ignored row across three accounts.
    con.executemany(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category) VALUES (?,?,?,?,?,?,?)",
        [
            (1, "Wells Fargo", "Checking", "2026-01-05", -50.0,
             "WALMART", "Groceries"),
            (2, "Wells Fargo", "Checking", "2026-01-06", -20.0,
             "STARBUCKS", "Dining"),
            (3, "Chase", "Checking", "2026-01-08", -30.0,
             "TARGET", "Shopping"),
            (4, "Chase", "Checking", "2026-01-09", 100.0,
             "PAYROLL", "Income"),
            (5, "Apple Card", "Apple Card", "2026-01-10", -80.0,
             "APPLE.COM/BILL", "Subscriptions"),
            (6, "Apple Card", "Apple Card", "2026-01-11", 25.0,
             "REFUND APPLE.COM", "Subscriptions"),
            (7, "Wells Fargo", "Checking", "2026-01-12", -10.0,
             "NO CAT", None),
            (8, "Chase", "Checking", "2026-01-13", -60.0,
             "TRANSFER OUT", "Ignored"),
            # Three more categories so the payload holds NINE distinct
            # ones — the top-8 menu must leave at least one in the
            # "All other" bucket for the bucket math to act on.
            (9, "Chase", "Checking", "2026-01-14", -15.0,
             "AIRBNB", "Travel"),
            (10, "Wells Fargo", "Checking", "2026-01-15", -12.0,
             "ELECTRIC CO", "Utilities"),
            (11, "Apple Card", "Apple Card", "2026-01-16", -5.0,
             "ANNUAL FEE", "Fees"),
        ],
    )


def _rows(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = analytics.sankey_flow(con)
    con.close()
    return out


def _cat_totals(rows):
    """The client's skCatList ranking input: per-category flow totals."""
    total = {}
    for r in rows:
        total[r["category"]] = total.get(r["category"], 0) + r["amount"]
    return total


def _cat_menu(rows):
    """The client's category menu (v1.6.0): EVERY payload category,
    spend-desc with name asc on ties — its head is the first-visit
    default (top-N-by-spend)."""
    total = _cat_totals(rows)
    return sorted(total, key=lambda c: (-total[c], c))


def _top8(rows):
    total = _cat_totals(rows)
    return sorted(total, key=lambda c: (-total[c], c))[:8]


def test_flows_carry_account_category_amount(tmp_path):
    """The bucket math indexes on (source, account, category, amount) —
    every flow must carry all four, non-empty and positive."""
    rows = _rows(tmp_path)
    assert rows  # non-empty payload for the bucket to act on
    for r in rows:
        assert isinstance(r["source"], str) and r["source"].strip()
        assert isinstance(r["account"], str) and r["account"].strip()
        # uncategorized rows are labeled, never null (see test_sankey)
        assert isinstance(r["category"], str) and r["category"]
        assert isinstance(r["amount"], (int, float))
        assert r["amount"] > 0


def test_ignored_never_reaches_the_payload(tmp_path):
    """"Ignored" is excluded like every other aggregate, so it can
    never appear as an individual menu entry OR leak into the bucket
    (the bucket is built from payload categories only)."""
    rows = _rows(tmp_path)
    assert "Ignored" not in {r["category"] for r in rows}


def test_bucket_is_payload_categories_minus_selection(tmp_path):
    """The client's aggregation (one flow per account into the bucket)
    must sum exactly to the raw flows of the non-selected categories
    — bucket + selected == whole payload, nothing lost or doubled."""
    rows = _rows(tmp_path)
    cats = {r["category"] for r in rows}
    total = _cat_totals(rows)
    selection = set(_top8(rows))
    assert selection < cats  # payload holds categories outside the top 8
    bucket_cats = cats - selection

    per_acct = {}
    for r in rows:
        if r["category"] in bucket_cats:
            k = (r["source"], r["account"])
            per_acct[k] = per_acct.get(k, 0) + r["amount"]

    assert sum(per_acct.values()) == sum(total[c] for c in bucket_cats)
    assert (sum(per_acct.values())
            + sum(total[c] for c in selection)
            == sum(r["amount"] for r in rows))
    # A bucket account can never exceed that account's raw payload total.
    acct_total = {}
    for r in rows:
        k = (r["source"], r["account"])
        acct_total[k] = acct_total.get(k, 0) + r["amount"]
    for k, v in per_acct.items():
        assert v <= acct_total[k]


def test_unchecking_a_category_grows_the_bucket(tmp_path):
    """The card's live check, server-side: moving a category out of the
    individual selection into the bucket grows the bucket by exactly
    that category's total."""
    rows = _rows(tmp_path)
    total = _cat_totals(rows)
    top = _top8(rows)

    def bucket(sel):
        """Sum of flows the catch-all would aggregate for a selection."""
        return sum(r["amount"] for r in rows
                   if r["category"] not in sel)

    full = bucket(set(top))
    dropped = set(top)
    dropped.discard("Housing" if "Housing" in top else top[0])
    grown = bucket(dropped)
    moved = top[0] if "Housing" not in top else "Housing"
    assert grown - full == total[moved]


def test_all_other_off_selects_nothing_outside_the_selection(tmp_path):
    """Catch-all off: the effective set IS the individual selection —
    no flow from outside it may be taken; flipping the catch-all on
    picks up exactly the data outside it."""
    rows = _rows(tmp_path)
    selection = set(_top8(rows))
    outside = [r for r in rows if r["category"] not in selection]
    assert outside  # there IS data outside the selection
    inside = [r for r in rows if r["category"] in selection]
    assert {r["category"] for r in inside} <= selection
    # On: bucket == outside data, disjoint from the selected flows.
    assert sum(r["amount"] for r in outside) > 0
    assert not ({r["category"] for r in outside}
                & {r["category"] for r in inside})


def test_menu_is_exactly_the_payload_category_set(tmp_path):
    """v1.6.0: the menu lists EVERY payload category — nothing hidden
    from the picker, nothing invented. The menu set must equal the
    payload category set exactly (no duplicates, every flow's category
    present), per-category totals must be computable as the spend-desc
    ranking input, and the first-visit default (top 8) must be the
    head of that same ranking."""
    rows = _rows(tmp_path)
    payload_cats = {r["category"] for r in rows}
    menu = _cat_menu(rows)
    assert set(menu) == payload_cats
    assert len(menu) == len(payload_cats)
    # spend-desc, name asc on ties — strict ordering, no ties broken
    # arbitrarily (the CDP check compares the menu DOM order against
    # this ranking).
    total = _cat_totals(rows)
    for a, b in zip(menu, menu[1:]):
        assert total[a] > total[b] or (total[a] == total[b] and a < b)
    # The first-paint default is the head of the list (top 8 by spend).
    assert menu[:8] == _top8(rows)
    # The payload must hold categories OUTSIDE the default so the
    # catch-all has real work to do (and the CDP "check a non-top-8"
    # check has a target).
    assert len(payload_cats) > 8


def test_reserved_all_other_name_never_in_payload(tmp_path):
    """The client names its catch-all node "All other" (ui.js ALL_OTHER,
    index.html SK_ALL_OTHER) and pins its color to the aggregate grey —
    a REAL payload category with that exact name would collide with the
    aggregate (Sankey node keys ARE the category strings, so the two
    would merge into one node). The standard vocabulary has "Other"
    (hence the separate OTHER_CATEGORY const); pin that "All other"
    never appears as a real category in the payload."""
    rows = _rows(tmp_path)
    assert "All other" not in {r["category"] for r in rows}
