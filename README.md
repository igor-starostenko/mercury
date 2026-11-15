# FamilyFinance

Local family finance dashboard: FastAPI + DuckDB. Parse bank CSV exports,
classify transactions, browse spend analytics. Runs entirely on your machine
on a localhost port — no cloud, no accounts.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python -m app.main
# → http://127.0.0.1:8000
```

Bind/host configurable via `FF_HOST` / `FF_PORT`.

### Data

Drop bank CSV exports into `app/data/extracts/` (Wells Fargo, Chase,
Apple Card, US Bank formats are auto-detected by filename; headers are
sniffed, so layout varies are tolerated). The DuckDB database is created
and populated automatically on first request.

- `POST /api/reimport` — idempotent rebuild from the CSV folder. An empty
  folder is a no-op (`{"imported": 0}`) and never wipes existing data.
  Manual/LLM-assigned categories survive re-imports.
- `python3 generate_samples.py` — regenerates synthetic sample CSVs in
  `app/data/extracts/` (no real data is stored in this repo).

### Classification

- Heuristic rules run at import time.
- `POST /api/classify` — batch-classify remaining unknowns via a local
  LLM (Ollama by default; `FF_LLM_ENDPOINT` / `FF_LLM_MODEL`). Falls back
  to rule-based when the model is unavailable.
- `POST /api/categorize` — assign a standard category manually:
  `{"ids": [1, 2], "category": "Groceries"}`.
