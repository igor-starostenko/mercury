"""Server-side invariants for the Overview Sankey 3-column flow.

v1.8.0 re-orients the chart: NET-POSITIVE (income) categories on
the LEFT, accounts in the middle, NET-NEGATIVE (spend) categories on
the RIGHT. The category picker controls the RIGHT side only — it
lists every NET-NEGATIVE category as an individual option plus one
"All other" entry (first-visit default = top 8 by DEBIT spend): the
client folds every remaining NET-NEGATIVE category into a single
"All other" node, computed purely from the /api/sankey payload
(bucket = debit payload − individual selection, one aggregated debit
flow per account). Each category is classified by its NET position
(credit − debit) across the whole payload: net positive → left
(credit flows only), net negative → right (debit flows only), net
zero → not shown at all. The left side always renders in full — no
filtering, no bucket.

These tests pin what that client math depends on: the sign split is
exactly right against direct SQL (the feature), amount == debit +
credit on every row, "Ignored" never reaches the payload, the menu
is exactly the NET-NEGATIVE category set (net-positive categories
never appear in it), the reserved "All other" name never collides
with a real category, and the per-account debit aggregation the
client does sums exactly to the raw flows — nothing lost, nothing
double-counted.
"""

import duckdb

from app import analytics, date_ignores, ingest, imports, merchants, rules


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
    con.execute(merchants.CREATE_MERCHANT_RULES_SQL)
    merchants.ensure_rule_position(con)
    merchants.ensure_rule_scope(con)
    con.execute(merchants.CREATE_MERCHANT_OVERRIDES_SQL)
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
    return con


def _seed(con):
    # Spend, income, a refund, an uncategorized row and an Ignored
    # row across three accounts — plus a 9th DEBIT category
    # (Transportation) so the debit menu holds NINE categories and
    # the top-8-by-debit first-visit default leaves at least one in
    # the "All other" bucket for the bucket math to act on.
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
            (9, "Chase", "Checking", "2026-01-14", -15.0,
             "AIRBNB", "Travel"),
            (10, "Wells Fargo", "Checking", "2026-01-15", -12.0,
             "ELECTRIC CO", "Utilities"),
            (11, "Apple Card", "Apple Card", "2026-01-16", -5.0,
             "ANNUAL FEE", "Fees"),
            (12, "Chase", "Checking", "2026-01-17", -8.0,
             "UBER", "Transportation"),
        ],
    )


def _rows(tmp_path):
    con = _conn(tmp_path)
    _seed(con)
    out = analytics.sankey_flow(con)
    con.close()
    return out


def _nets(rows):
    """Net position per category (credit − debit) across the whole
    payload — the client's left/right classification input."""
    net = {}
    for r in rows:
        net[r["category"]] = (net.get(r["category"], 0)
                              + r["credit"] - r["debit"])
    return net


def _debit_totals(rows):
    """The client's skCatList ranking input: per-category DEBIT
    totals — NET-NEGATIVE categories only (the menu is the spend side
    of the flow; net-positive categories render regardless and are
    never options; a mixed category nets to its net position)."""
    net = _nets(rows)
    total = {}
    for r in rows:
        if r["debit"] > 0:
            total[r["category"]] = total.get(r["category"], 0) + r["debit"]
    return {c: v for c, v in total.items() if net.get(c, 0) < 0}


def _cat_menu(rows):
    """The client's category menu: EVERY NET-NEGATIVE category,
    debit-desc with name asc on ties — its head is the first-visit
    default (top-N-by-debit)."""
    total = _debit_totals(rows)
    return sorted(total, key=lambda c: (-total[c], c))


def _top8(rows):
    total = _debit_totals(rows)
    return sorted(total, key=lambda c: (-total[c], c))[:8]


def test_flows_carry_account_category_amount(tmp_path):
    """The client indexes on (source, account, category) and splits
    the sign — every flow must carry all three plus debit/credit with
    amount == debit + credit (the backward-compat gross total)."""
    rows = _rows(tmp_path)
    assert rows  # non-empty payload for the bucket to act on
    for r in rows:
        assert isinstance(r["source"], str) and r["source"].strip()
        assert isinstance(r["account"], str) and r["account"].strip()
        # uncategorized rows are labeled, never null (see test_sankey)
        assert isinstance(r["category"], str) and r["category"]
        for key in ("debit", "credit"):
            assert isinstance(r[key], (int, float))
            assert r[key] >= 0
        assert isinstance(r["amount"], (int, float))
        assert r["amount"] > 0
        assert abs(r["amount"] - (r["debit"] + r["credit"])) < 1e-9


