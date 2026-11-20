"""Merge semantics: the source listing is authoritative.

- Identical rows (same date/account/amount/description) listed N times in
  one file are N real transactions — all inserted, none silently dropped.
- Re-uploading the same file is a no-op (count-for-count).
- Overlapping extracts across files collapse: a key listed once per file
  lands in the table once.
- A later file listing a key more often than the database holds adds the
  surplus only.
- Existing rows are never touched by a merge (categories survive).
"""

import duckdb
from datetime import date

from app import ingest


def _t(desc, amt, src="Chase", acct="Checking", d=date(2026, 3, 4)):
    return ingest.Txn(source=src, account=acct, date=d,
                      amount=amt, description=desc)


def test_identical_rows_all_inserted():
    con = duckdb.connect(":memory:")
    txns = [_t("AMAZON.COM", -49.99)] * 3 + [_t("LYFT RIDE", -12.50)]
    total, new = ingest.merge_txns(con, txns)
    assert (total, new) == (4, 4)
    n = con.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE description = 'AMAZON.COM'").fetchone()[0]
    assert n == 3
    con.close()


def test_reupload_is_noop():
    con = duckdb.connect(":memory:")
    txns = [_t("AMAZON.COM", -49.99)] * 2 + [_t("LYFT RIDE", -12.50)]
    ingest.merge_txns(con, txns)
    total, new = ingest.merge_txns(con, txns)
    assert (total, new) == (3, 0)
    con.close()


def test_overlapping_extracts_collapse():
    con = duckdb.connect(":memory:")
    a = [_t("WALMART", -81.20, d=date(2026, 2, 1)),
         _t("SHELL GAS", -50.00, d=date(2026, 2, 2))]
    b = [_t("WALMART", -81.20, d=date(2026, 2, 1)),
         _t("STARBUCKS", -6.75, d=date(2026, 2, 3))]
    total, new_a = ingest.merge_txns(con, a)
    total, new_b = ingest.merge_txns(con, b)
    assert (total, new_a, new_b) == (3, 2, 1)
    con.close()


def test_larger_file_adds_surplus():
    con = duckdb.connect(":memory:")
    once = [_t("ATM WITHDRAWAL", -100.00, d=date(2026, 1, 5))]
    twice = once * 2
    ingest.merge_txns(con, once)
    total, new = ingest.merge_txns(con, twice)
    assert (total, new) == (2, 1)
    # Re-uploading the two-row file must not add a third copy.
    total, new = ingest.merge_txns(con, twice)
    assert (total, new) == (2, 0)
    con.close()


def test_existing_rows_untouched():
    con = duckdb.connect(":memory:")
    ingest.merge_txns(con, [_t("WALMART", -81.20)])
    con.execute("UPDATE transactions SET category = 'Groceries'")
    ingest.merge_txns(con, [_t("WALMART", -81.20), _t("SPOTIFY", -10.99)])
    cat = con.execute(
        "SELECT category FROM transactions "
        "WHERE description = 'WALMART'").fetchone()[0]
    assert cat == "Groceries"
    total = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert total == 2
    con.close()
