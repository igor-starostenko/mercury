"""Mercury web server.

Serves the static analytics dashboard and a small JSON API backed by DuckDB.
Local-only: binds to 127.0.0.1.

Data enters exclusively through POST /api/upload (CSVs picked in the UI);
nothing is scanned from disk in the background.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import ingest, analytics, llm

DB_PATH = ingest.DB_PATH
STATIC_DIR = Path(__file__).resolve().parent / "static"

MAX_UPLOAD_FILES = 200


def get_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The table must exist before any endpoint runs: uploads are the only
    # way data gets in, so an empty database is a valid state, not an error.
    con = get_con()
    try:
        con.execute(ingest.CREATE_TABLE_SQL)
    finally:
        con.close()
    yield


app = FastAPI(title="Mercury", lifespan=lifespan)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Import uploaded bank CSVs (individual files or a whole folder).

    Each file is parsed by its filename and merged into the transactions
    table by stable key: rows already present are skipped, so existing rows
    and their manual/LLM categories are never touched. Idempotent —
    re-uploading the same file adds nothing.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400, detail=f"too many files (max {MAX_UPLOAD_FILES})")
    con = get_con()
    report: list[dict] = []
    try:
        for f in files:
            name = f.filename or "unnamed.csv"
            data = await f.read()
            txns, source, error = ingest.parse_file_bytes(name, data)
            entry = {"name": name, "source": source,
                     "rows": len(txns), "new": 0, "error": error}
            if error is None:
                _, new = ingest.merge_txns(con, txns)
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
def monthly():
    con = get_con()
    try:
        return {
            "spend": analytics.monthly_spend(con),
            "income": analytics.income_by_month(con),
            "by_category": analytics.spend_by_category_month(con),
        }
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
    """Classify all currently-unknown transactions with the local LLM."""
    con = get_con()
    try:
        unknown = analytics.unknown_categories(con, limit=100000)
        if not unknown:
            return {"classified": 0, "method": "none"}
        results = llm.classify_batch_llm([u["description"] for u in unknown])
        updated = 0
        for u, cat in zip(unknown, results):
            if cat:
                analytics.set_category(con, u["id"], cat)
                updated += 1
        con.commit()
        return {"classified": updated, "of": len(unknown)}
    finally:
        con.close()


@app.get("/api/transactions")
def transactions(year: str | None = None, month: str | None = None,
                 limit: int = 1000):
    """Individual transactions for a date range (newest first)."""
    con = get_con()
    try:
        return analytics.transactions(con, year, month, limit)
    finally:
        con.close()


@app.get("/api/standard-categories")
def standard_categories():
    return llm.STANDARD_CATEGORIES


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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