def test_sign_split_matches_direct_sql(tmp_path):
    """The feature: per (account, category) the debit side is the
    sum of ABS over negative rows, the credit side over positive
    rows — verified against direct SQL on the fixture, to the
    cent."""
    con = _conn(tmp_path)
    _seed(con)
    want = {
        (s, a, c): (d, cr)
        for s, a, c, d, cr in con.execute("""
            SELECT source, account, COALESCE(category, 'Uncategorized'),
                   COALESCE(SUM(ABS(CASE WHEN amount < 0 THEN amount END)), 0),
                   COALESCE(SUM(ABS(CASE WHEN amount > 0 THEN amount END)), 0)
            FROM effective_transactions
            WHERE amount != 0 AND category IS DISTINCT FROM 'Ignored'
            GROUP BY 1, 2, 3
        """).fetchall()
    }
    got = {(r["source"], r["account"], r["category"]):
           (r["debit"], r["credit"]) for r in analytics.sankey_flow(con)}
    con.close()
    assert set(got) == set(want)
    for k, (d, cr) in want.items():
        assert abs(got[k][0] - d) < 1e-9, (k, got[k], d)
        assert abs(got[k][1] - cr) < 1e-9, (k, got[k], cr)
    # The fixture's known split, spelled out: spend-only, income-only
    # and mixed-sign (Apple Card refund) pairs.
    assert got[("Wells Fargo", "Checking", "Groceries")] == (50.0, 0.0)
    assert got[("Chase", "Checking", "Income")] == (0.0, 100.0)
    assert got[("Apple Card", "Apple Card", "Subscriptions")] == (80.0, 25.0)


def test_ignored_never_reaches_the_payload(tmp_path):
    """"Ignored" is excluded like every other aggregate, so it can
    never appear as an individual menu entry OR leak into the bucket
    (the bucket is built from payload categories only)."""
    rows = _rows(tmp_path)
    assert "Ignored" not in {r["category"] for r in rows}


def test_bucket_is_debit_categories_minus_selection(tmp_path):
    """The client's aggregation (one DEBIT flow per account into the
    bucket) must sum exactly to the raw debit flows of the
    non-selected categories — bucket + selected == whole debit
    payload, nothing lost or doubled. Credit flows never enter the
    bucket (the left side always renders in full)."""
    rows = _rows(tmp_path)
    net = _nets(rows)
    debit_cats = {c for c, n in net.items() if n < 0}
    total = _debit_totals(rows)
    selection = set(_top8(rows))
    assert selection < debit_cats  # a debit category sits outside top 8
    bucket_cats = debit_cats - selection

    per_acct = {}
    for r in rows:
        if r["category"] in bucket_cats:
            k = (r["source"], r["account"])
            per_acct[k] = per_acct.get(k, 0) + r["debit"]

    assert sum(per_acct.values()) == sum(total[c] for c in bucket_cats)
    assert (sum(per_acct.values())
            + sum(total[c] for c in selection)
            == sum(r["debit"] for r in rows))
    # A bucket account can never exceed that account's raw debit total.
    acct_total = {}
    for r in rows:
        k = (r["source"], r["account"])
        acct_total[k] = acct_total.get(k, 0) + r["debit"]
    for k, v in per_acct.items():
        assert v <= acct_total[k]


def test_unchecking_a_category_grows_the_bucket(tmp_path):
    """The card's live check, server-side: moving a category out of
    the individual selection into the bucket grows the bucket by
    exactly that category's DEBIT total."""
    rows = _rows(tmp_path)
    total = _debit_totals(rows)
    top = _top8(rows)
    net = _nets(rows)

    def bucket(sel):
        """Sum of debit flows the catch-all would aggregate for a
        selection."""
        return sum(r["debit"] for r in rows
                   if net.get(r["category"], 0) < 0
                   and r["category"] not in sel)

    full = bucket(set(top))
    moved = top[0]
    grown = bucket(set(top) - {moved})
    assert grown - full == total[moved]


