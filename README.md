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

Import bank CSVs from the UI (top bar): **Manage Imports** opens the
import panel, where **Import CSV files** picks individual files and
**Import folder** picks a whole directory (e.g. your bank-exports
folder) and sends every file in one request. Formats are auto-detected
by filename (Wells Fargo, Chase, Apple Card, US Bank) — keep the bank's
original filenames. Nothing is scanned from disk in the background; the
database only contains what you uploaded.

Every upload is tracked as one *import* (which file it came from), shown
in the Manage Imports panel:

- **Rename an import's account** — the account label parsed from the
  filename is editable per import, so a second checking account at the
  same bank can be told apart (e.g. `Wells Fargo — Checking` →
  `Checking #2`). The re-label is retroactive: every report reads
  through the effective view, which maps each row's account to its
  import's current label. The stored rows are never rewritten, so
  re-uploading the same file still dedupes under the new name.
- **Delete an import** — removes only that file's transactions (plus
  their per-transaction overrides), keeping category rules and every
  other import untouched.

- `GET /api/imports` — all imports with their current account label,
  row count, and date span.
- `POST /api/imports/{id}/account` — `{"account": "…"}` re-labels the
  import's account for all of its rows.
- `DELETE /api/imports/{id}?source=…` — deletes an import's rows; the
  `source` filter scopes a synthetic "earlier import" group to one bank.

- `POST /api/upload` — multipart form, field `files` (one or more CSVs).
  Rows are merged into the table by stable key and stamped with the
  import they came from. The source listing is authoritative: every row
  the file lists is imported — a file listing the identical row (same
  day, account, amount, description) several times means several real
  transactions. Only rows the database already holds from an earlier
  (overlapping) import are skipped, so existing imports and edits are
  preserved and re-uploading a file is a no-op.
- `uv run python generate_samples.py` — generates synthetic sample CSVs in
  `app/data/extracts/` (no real data is stored in this repo). These are
  the "dummy data" behind the demo dashboard: generate them, then import
  via the UI (they are not imported automatically).

### Classification

- The CSV's own category labels are mapped to standard categories at
  import time; the imported value is stored in the row as-is and is
  what every report starts from.
- `POST /api/categorize` — assign a standard category manually:
  `{"ids": [1, 2], "category": "Groceries"}`. `GET
  /api/standard-categories` returns the full vocabulary — the standard
  categories (including `Beauty`, `Donations` and `Education`) plus the reserved
  `Ignored` marker. The LLM classifier is prompted with everything
  except `Ignored`: ignoring is a human judgment, not a merchant guess.

### Auto-classification with a local AI model (optional)

Mercury can fill in the category for transactions it doesn't
recognize by asking an AI model. **This feature is completely
optional** — the app works exactly the same without it: you can
assign categories by hand (the category dropdowns on the Categories
page) and with custom category rules, and the **Classify unknown
(LLM)** button is simply greyed out.

To turn it on, you need a model server already running on your
machine or network that speaks the OpenAI chat format (Ollama, oMLX,
LM Studio, llama.cpp server, vLLM, ...), and you tell Mercury two
things when you start the server:

1. **Where the model is** — `MERCURY_LLM_ENDPOINT`, the URL of the
   server's chat endpoint. Example for Ollama running on your own
   machine: `http://localhost:11434/v1/chat/completions`.
2. **Which model to use** — `MERCURY_LLM_MODEL`, the exact name of
   the model that server runs. You can list the names it offers with
   `curl http://localhost:11434/v1/models` (for Ollama). Example:
   `llama3.2`.

Both settings are **required**. Mercury ships with no defaults and
never guesses either one: with either missing, the feature simply
stays off — the Classify button is greyed out, `GET /api/health`
reports `llm_configured: false`, and `POST /api/classify` returns a
`503` that says what to set. If your model server asks for a key,
also set the optional `MERCURY_LLM_API_KEY` (sent to the server as
`Authorization: Bearer <key>`). Example:

