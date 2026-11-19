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

- The CSV's own category labels are mapped to standard categories at
  import time; the imported value is stored in the row as-is and is
  what every report starts from.
- `POST /api/classify` — batch-classify remaining unknowns via a local
  LLM: any OpenAI-compatible endpoint works (Ollama, oMLX, LM Studio,
  llama.cpp server, ...). Configure with `MERCURY_LLM_ENDPOINT`
  (default `http://localhost:11434/v1/chat/completions` — Ollama),
  `MERCURY_LLM_MODEL` (default `llama3.2`), and `MERCURY_LLM_API_KEY`
  (sent as `Authorization: Bearer *** when set).
  Descriptions are sent in small batches (25 per request), so a long
  run stays robust and a failing batch only costs itself (the keyword
  rules cover it). For thinking models (Qwen3 and similar) the request
  also asks the server to disable reasoning tokens via
  `chat_template_kwargs`; servers that reject the field are retried
  without it, once.
  "Ollama by default"
  only means the default endpoint points at Ollama's OpenAI-compatible
  API — nothing is required to be running: if the endpoint is
  **configured but unreachable**, the built-in keyword rules classify
  instead. If `MERCURY_LLM_ENDPOINT` is not set at all, no fallback is
  run: `GET /api/health` reports `llm_configured: false`, the
  **Classify unknown (LLM)** button (Categories page, next to the
  categorization list) is greyed out, and `POST /api/classify` returns
  `503` with the setup hint.
- `POST /api/categorize` — assign a standard category manually:
  `{"ids": [1, 2], "category": "Groceries"}`. `GET
  /api/standard-categories` returns the full vocabulary — the standard
  categories (including `Donations` and `Education`) plus the reserved
  `Ignored` marker. The LLM classifier is prompted with everything
  except `Ignored`: ignoring is a human judgment, not a merchant guess.

### Custom category rules

Rules are a pure read-time layer: a rule (keyword → category,
case-insensitive substring in the description, first match wins)
reshapes every report the moment it is saved, and it writes nothing
per-row — no "apply" step, and the original import is never touched.
Deleting a rule reverts the affected rows to whatever else classifies
them (an explicit assignment, an LLM category, or the CSV value).

Rule priority is the list order: rules are evaluated top to bottom and
the first match wins. New rules append to the end (lowest priority);
the UI rows in the rules and Ignore panels can be dragged to reorder —
rows work across both panels, since the panels share one global
priority list — and the order is persisted (`POST
/api/rules/reorder`) and takes effect immediately.

Precedence, in every report: an explicit assignment (manual edit, or a
legacy `rule` override from before rules became read-time) > first
matching rule > LLM assignment > CSV import. Re-picking a category on
a rule-classified row marks it as yours and pins it against the rule.

The rule's category can be the reserved value `"Ignored"` — the UI
gives it its own **Ignore transactions** box (home + Categories pages):
a keyword plus "Ignore matches", no category dropdown needed. A single
transaction can also be ignored by hand via its category dropdown.
Ignored rows are excluded from every total (spend,
income, net, account summaries, period lists) but stay visible in the
transaction lists, dimmed and marked "ignored" — the intended tool for
transfers between accounts and any other row that isn't real spend or
income. `GET /api/transactions` rows carry an `"ignored": true` flag
when this applies; deleting the rule (or picking a real category on the
row) un-ignores it.

The same box also holds **date-based ignores**: pick a mode (Before /
Within / After) and one or two dates. Matching is on `txn_date` —
"Before 2025-01-01" covers everything before that date (the date
itself is kept), "After" covers everything after it, "Within" covers
both end dates inclusive. Date-ignored rows get the same treatment as
keyword-ignored ones — excluded from every total, still listed,
flagged — and when a month or year is left with no counting rows it
disappears entirely from the flow chart, period selector, year cards
and month summaries, exactly like a period with no data. Multiple
rules stack; they are read-time only and the originals are never
modified.

- `GET /api/rules` — rules in evaluation order, with live per-rule
  match counts.
- `POST /api/rules` — add a rule: `{"pattern": "walmart",
  "category": "Groceries"}` (400 on empty/too-long fields or a
  duplicate pattern). Appends to the end of the priority list.
- `POST /api/rules/reorder` — persist a new priority order:
  `{"ordered_ids": [2, 1, 3]}` (every rule id exactly once, highest
  priority first; 400 with "refresh and retry" on stale client state).
- `DELETE /api/rules/{id}` — remove a rule (rows it classified revert
  immediately).
- `GET /api/date-ignores` — date-based ignore rules, with a live
  count of the rows each one excludes from the totals.
- `POST /api/date-ignores` — add one: `{"kind": "before" | "range" |
  "after", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` (`range`
  requires `end`; 400 on an unknown kind, a malformed date, an
  inverted range, or an exact duplicate).
- `DELETE /api/date-ignores/{id}` — remove a date ignore; its rows
  re-enter every total immediately.

### Editing imported data

The imported rows are never rewritten: manual and LLM category changes
are stored as overrides that take precedence over the CSV-provided
category, custom rules apply on top at read time (see above), and
clearing a category (or sending `"category": null`) puts the imported
one back.

Every override records who set it — `manual` (an edit from the UI) or
`llm` (auto-classify); `rule` appears only in legacy overrides written
before rules became a read-time layer. The Categories page has one
**Transaction categorization** list: every override, every row
re-categorized by a live rule (shown with the Rule badge even though no
override was stored), plus every transaction that was missing a
category in the CSV import — each with a "Set by" badge, filterable by
All / You / LLM / Rule / Unknown. Unknown shows the ones still
uncategorized; All and You also include your re-categorizations of
CSV-provided categories (the original CSV category is shown as "was: …"
under the select). The category is editable inline (re-picking one
marks the row as yours). The chip filter, the description search and
the column sort all run **server-side over the full history** — the
API filters and sorts everything in SQL and only then applies its
limit, so "Unknown" shows every uncategorized transaction, not just
the ones inside the first page. `GET /api/overrides` returns
`{"rows": [...], "total": N, "uncategorized": M}`: the window, the
count of rows matching the current filter, and the full-history count
of still-uncategorized rows (drives the "N need a category" badge).
Add `include_uncategorized=true` to also get the still-uncategorized
rows (those carry `set_by = "uncategorized"`). Filter and sort
parameters: `set_by=all|manual|llm|rule|uncategorized`,
`sort_by=date|description|amount` with `sort_dir=asc|desc`
(defaults: date desc, description asc, amount desc), and `q` for a
case-insensitive description search (whitespace-normalized, the same
matching as the rules).
Overrides that predate source tracking are best-effort attributed on
first startup: any whose category is exactly what the auto-classify
rule fallback assigns to the description becomes `llm` (that is the
only path that produces such a match without human input); the rest —
most likely manual edits — stay `"unknown"`.

- `DELETE /api/transactions/{id}` — remove a transaction (the × in the
  transaction tables). It is a soft delete: the row disappears from every
  view and total, but the imported data stays in the database, and
  re-importing the file does not bring it back.
- `POST /api/transactions/{id}/restore` — undo a soft delete.

### Sorting transactions

Every transaction list (the Month page table and the per-category
drill-down lists on Overview/Year/Categories) is sortable by **Date**,
**Description**, and **Amount**. Click a column header to sort; click the
same header again to flip the direction (▲ ascending / ▼ descending in
the header). The list defaults to date, newest first. Sorting happens in
the browser over the already-loaded rows — no extra API calls, and the
category dropdowns keep working after a re-sort.

### Searching transactions

The **Transaction categorization** list (Categories page) and the
**All transactions** table (Month page) each have a search box. Search
uses the exact same matching as category rules — case-insensitive
partial substring on whitespace-normalized description text — so the
rows shown for a phrase are precisely the rows a rule with that
pattern would catch: type a candidate pattern to dry-run it before
saving it. Search is a client-side filter over the rows already loaded
(no extra API calls) and composes with the All / You / LLM / Rule /
Unknown chips (both must match).

While a search is active, the matched text is highlighted in each
description and the header hint shows how many rows are visible.
Every row in both lists also carries a **rule verdict** in its
category cell: which enabled rule the description matches (the first
in priority order — the one that would classify it), whether that
rule is the effective category source or overridden by a manual / LLM
assignment, and how many lower-priority rules it shadows. The Month
table additionally shows a small `you` / `LLM` / `rule` provenance
badge next to each category dropdown, using the same vocabulary as
the "Set by" column. The API rows behind both lists carry `rule_match`
(`{pattern, category, position}` or `null`), `rule_matches` (count of
enabled rules matching) and `set_by` (effective source: `manual` /
`llm` / `rule` / `csv` / `none`).

### Charts

The cash-flow bar charts (Overview, Year, Month) default to **stacked by
category** — spend and income each in their own stack, one segment per
category — so per-category trends are visible across months and weeks.
The **Totals** toggle in each chart's header switches to the grouped
Spend vs Income bars. The choice is remembered per chart in the browser
(localStorage). The top 8 are selected **per bar**: each bucket's
(month's, or week's) top 8 by its own spend + income are spelled out, so a
category that spikes in a single bucket (e.g. a month of tuition) is named
in that bar's legend and tooltip instead of being hidden in "All other" by
the all-range totals. The legend lists the union of every bucket's top 8
(in all-range volume order); all remaining categories fold into one
"All other" segment, and grand totals appear in the tooltip footer.
API side: `GET /api/monthly` adds `flow_by_category`
(`[{bucket, category, spend, income}]` per month, plus income), and
`GET /api/weekly?month=YYYY-MM&by_category=true` returns
`{weeks, flow_by_category}` — the default response shape of the weekly
endpoint is unchanged.
