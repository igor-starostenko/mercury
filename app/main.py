"""Mercury web server.

Serves the static analytics dashboard and a small JSON API backed by DuckDB.
Local-only: binds to 127.0.0.1.

Data enters exclusively through POST /api/upload (CSVs picked in the UI);
nothing is scanned from disk in the background.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ingest, analytics, llm, rules

DB_PATH = ingest.DB_PATH
STATIC_DIR = Path(__file__).resolve().parent / "static"

MAX_UPLOAD_FILES = 200
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def get_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The tables must exist before any endpoint runs: uploads are the
    # only way data gets in, so an empty database is a valid state, not
    # an error. The effective_transactions view layers the override and
    # soft-delete tables on top of the imported rows.
    con = get_con()
    try:
        con.execute(ingest.CREATE_TABLE_SQL)
        con.execute(ingest.CREATE_OVERRIDES_SQL)
        ingest.ensure_override_source(con)
        con.execute(ingest.CREATE_DELETED_SQL)
        con.execute(rules.CREATE_RULES_SQL)
        con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
        # One-time migration: move pre-override categories (rule/LLM/
        # manual) out of the imported rows into category_overrides.
        moved = ingest.migrate_originals(con)
        if moved:
            print(f"  migrated    : moved {moved} pre-override categories "
                  f"to category_overrides")
        # One-time: attribute legacy overrides (no recorded source) to
        # 'llm' where their category matches the auto-classify fallback.
        attributed = ingest.backfill_override_source(con)
        if attributed:
            print(f"  backfilled  : attributed {attributed} legacy overrides "
                  f"to 'llm'")
    finally:
        con.close()
    yield


app = FastAPI(title="Mercury", lifespan=lifespan)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...),
                 source: str | None = Form(None),
                 source_map: str | None = Form(None)):
    """Import uploaded bank CSVs (individual files or a whole folder).

    Bank resolution per file: filename token -> CSV header signature ->
    user's pick. ``source`` applies a bank to every file that had none in
    its name; ``source_map`` is a JSON object {filename: bank} for picking
    per file. Files whose bank still can't be resolved come back with
    needs_source=true and the list of known banks so the UI can ask.

    Each file is merged into the transactions table by stable key: rows
    already present are skipped, so existing rows and their manual/LLM
    categories are never touched. Idempotent — re-uploading the same file
    adds nothing.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400, detail=f"too many files (max {MAX_UPLOAD_FILES})")
    smap: dict[str, str] = {}
    if source_map:
        try:
            parsed = json.loads(source_map)
            if not isinstance(parsed, dict):
                raise ValueError
            smap = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="source_map must be a JSON object {filename: bank}")
    con = get_con()
    report: list[dict] = []
    try:
        # No rule writes at import time: rules apply at read time, so new
        # rows are shaped exactly like existing ones.
        for f in files:
            name = f.filename or "unnamed.csv"
            data = await f.read()
            res = ingest.parse_file_bytes(name, data,
                                          source=smap.get(name) or source)
            entry = {"name": name, "source": res.source,
                     "rows": len(res.txns), "new": 0, "error": res.error,
                     "needs_source": res.needs_source, "note": res.note,
                     "banks": ingest.KNOWN_BANKS if res.needs_source else None}
            if res.error is None:
                _, new = ingest.merge_txns(con, res.txns)
                entry["new"] = new
            report.append(entry)
        total = con.execute(
            "SELECT COUNT(*) FROM effective_transactions").fetchone()[0]
        return {"files": report,
                "imported": sum(e["new"] for e in report),
                "total": total}
    finally:
        con.close()


@app.get("/api/summary")
def summary(year: str | None = None, month: str | None = None):
    """Totals for all data, or scoped to one year / one month."""
    con = get_con()
    try:
        return analytics.summary(con, year, month)
    finally:
        con.close()


@app.get("/api/monthly")
def monthly(year: str | None = None):
    """Spend/income by month, all time or one calendar year."""
    con = get_con()
    try:
        return {
            "spend": analytics.monthly_spend(con, year),
            "income": analytics.income_by_month(con, year),
            "by_category": analytics.spend_by_category_month(con, year),
        }
    finally:
        con.close()


@app.get("/api/year-totals")
def year_totals():
    """Per-year income/spend/net totals (home page year cards)."""
    con = get_con()
    try:
        return analytics.year_totals(con)
    finally:
        con.close()


@app.get("/api/weekly")
def weekly(month: str = ""):
    """Spend/income per calendar week (Mon–Sun) within one month."""
    if not MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    con = get_con()
    try:
        return analytics.weekly_breakdown(con, month)
    finally:
        con.close()


@app.get("/api/periods")
def periods():
    """Years and months present in the data, for page selectors."""
    con = get_con()
    try:
        return analytics.available_periods(con)
    finally:
        con.close()


@app.get("/api/categories")
def categories(year: str | None = None, month: str | None = None):
    con = get_con()
    try:
        return analytics.spend_by_category(con, year, month)
    finally:
        con.close()


@app.get("/api/accounts")
def accounts():
    con = get_con()
    try:
        return analytics.accounts(con)
    finally:
        con.close()


@app.get("/api/unknown")
def unknown(limit: int = 200):
    con = get_con()
    try:
        return analytics.unknown_categories(con, limit)
    finally:
        con.close()


