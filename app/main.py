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
import time
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ingest, analytics, llm, rules, date_ignores, imports

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
        rules.ensure_rule_position(con)
        # Scope columns must exist before the view: the view's rule
        # subquery references category_rules.source/account.
        rules.ensure_rule_scope(con)
        # Date ignores must exist before the view: the view's
        # date_ignored column references the table.
        con.execute(date_ignores.CREATE_DATE_IGNORES_SQL)
        # Import tracking must exist before the view is (re)created: the
        # view maps every row's display account to its import's current
        # label (csv_imports.account) for the Manage Imports rename.
        con.execute(imports.CREATE_IMPORTS_SQL)
        imports.ensure_import_column(con)
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
        # One-time: pre-tracking rows (imported before csv_imports existed)
        # are grouped into synthetic imports, so every row belongs to one
        # entry in Manage Imports (labelable and deletable as a unit).
        legacy = imports.backfill_imports(con)
        if legacy:
            print(f"  backfilled  : created {legacy} import records "
                  f"for legacy rows")
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
                # Record which file these rows came from (Manage Imports):
                # the whole import can be re-labeled or deleted later as a
                # unit. Re-uploads of a known file find the existing
                # record, so its current label survives (idempotent).
                imp_id = (
                    imports.resolve_import(con, name, res.txns[0].account)
                    if res.txns else None)
                _, new = ingest.merge_txns(con, res.txns, imp_id)
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
    """Spend/income by month, all time or one calendar year.

    ``flow_by_category`` carries spend and income per (month, category)
    for the stacked-by-category chart view."""
    con = get_con()
    try:
        return {
            "spend": analytics.monthly_spend(con, year),
            "income": analytics.income_by_month(con, year),
            "by_category": analytics.spend_by_category_month(con, year),
            "flow_by_category": analytics.flow_by_category(con, year=year),
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
def weekly(month: str = "", by_category: bool = False):
    """Spend/income per calendar week (Mon–Sun) within one month.

    Default shape (list of week rows) is unchanged. With
    ``by_category=true`` the response is ``{"weeks": [...],
    "flow_by_category": [{bucket, category, spend, income}]}`` —
    per (week, category) spend and income for the stacked chart view.
    """
    if not MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    con = get_con()
    try:
        weeks = analytics.weekly_breakdown(con, month)
        if not by_category:
            return weeks
        return {"weeks": weeks,
                "flow_by_category": analytics.flow_by_category(
                    con, month=month, weekly=True)}
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


@app.get("/api/sankey")
def sankey():
    """Account → category gross flows for the Overview sankey chart:
    per (source, account, effective category), SUM(ABS(amount)) over
    non-ignored, non-zero rows — all time."""
    con = get_con()
    try:
        return analytics.sankey_flow(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Manage Imports: re-label or delete a CSV import as a unit
# ---------------------------------------------------------------------------

class ImportAccountBody(BaseModel):
    account: str


@app.get("/api/imports")
def list_imports():
    """All CSV imports: each uploaded file (or a synthetic "earlier
    import" group for DBs predating tracking), with its current account
    label, row count, and date span. Backs the Manage Imports panel."""
    con = get_con()
    try:
        rows = imports.list_imports(con)
        return {"imports": rows,
                "total": sum(r["n"] for r in rows)}
    finally:
        con.close()


@app.post("/api/imports/{import_id}/account")
def rename_import_account(import_id: int, body: ImportAccountBody):
    """Re-label an import's account for all of its rows (e.g. a second
    checking account at the same bank). The stored rows are never
    rewritten — the label lives on the import record and the effective
    view maps it in at read time, so re-uploading the same file still
    dedupes under the current name."""
    acct = (body.account or "").strip()
    if not acct:
        raise HTTPException(status_code=400, detail="account name is empty")
    if len(acct) > 100:
        raise HTTPException(
            status_code=400, detail="account name too long (max 100)")
    con = get_con()
    try:
        if imports.rename_import(con, import_id, acct) is None:
            raise HTTPException(status_code=404, detail="no such import")
        con.commit()
        return {"id": import_id, "account": acct}
    finally:
        con.close()


@app.delete("/api/imports/{import_id}")
def delete_import(import_id: int, source: str | None = None):
    """Delete an import: every row that came from this file — only its
    rows, plus those rows' per-transaction overrides and soft-delete
    tombstones. Category rules, date-ignores, and every other import are
    untouched. The record itself goes once it has no rows left.
    `source` scopes the delete to one bank's rows of a synthetic legacy
    import (those group rows from several sources)."""
    con = get_con()
    try:
        if not con.execute(
                "SELECT COUNT(*) FROM csv_imports WHERE id = ?",
                [import_id]).fetchone()[0]:
            raise HTTPException(status_code=404, detail="no such import")
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                counts = imports.delete_import(con, import_id, source)
                con.commit()
                return {"deleted": import_id, **counts}
            except duckdb.TransactionException as e:
                # A concurrent writer (e.g. a double-submit) touched the same
                # rows. The failed statement already aborts the transaction;
                # roll back defensively, then retry — the operation converges.
                last_err = e
                try:
                    con.rollback()
                except duckdb.TransactionException:
                    pass
                time.sleep(0.1 * (attempt + 1))
        raise HTTPException(status_code=503, detail="delete conflicted with a concurrent write; try again")
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
def overrides(limit: int = 200, include_uncategorized: bool = False,
              set_by: str = "all", sort_by: str = "date",
              sort_dir: str | None = None, q: str | None = None):
    """Transactions with a category set — explicit or by live rule.

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

    Without ``q`` the rows are the section's base set above. With a
    search and ``set_by=all`` the search scans the FULL history —
    including rows that only carry the bank's CSV category and are not
    listed by default; those have ``set_by = "csv"`` (badge "bank").
    Chip-scoped searches keep their provenance scope. Filtering and
    sorting run in SQL over the full history — ``LIMIT`` is applied
    after them, so a filter can never be applied to a truncated page.
    ``set_by`` filters to one of all / manual / llm / rule / csv /
    uncategorized; ``sort_by`` is date / description / amount
    with ``sort_dir`` asc or desc (defaults: date desc, description
    asc, amount desc); ``q`` is a case-insensitive substring on the
    whitespace-normalized description (the rule-matching preview).
    The response is ``{"rows": [...], "total": N, "uncategorized":
    M}`` — the window, the count of rows matching the filter, and the
    full-history count of still-uncategorized rows.
    """
    con = get_con()
    try:
        return analytics.override_rows(con, limit, include_uncategorized,
                                       set_by, sort_by, sort_dir, q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    the local LLM. Returns 503 when the endpoint or the model is not
    configured (no silent keyword fallback in that case); the fallback
    only runs when both are set but the endpoint is unreachable.
    """
    if not llm.llm_configured():
        raise HTTPException(
            status_code=503,
            detail="LLM is not configured — set both MERCURY_LLM_ENDPOINT "
                   "and MERCURY_LLM_MODEL and restart the server")
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
    # Optional scope (default All): restrict the rule to one bank
    # and/or one account (the account's current display label).
    source: str | None = None
    account: str | None = None


class RuleUpdateBody(BaseModel):
    category: str


class RuleReorderBody(BaseModel):
    # Every rule id exactly once, highest priority first.
    ordered_ids: list[int]


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
        nets = rules.net_by_rule(con, enabled)
        return {
            "rules": [{"id": r.id, "pattern": r.pattern,
                       "category": r.category, "enabled": r.enabled,
                       "position": r.position,
                       "source": r.source, "account": r.account,
                       "matches": matches.get(r.id, 0),
                       "net": round(nets.get(r.id, 0.0), 2)}
                      for r in rule_list],
        }
    finally:
        con.close()


@app.post("/api/rules")
def create_rule(body: RuleBody):
    con = get_con()
    try:
        try:
            r = rules.create_rule(con, body.pattern, body.category,
                                  source=body.source,
                                  account=body.account)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": r.id, "pattern": r.pattern,
                "category": r.category, "enabled": True,
                "position": r.position,
                "source": r.source, "account": r.account}
    finally:
        con.close()


@app.put("/api/rules/{rule_id}")
def update_rule_category(rule_id: int, body: RuleUpdateBody):
    """Change the category an existing rule assigns.

    Pattern, scope, and priority are untouched — the rule keeps matching
    the same rows, and the new category takes effect at read time like
    every other rule change. The category may be the reserved
    "Ignored" (moves the rule between the rules and Ignore panels)."""
    con = get_con()
    try:
        try:
            r = rules.update_rule_category(con, rule_id, body.category)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if r is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return {"id": r.id, "pattern": r.pattern, "category": r.category,
                "enabled": r.enabled, "position": r.position,
                "source": r.source, "account": r.account}
    finally:
        con.close()


@app.post("/api/rules/reorder")
def reorder_rules(body: RuleReorderBody):
    """Persist a new rule priority order (drag-and-drop in the UI).

    The new order takes effect immediately — like every rule change,
    it is a read-time layer and rewrites no transaction rows."""
    con = get_con()
    try:
        try:
            ordered = rules.reorder_rules(con, body.ordered_ids)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"order": [r.id for r in ordered]}
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
# Date-based ignores (before / range / after) — read-time layer, same
# treatment as the per-row 'Ignored' category: excluded from every
# aggregate, still listed, flagged ignored.
# ---------------------------------------------------------------------------
class DateIgnoreBody(BaseModel):
    # 'before' -> ignore txn_date < start (start kept)
    # 'after'  -> ignore txn_date > start (start kept)
    # 'range'  -> ignore start <= txn_date <= end (both ends inclusive)
    kind: str
    start: str
    end: str | None = None


@app.get("/api/date-ignores")
def list_date_ignores():
    """Date ignore rules, with live per-rule counts of the rows they
    exclude (n, and net signed amount) — computed on the same effective
    view every report reads."""
    con = get_con()
    try:
        items = date_ignores.load(con)
        stats = date_ignores.ignored_counts(con, items)
        return {
            "ignores": [{
                "id": r.id, "kind": r.kind, "start": r.start, "end": r.end,
                "n": stats[r.id][0],
                "net": round(stats[r.id][1], 2),
            } for r in items],
        }
    finally:
        con.close()


@app.post("/api/date-ignores")
def create_date_ignore(body: DateIgnoreBody):
    """Add a date ignore. 400 on an invalid kind/date, an inverted
    range, or an exact duplicate."""
    con = get_con()
    try:
        try:
            r = date_ignores.create(con, body.kind, body.start, body.end)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": r.id, "kind": r.kind, "start": r.start,
                "end": r.end}
    finally:
        con.close()


@app.delete("/api/date-ignores/{ignore_id}")
def delete_date_ignore(ignore_id: int):
    con = get_con()
    try:
        if not date_ignores.delete(con, ignore_id):
            raise HTTPException(status_code=404,
                                detail="date ignore not found")
        return {"deleted": ignore_id}
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


@app.get("/transactions")
def transactions_page():
    return FileResponse(STATIC_DIR / "transactions.html")


@app.get("/month")
def month_page():
    return FileResponse(STATIC_DIR / "month.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": llm.llm_configured()}


if __name__ == "__main__":
    import logging
    import uvicorn

    def _skip_well_known_probe(record: logging.LogRecord) -> bool:
        # Chrome probes /.well-known/... (DevTools metadata) on every page load;
        # it is a legitimate 404, but keep the access log clean.
        return "/.well-known/" not in record.getMessage()

    logging.getLogger("uvicorn.access").addFilter(_skip_well_known_probe)

    host = os.environ.get("MERCURY_HOST", "127.0.0.1")
    port = int(os.environ.get("MERCURY_PORT", "8000"))
    print(f"Mercury starting on http://{host}:{port}")
    print(f"  database  : {ingest.DB_PATH}")
    uvicorn.run(app, host=host, port=port)
