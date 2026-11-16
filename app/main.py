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
    # The table must exist before any endpoint runs: uploads are the only
    # way data gets in, so an empty database is a valid state, not an error.
    con = get_con()
    try:
        con.execute(ingest.CREATE_TABLE_SQL)
        con.execute(rules.CREATE_RULES_SQL)
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
        # Custom rules override CSV categories on new rows (first match wins).
        rules_list = rules.load_rules(con)
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
                _, new = ingest.merge_txns(con, res.txns, rules=rules_list)
                entry["new"] = new
            report.append(entry)
        total = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        return {"files": report,
                "imported": sum(e["new"] for e in report),
                "total": total}
    finally:
        con.close()


@app.get("/api/summary")
def summary():
    con = get_con()
    try:
        return analytics.summary(con)
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


class CategorizeBody(BaseModel):
    ids: list[int]
    category: str


@app.post("/api/categorize")
def categorize(body: CategorizeBody):
    """Manually assign a standard category to specific transactions."""
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

    Custom rules run first (no LLM cost); the remaining unknowns go to the
    local LLM (or its built-in rule fallback).
    """
    con = get_con()
    try:
        unknown = analytics.unknown_categories(con, limit=100000)
        if not unknown:
            return {"classified": 0, "of": 0}
        rules_list = rules.load_rules(con)
        rest: list[dict] = []
        updated = 0
        for u in unknown:
            cat = rules.match_category(u["description"], rules_list)
            if cat:
                analytics.set_category(con, u["id"], cat)
                updated += 1
            else:
                rest.append(u)
        if rest:
            results = llm.classify_batch_llm(
                [u["description"] for u in rest])
            for u, cat in zip(rest, results):
                if cat:
                    analytics.set_category(con, u["id"], cat)
                    updated += 1
        con.commit()
        return {"classified": updated, "of": len(unknown)}
    finally:
        con.close()


@app.get("/api/transactions")
def transactions(year: str | None = None, month: str | None = None,
                 category: str | None = None, limit: int = 1000):
    """Individual transactions for a date range (newest first),
    optionally one normalized category."""
    con = get_con()
    try:
        return analytics.transactions(con, year, month, category, limit)
    finally:
        con.close()


@app.get("/api/standard-categories")
def standard_categories():
    return llm.STANDARD_CATEGORIES


# ---------------------------------------------------------------------------
# Custom category rules (keyword in description -> category, bulk override)
# ---------------------------------------------------------------------------
class RuleBody(BaseModel):
    pattern: str
    category: str


@app.get("/api/rules")
def list_rules():
    """Rules in evaluation order, with live match counts and an exact
    preview of how many transactions 'Apply' would change."""
    con = get_con()
    try:
        rule_list = rules.load_rules(con, enabled_only=False)
        enabled = [r for r in rule_list if r.enabled]
        matches, would_change = rules.rule_stats(con, enabled)
        return {
            "rules": [{"id": r.id, "pattern": r.pattern,
                       "category": r.category, "enabled": r.enabled,
                       "matches": matches.get(r.id, 0)}
                      for r in rule_list],
            "would_change": would_change,
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


@app.post("/api/rules/apply")
def apply_rules():
    """Bulk-apply all enabled rules: every matching transaction gets the
    winning rule's category, overwriting its current category. Returns the
    number of rows actually changed."""
    con = get_con()
    try:
        rule_list = rules.load_rules(con)
        updated = rules.apply_rules(con, rule_list)
        con.commit()
        return {"updated": updated, "rules": len(rule_list)}
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