@app.get("/api/overrides")
def overrides(limit: int = 200, include_uncategorized: bool = False):
    """Transactions with a category set — explicit or by live rule
    (newest first).

    Rule re-categorizations are read-time, so they appear here with
    ``set_by`` = 'rule' without any stored override. ``set_by`` is
    'manual' (a user edit), 'llm' (auto-classify) or 'rule' — plus
    'unknown' for overrides set before source tracking existed — and
    the CSV-provided ``original_category`` is shown alongside the
    effective one.

    With ``include_uncategorized=true`` the list also carries rows that
    have no override, no rule match and were missing a category in the
    CSV import; those have ``set_by = "uncategorized"`` (still
    uncategorized).
    """
    con = get_con()
    try:
        return analytics.override_rows(con, limit, include_uncategorized)
    finally:
        con.close()


class CategorizeBody(BaseModel):
    ids: list[int]
    category: str | None = None


@app.post("/api/categorize")
def categorize(body: CategorizeBody):
    """Set the category for one or more transactions.

    The imported (CSV) category in the row itself is never rewritten —
    the value is written to category_overrides, which takes precedence
    in every view and aggregate. null or an empty string removes the
    user's override, so the row reverts to its CSV-provided original.
    """
    con = get_con()
    try:
        for txn_id in body.ids:
            analytics.set_category(con, txn_id, body.category)
        con.commit()
        return {"updated": len(body.ids)}
    finally:
        con.close()


@app.post("/api/classify")
def classify():
    """Classify all currently-unknown transactions.

    Rules already apply at read time (effective_transactions view), so
    by definition no unknown matches a rule — each one goes straight to
    the local LLM (or its built-in rule fallback).
    """
    con = get_con()
    try:
        unknown = analytics.unknown_categories(con, limit=100000)
        if not unknown:
            return {"classified": 0, "of": 0}
        results = llm.classify_batch_llm([u["description"] for u in unknown])
        updated = 0
        for u, cat in zip(unknown, results):
            if cat:
                analytics.set_category(con, u["id"], cat, source="llm")
                updated += 1
        con.commit()
        return {"classified": updated, "of": len(unknown)}
    finally:
        con.close()


@app.get("/api/transactions")
def transactions(year: str | None = None, month: str | None = None,
                 category: str | None = None, limit: int = 1000):
    """Individual transactions for a date range (newest first),
    optionally one normalized category. Soft-deleted rows are excluded;
    each row carries ``category`` (effective, override-aware) and
    ``original_category`` (the CSV import's value, untouched)."""
    con = get_con()
    try:
        return analytics.transactions(con, year, month, category, limit)
    finally:
        con.close()


@app.delete("/api/transactions/{txn_id}")
def delete_transaction(txn_id: int):
    """Soft-delete a transaction: it disappears from every view and
    aggregate, but the imported row itself stays in the database
    untouched. Re-importing the source file will not resurrect it.
    Restore with POST /api/transactions/{id}/restore."""
    con = get_con()
    try:
        if not analytics.soft_delete(con, txn_id):
            raise HTTPException(status_code=404, detail="transaction not found")
        con.commit()
        return {"deleted": txn_id}
    finally:
        con.close()


@app.post("/api/transactions/{txn_id}/restore")
def restore_transaction(txn_id: int):
    """Undo a soft-delete."""
    con = get_con()
    try:
        if not analytics.restore(con, txn_id):
            raise HTTPException(
                status_code=404, detail="transaction is not deleted")
        con.commit()
        return {"restored": txn_id}
    finally:
        con.close()


@app.get("/api/standard-categories")
async def standard_categories():
    # Pickable categories for every UI dropdown: the standard vocabulary
    # plus the reserved "Ignored" marker (rule targets or manual assigns).
    return llm.UI_CATEGORIES


# ---------------------------------------------------------------------------
# Custom category rules (keyword in description -> category, bulk override)
# ---------------------------------------------------------------------------
class RuleBody(BaseModel):
    pattern: str
    category: str


@app.get("/api/rules")
def list_rules():
    """Rules in evaluation order, with live per-rule match counts.

    Rules are applied at read time, so nothing further has to happen
    for them to take effect."""
    con = get_con()
    try:
        rule_list = rules.load_rules(con, enabled_only=False)
        enabled = [r for r in rule_list if r.enabled]
        matches = rules.match_counts(con, enabled)
        return {
            "rules": [{"id": r.id, "pattern": r.pattern,
                       "category": r.category, "enabled": r.enabled,
                       "matches": matches.get(r.id, 0)}
                      for r in rule_list],
        }
    finally:
        con.close()


@app.post("/api/rules")
def create_rule(body: RuleBody):
    con = get_con()
    try:
        try:
            r = rules.create_rule(con, body.pattern, body.category)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": r.id, "pattern": r.pattern,
                "category": r.category, "enabled": True}
    finally:
        con.close()


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    con = get_con()
    try:
        if not rules.delete_rule(con, rule_id):
            raise HTTPException(status_code=404, detail="rule not found")
        return {"deleted": rule_id}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/year")
def year_page():
    return FileResponse(STATIC_DIR / "year.html")


@app.get("/categories")
def categories_page():
    return FileResponse(STATIC_DIR / "categories.html")


@app.get("/month")
def month_page():
    return FileResponse(STATIC_DIR / "month.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("MERCURY_HOST", "127.0.0.1")
    port = int(os.environ.get("MERCURY_PORT", "8000"))
    print(f"Mercury starting on http://{host}:{port}")
    print(f"  database  : {ingest.DB_PATH}")
    uvicorn.run(app, host=host, port=port)
