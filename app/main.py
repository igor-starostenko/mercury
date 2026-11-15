"""Mercury web server.

Serves the static analytics dashboard and a small JSON API backed by DuckDB.
Local-only: binds to 127.0.0.1.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ingest, analytics, llm

app = FastAPI(title="Mercury")

DB_PATH = ingest.DB_PATH
STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


def ensure_loaded() -> None:
    """Import CSVs on first request if the DB is empty/missing."""
    con = get_con()
    try:
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
        except duckdb.Error:
            n = 0
        if n == 0:
            print("No data yet - importing CSVs from", ingest.INPUT_DIR)
            ingest.load_all(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/api/reimport")
def reimport():
    """Re-scan the input folder and rebuild the table (idempotent)."""
    con = get_con()
    try:
        count = ingest.load_all(con)
        return {"imported": count}
    finally:
        con.close()


@app.get("/api/summary")
def summary():
    ensure_loaded()
    con = get_con()
    try:
        return analytics.summary(con)
    finally:
        con.close()


@app.get("/api/monthly")
def monthly():
    ensure_loaded()
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
    ensure_loaded()
    con = get_con()
    try:
        return analytics.spend_by_category(con, year, month)
    finally:
        con.close()


@app.get("/api/accounts")
def accounts():
    ensure_loaded()
    con = get_con()
    try:
        return analytics.accounts(con)
    finally:
        con.close()


@app.get("/api/unknown")
def unknown(limit: int = 200):
    ensure_loaded()
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
    ensure_loaded()
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
    ensure_loaded()
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
    ensure_loaded()
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
    print(f"  input dir : {ingest.INPUT_DIR}")
    print(f"  database  : {ingest.DB_PATH}")
    uvicorn.run(app, host=host, port=port)