```bash
MERCURY_LLM_ENDPOINT=http://localhost:11434/v1/chat/completions \
MERCURY_LLM_MODEL=llama3.2 \
MERCURY_LLM_API_KEY=*** \
uv run python -m app.main
```

The model is only contacted when you press the Classify button (or
call `POST /api/classify`) — not at startup, and the only data sent
to it is the transaction descriptions that still need a category,
sent to the server you configured.

What happens during a classify run:

- Descriptions are sent in small batches (25 per request), so a long
  run stays robust and a failing batch only costs itself: the
  built-in keyword rules ("starbucks" → Dining, "uber" →
  Transportation, ...) cover it.
- If the endpoint is set but the model server can't be reached at
  that moment, those keyword rules classify instead of failing the
  run. This is the only fallback — with the two settings missing,
  nothing runs at all.
- For thinking models (Qwen3 and similar) the request also asks the
  server to disable reasoning tokens via `chat_template_kwargs`;
  servers that reject the field are retried once without it.
- The model can only pick from the standard category list (it never
  returns `Ignored` — ignoring is a human judgment). Each result is
  stored as an "LLM" category you can change or clear like any other
  edit.

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

A rule's category can be changed after the fact: every row in the rules
and Ignore panels carries a category dropdown (`PUT /api/rules/{id}`).
Only the target category moves — the pattern, scope, and priority are
kept — so the rule keeps matching the same rows from the same slot.
Picking `Ignored` (or moving a rule away from it) moves the rule
between the two panels.

A rule can be **scoped** to one bank (`source`) and/or one account:
create it with `source` or `account` set and it only matches rows
from that bank and/or account — every other row ignores it. An unset
scope means **All**, so unscoped rules — including everything created
before scoping existed — behave exactly as before, and the same
pattern may exist once per scope (a "walmart" → Groceries rule for
one bank, "walmart" → Dining for another, plus an unscoped fallback
all coexist). The account scope is the account's *current display
label*, so a Manage Imports rename moves the rule's coverage along
with its rows. The rules and Ignore panels show each rule's scope in
a Scope column ("All" when unscoped), and the add-rule forms offer
source/account dropdowns (defaulting to All) built from the live
account list; per-rule match counts and nets honor the scope.

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
  "category": "Groceries", "source": "Chase", "account": "Checking"}`
  (`source`/`account` optional — omit them for "All"; 400 on
  empty/too-long fields or a duplicate pattern within the same scope).
  Appends to the end of the priority list.
- `PUT /api/rules/{id}` — change the category a rule assigns:
  `{"category": "Beauty"}` (400 on an empty/too-long category, 404 on
  an unknown rule id). Pattern, scope, and priority are untouched and
  the new category takes effect immediately, like every other rule
  change; setting `Ignored` moves the rule to the Ignore box.
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
marks the row as yours). Browsing (no search) shows that base set —
rows the app itself categorized — while rows that simply keep the
category their bank's CSV provided are not listed. The description
search is different: with the **All** chip it scans the **full
transaction history**, so it also finds those bank-CSV rows (they
render with the "bank" badge, `set_by = "csv"`); a search inside a chip
keeps that chip's provenance scope. A search also requests a larger
window, so its results are every matching record, not the first 1000 —
the "N of M shown" hint appears only if a term matches more. The chip
filter, the description search and the column sort all run
**server-side over the full history** — the API filters and sorts
everything in SQL and only then applies its limit, so "Unknown" shows
every uncategorized transaction, not just the ones inside the first
page. `GET /api/overrides` returns `{"rows": [...], "total": N,
"uncategorized": M}`: the window, the count of rows matching the
current filter, and the full-history count of still-uncategorized rows
(drives the "N need a category" badge).
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
saving it. Search is a server-side filter over the full history (the
request includes the term, so no row is filtered out before matching)
and composes with the All / You / LLM / Rule / Unknown chips (both
must match). In the categorization list, an active search widens the
scope from the section's base set to every transaction — bank-CSV rows
included, badged "bank" — and raises the result window so all matches
are returned (see above).

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