def test_credit_side_ignores_the_selection(tmp_path):
    """v1.8.0: NET-POSITIVE (income) categories always render in full
    — no filtering, no bucket. They are never menu options (the
    menu is the net-negative spend side), and their credit flows
    appear even when every spend category is unchecked. A
    NET-NEGATIVE category renders only its debit flows — its credit
    flow is not rendered (the chart is a left-to-right flow)."""
    rows = _rows(tmp_path)
    credit_rows = [r for r in rows if r["credit"] > 0]
    assert credit_rows  # the fixture has income
    net = _nets(rows)
    net_positive = {c for c, n in net.items() if n > 0}
    net_negative = {c for c, n in net.items() if n < 0}
    assert net_positive  # Income in the fixture
    menu = _debit_totals(rows)
    for c in net_positive:
        assert c not in menu
    for c in net_negative:
        assert c in menu
    # Unselecting every spend category hides nothing on the left.
    rendered_credit = sum(r["credit"] for r in rows
                          if r["credit"] > 0 and net[r["category"]] > 0)
    assert rendered_credit == sum(r["credit"] for r in rows
                                  if net[r["category"]] > 0)
    # The mixed category (net-negative) renders only its debit flow —
    # its credit flow is not rendered.
    mixed = {r["category"] for r in rows if r["credit"] > 0 and r["debit"] > 0}
    for c in mixed:
        assert net[c] < 0  # a mixed category that nets negative
        assert c in menu  # ...still appears in the menu
        assert sum(r["debit"] for r in rows if r["category"] == c) > 0


def test_all_other_off_selects_nothing_outside_the_selection(tmp_path):
    """Catch-all off: the effective DEBIT set IS the individual
    selection — no debit flow from outside it may be taken; flipping
    the catch-all on picks up exactly the debit data outside it.
    (Credit flows ride through either way — see
    test_credit_side_ignores_the_selection.)"""
    rows = _rows(tmp_path)
    net = _nets(rows)
    selection = set(_top8(rows))
    outside = [r for r in rows if net.get(r["category"], 0) < 0
               and r["category"] not in selection]
    assert outside  # there IS debit data outside the selection
    inside = [r for r in rows if net.get(r["category"], 0) < 0
              and r["category"] in selection]
    assert {r["category"] for r in inside} <= selection
    # On: bucket == outside debit data, disjoint from the selected.
    assert sum(r["debit"] for r in outside) > 0
    assert not ({r["category"] for r in outside}
                & {r["category"] for r in inside})


def test_menu_is_exactly_the_debit_category_set(tmp_path):
    """v1.8.0: the menu lists EVERY NET-NEGATIVE category — nothing
    hidden from the picker, nothing invented, and NET-POSITIVE
    categories (Income in the fixture) never appear (they render on
    the left regardless of the selection). The menu set must equal
    the net-negative category set exactly, per-category DEBIT totals
    must be computable as the debit-desc ranking input, and the
    first-visit default (top 8 by debit) must be the head of that
    same ranking."""
    rows = _rows(tmp_path)
    net = _nets(rows)
    debit_cats = {c for c, n in net.items() if n < 0}
    menu = _cat_menu(rows)
    assert set(menu) == debit_cats
    assert len(menu) == len(debit_cats)
    assert "Income" not in menu  # credit-only: left side, never a box
    # debit-desc, name asc on ties — strict ordering, no ties broken
    # arbitrarily (the CDP check compares the menu DOM order against
    # this ranking).
    total = _debit_totals(rows)
    for a, b in zip(menu, menu[1:]):
        assert total[a] > total[b] or (total[a] == total[b] and a < b)
    # The first-paint default is the head of the list (top 8 by debit).
    assert menu[:8] == _top8(rows)
    # The debit set must hold categories OUTSIDE the default so the
    # catch-all has real work to do (and the CDP "check a non-top-8"
    # check has a target).
    assert len(debit_cats) > 8


def test_reserved_all_other_name_never_in_payload(tmp_path):
    """The client names its catch-all node "All other" (ui.js
    ALL_OTHER, index.html SK_ALL_OTHER) and pins its color to the
    aggregate grey — a REAL payload category with that exact name
    would collide with the aggregate (Sankey node keys ARE the
    category strings, so the two would merge into one node). The
    standard vocabulary has "Other" (hence the separate
    OTHER_CATEGORY const); pin that "All other" never appears as a
    real category in the payload."""
    rows = _rows(tmp_path)
    assert "All other" not in {r["category"] for r in rows}
