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

### Data

Import bank CSVs from the UI (top bar): **Import CSV files** picks
individual files, **Import folder** picks a whole directory (e.g. your
bank-exports folder) and sends every file in one request. Formats are
auto-detected by filename (Wells Fargo, Chase, Apple Card, US Bank) —
keep the bank's original filenames. Nothing is scanned from disk in the
background; the database only contains what you uploaded.

- `POST /api/upload` — multipart form, field `files` (one or more CSVs).
  Rows are merged into the table by stable key: duplicates are skipped,
  manual/LLM categories are preserved, re-uploading is a no-op.
- `uv run python generate_samples.py` — regenerates synthetic sample CSVs in
  `app/data/extracts/` (no real data is stored in this repo).

### Classification

- Heuristic rules run at import time.
- `POST /api/classify` — batch-classify remaining unknowns via a local
  LLM (Ollama by default; `MERCURY_LLM_ENDPOINT` / `MERCURY_LLM_MODEL`). Falls back
  to rule-based when the model is unavailable.
- `POST /api/categorize` — assign a standard category manually:
  `{"ids": [1, 2], "category": "Groceries"}`.
