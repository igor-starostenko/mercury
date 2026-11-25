"""Classify endpoints classify ALL unknown transactions: distinct
descriptions are classified once each and the result is applied to every
transaction sharing that description. No 200-row cap."""

import duckdb
import pytest

import app.main as main
from app import date_ignores, ingest, imports, merchants, rules


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.duckdb")


def _con(db_path):
    con = duckdb.connect(db_path)
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


def _txn(con, tid, desc):
    con.execute(
        "INSERT INTO transactions (id, source, account, txn_date, amount, "
        "description, category, raw_category, currency, key, classified) "
        "VALUES (?, 'test', 'acc', '2026-01-01', 1.0, ?, NULL, NULL, 'USD', ?, false)",
        [tid, desc, f"key-{tid}"])


def _setup(db_path, rows):
    con = _con(db_path)
    for tid, desc in rows:
        _txn(con, tid, desc)
    con.close()


def test_merchants_classify_dedups_by_description(monkeypatch, db_path):
    _setup(db_path, [(1, "WALMART #1"), (2, "WALMART #1"),
                    (3, "UBER TRIP 08/12")])
    monkeypatch.setattr(main, "get_con", lambda: _con(db_path))
    calls = []

    def fake_batch(descs):
        calls.append(list(descs))
        return ["Walmart" if d.startswith("WALMART") else None for d in descs]

    monkeypatch.setattr(main.llm, "classify_merchants_batch_llm", fake_batch)
    result = main.classify_merchants(set_by="uncategorized")
    assert calls == [["WALMART #1", "UBER TRIP 08/12"]]
    assert result["classified"] == 2
    assert result["skipped"] == 1
    assert result["of"] == 3
    assert result["unique_descriptions"] == 2
    con = _con(db_path)
    merchants_by_id = dict(con.execute(
        "SELECT txn_id, merchant FROM merchant_overrides").fetchall())
    assert merchants_by_id == {1: "Walmart", 2: "Walmart"}
    skipped = con.execute(
        "SELECT id FROM transactions WHERE llm_skipped = true").fetchall()
    assert [r[0] for r in skipped] == [3]
    con.close()


def test_classify_endpoint_has_no_cap(monkeypatch, db_path):
    # 300 unknown rows — more than the old 200 cap.
    _setup(db_path, [(i, f"MERCHANT {i % 10}") for i in range(1, 301)])
    monkeypatch.setattr(main, "get_con", lambda: _con(db_path))
    calls = []

    def fake_batch(descs):
        calls.append(list(descs))
        return ["Shopping" for _ in descs]

    monkeypatch.setenv("MERCURY_LLM_ENDPOINT", "http://localhost:1234/v1")
    monkeypatch.setenv("MERCURY_LLM_MODEL", "test-model")
    monkeypatch.setattr(main.llm, "classify_batch_llm", fake_batch)
    result = main.classify()
    # 10 distinct descriptions, not 300 — and every row classified.
    assert sorted(calls[0]) == [f"MERCHANT {i}" for i in range(10)]
    assert result["classified"] == 300
    assert result["of"] == 300
    assert result["unique_descriptions"] == 10


def test_classify_loop_batches_and_reports_progress(monkeypatch, db_path):
    # 30 distinct descriptions (> BATCH_SIZE) — two batches, applied per
    # batch with progress written to the state dict after each.
    # (i - 1) // 2: exactly 2 transactions per description, ids 1..60.
    _setup(db_path, [(i, f"MERCHANT {(i - 1) // 2}") for i in range(1, 61)])
    monkeypatch.setattr(main, "get_con", lambda: _con(db_path))
    calls = []

    def fake_batch(descs):
        calls.append(list(descs))
        return ["Shopping" for _ in descs]

    monkeypatch.setenv("MERCURY_LLM_ENDPOINT", "http://localhost:1234/v1")
    monkeypatch.setenv("MERCURY_LLM_MODEL", "test-model")
    monkeypatch.setattr(main.llm, "classify_batch_llm", fake_batch)
    result = main.classify()
    assert len(calls) == 2
    # Row order is nondeterministic (all fixture rows share a txn_date, and
    # unknown_categories orders by txn_date DESC) — assert the batch sizes
    # and the union of all descriptions, not their positions.
    assert len(calls[0]) == 25
    assert len(calls[1]) == 5
    assert not (set(calls[0]) & set(calls[1]))
    assert set(calls[0] + calls[1]) == {f"MERCHANT {i}" for i in range(30)}
    assert result["classified"] == 60
    assert result["of"] == 60
    assert result["unique_descriptions"] == 30
    state = main._classify_state
    assert state["batches_done"] == 2
    assert state["total_batches"] == 2
    assert state["processed_descs"] == 30
    assert state["classified"] == 60
    assert state["remaining_unknown"] == 0
