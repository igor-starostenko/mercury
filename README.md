# Mercury

Local family finance dashboard: FastAPI + DuckDB. Parse bank CSV exports,
classify transactions, browse spend analytics. Runs entirely on your machine
on a localhost port — no cloud, no accounts.

## Setup

```bash
uv sync   # creates .venv from the locked versions in uv.lock
```

## Run

```bash
uv run python -m app.main
# → http://127.0.0.1:8000
```

Bind/host configurable via `MERCURY_HOST` / `MERCURY_PORT`.

Dependencies are pinned in `pyproject.toml` and fully locked (including
transitive deps) in `uv.lock` — commit both. Update with `uv add pkg` /
`uv lock --upgrade`, then `uv sync`.

### Database

Data lives in `app/mercury.duckdb` — a single file next to the code.
It is gitignored and not in the repo: on first start the app creates
empty tables, so a fresh clone starts with no data. If you start the
server and see stale data, it comes from a leftover
`app/mercury.duckdb` in your working copy (gitignored, so git won't
show it) — move it aside and the next start is fresh.

Use a different database file — e.g. your own data — without touching
the default:

```bash
MERCURY_DB=~/my-data/mercury.duckdb uv run python -m app.main
```

`MERCURY_DB` takes an absolute path (`~` is fine); the resolved file is
printed on the startup line `database: ...`. Unset, the default
`app/mercury.duckdb` is used.

### Data

Import bank CSVs from the UI (top bar): **Import CSV files** picks
individual files, **Import folder** picks a whole directory (e.g. your
bank-exports folder) and sends every file in one request. Formats are
auto-detected by filename (Wells Fargo, Chase, Apple Card, US Bank) —
keep the bank's original filenames. Nothing is scanned from disk in the
background; the database only contains what you uploaded.

- `POST /api/upload` — multipart form, field `files` (one or more CSVs).
  Rows are merged into the table by stable key: duplicates are skipped,
  original imports and existing edits are preserved, re-uploading is a
  no-op.
- `uv run python generate_samples.py` — generates synthetic sample CSVs in
  `app/data/extracts/` (no real data is stored in this repo). These are
  the "dummy data" behind the demo dashboard: generate them, then import
  via the UI (they are not imported automatically).

### Classification

- Heuristic rules run at import time.
- `POST /api/classify` — batch-classify remaining unknowns via a local
  LLM: any OpenAI-compatible endpoint works (Ollama, oMLX, LM Studio,
  llama.cpp server, ...). Configure with `MERCURY_LLM_ENDPOINT` (default
  `http://localhost:11434/v1/chat/completions` — Ollama),
  `MERCURY_LLM_MODEL` (default `llama3.2`), and `MERCURY_LLM_API_KEY`
  (sent as `Authorization: Bearer …` when set). If the model is
  unreachable the built-in keyword rules classify instead. The
  **Classify unknown (LLM)** button is on the Categories page, next to
  the uncategorized list.
- `POST /api/categorize` — assign a standard category manually:
  `{"ids": [1, 2], "category": "Groceries"}`.

### Editing imported data

The imported rows are never rewritten: your category changes (manual,
LLM, or custom rules) are stored as overrides that take precedence over
the CSV-provided category, and clearing a category (or sending
`"category": null`) puts the imported one back.

Every override records who set it — `manual` (an edit from the UI),
`llm` (auto-classify) or `rule` (custom category rule). The Categories
page has a **Categorized transactions** section next to the
uncategorized list: it shows every override with a "Set by" badge,
filterable by All / You / LLM / Rule / Unknown, and the category is
editable
inline (re-picking one marks the row as yours).
`GET /api/overrides` returns the same list as JSON. Overrides that
predate source tracking are best-effort attributed on first startup:
any whose category is exactly what the auto-classify rule fallback
assigns to the description becomes `llm` (that is the only path that
produces such a match without human input); the rest — most likely
manual edits — stay `"unknown"`.

- `DELETE /api/transactions/{id}` — remove a transaction (the × in the
  transaction tables). It is a soft delete: the row disappears from every
  view and total, but the imported data stays in the database, and
  re-importing the file does not bring it back.
- `POST /api/transactions/{id}/restore` — undo a soft delete.
