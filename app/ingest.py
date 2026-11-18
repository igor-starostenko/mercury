"""Mercury - local personal finance planner.

Standardizes CSV extracts from multiple institutions into a single DuckDB
table, deduplicates overlapping extracts, and serves a static-ish web UI
for spend analytics.

Run:
    .venv/bin/python -m app.main

Data enters exclusively through uploads: the UI sends bank CSVs (single
files or a whole folder) to POST /api/upload, which merges them into the
table by stable key. Nothing is scanned from disk in the background.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import duckdb

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
# MERCURY_DB (absolute path, ~ ok) points the app at a different database
# file — e.g. your own data — without touching the default dev database.
_env_db = os.environ.get("MERCURY_DB")
DB_PATH = Path(_env_db).expanduser() if _env_db else BASE_DIR / "mercury.duckdb"


# ---------------------------------------------------------------------------
# Standardized transaction model
# ---------------------------------------------------------------------------
@dataclass
class Txn:
    """One normalized transaction, independent of source institution."""
    source: str          # institution name, e.g. "Chase"
    account: str         # account identifier / name
    date: date
    amount: float        # positive = income, negative = expense
    description: str
    category: Optional[str] = None   # normalized category, or None if unknown
    raw_category: str = ""           # institution's own category (if any)
    currency: str = "USD"
    # Fingerprint used to deduplicate overlapping extracts.
    key: str = field(default="")

    def __post_init__(self) -> None:
        self.key = build_key(self.source, self.account, self.date,
                             self.amount, self.description)


def build_key(source: str, account: str, d: date, amount: float,
              description: str) -> str:
    """Stable identity for a transaction.

    Two rows from the *same* account on the *same* date with the *same*
    amount and description are treated as the same transaction (this is what
    lets overlapping extracts collapse). Description is normalized (lowercase,
    whitespace-collapsed) so trivial formatting differences don't break the
    match, but it is still part of the key so two genuinely different charges
    on the same day are kept distinct.
    """
    norm_desc = re.sub(r"\s+", " ", (description or "").strip()).lower()
    return f"{source}|{account}|{d.isoformat()}|{round(amount, 2)}|{norm_desc}"


@dataclass
class ParseResult:
    """Outcome of parsing one uploaded CSV."""
    txns: list[Txn] = field(default_factory=list)
    source: Optional[str] = None       # institution name, or None
    error: Optional[str] = None        # why the file could not be imported
    needs_source: bool = False         # bank unknown -> caller should ask
    note: Optional[str] = None         # how the bank was resolved (UI)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
                "%Y/%m/%d", "%m/%d/%y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_money_signed(value: str) -> Optional[float]:
    """Handle both '123.45' and '(123.45)' / '-123.45' conventions.

    Returns the magnitude with an explicit sign: the sign is whatever the
    input had (a leading '-' or parentheses). Callers that use a convention
    where a positive number is a purchase (e.g. Apple Card) flip the sign.
    """
    value = (value or "").replace("$", "").replace(",", "").strip()
    if not value:
        return None
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative = True
        value = value[1:-1]
    if value.startswith("-"):
        negative = True
        value = value[1:]
    elif value.startswith("+"):
        value = value[1:]
    try:
        amt = float(value)
    except ValueError:
        return None
    return -amt if negative else amt


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _read_rows(path: Path):
    """Yield dict rows from a CSV, tolerating a trailing backslash in a field.

    Some banks (Wells Fargo) put a literal backslash inside a quoted field to
    escape a quote. The csv module would then treat the closing quote as data
    and swallow the following comma. We neutralize that one case first.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        text = fh.read()
    # A backslash that sits directly before a closing quote of a quoted field
    # is a bank escape artifact; strip it so the csv module parses cleanly.
    text = text.replace('\\"', '"')
    for row in csv.DictReader(text.splitlines()):
        yield row


# Map each institution's own category vocabulary onto our standard set.
# (lowercase raw category -> standard category). Anything missing -> unknown.
def _map_category(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    if not key:
        return None
    table = {
        # Apple Card
        "grocery": "Groceries",
        "restaurants": "Dining",
        "alcohol": "Dining",
        "shopping": "Shopping",
        "entertainment": "Entertainment",
        "transportation": "Transportation",
        "medical": "Health",
        "utilities": "Utilities",
        "gas": "Transportation",
        "hotels": "Travel",
        "airlines": "Travel",
        "govt-services-parking": "Other",
        "installment": "Fees",
        "payment": "Transfers",
        "debit": "Transfers",
        "credit": "Transfers",
        "interest": "Fees",
        "other": None,
        # Chase
        "food & drink": "Dining",
        "travel": "Travel",
        "shopping": "Shopping",
        "groceries": "Groceries",
        "gifts & donations": "Other",
        "entertainment": "Entertainment",
        "fees & adjustments": "Fees",
        "personal": "Other",
        "home": "Housing",
        "health & wellness": "Health",
        "bills & utilities": "Utilities",
        "automotive": "Transportation",
    }
    return table.get(key)


# ---------------------------------------------------------------------------
# Parsers - one per institution (header-driven, so both real and sample files
# work regardless of exact column order).
# ---------------------------------------------------------------------------
def parse_wells_fargo(path: Path, source: str = "Wells Fargo") -> list[Txn]:
    """Wells Fargo CSV: DATE, DESCRIPTION, AMOUNT, CHECK #, STATUS.

    Amounts are signed (negative = money out). Account is inferred from the
    filename (WF-Checking / WF-CreditCard).
    """
    account = _account_from_filename(path, default="Checking")
    out: list[Txn] = []
    for row in _read_rows(path):
        d = _parse_date(row.get("DATE") or row.get("Date") or "")
        amt = _parse_money_signed(row.get("AMOUNT") or row.get("Amount") or "")
        if d is None or amt is None:
            continue
        out.append(Txn(
            source=source,
            account=account,
            date=d,
            amount=amt,
            description=_clean(row.get("DESCRIPTION") or row.get("Description")),
            category=None,
            raw_category="",
        ))
    return out


def parse_us_bank(path: Path, source: str = "US Bank") -> list[Txn]:
    """US Bank credit card CSV: Date, Transaction, Name, Memo, Amount.

    Amounts are signed (negative = purchase). No category column, so every
    row starts as unknown.
    """
    account = _account_from_filename(path, default="Credit Card")
    out: list[Txn] = []
    for row in _read_rows(path):
        d = _parse_date(row.get("Date") or row.get("DATE") or "")
        amt = _parse_money_signed(row.get("Amount") or row.get("AMOUNT") or "")
        if d is None or amt is None:
            continue
        name = _clean(row.get("Name") or row.get("NAME") or "")
        memo = _clean(row.get("Memo") or row.get("MEMO") or "")
        desc = f"{name} {memo}".strip()
        out.append(Txn(
            source=source,
            account=account,
            date=d,
            amount=amt,
            description=desc,
            category=None,
            raw_category="",
        ))
    return out


def parse_apple_card(path: Path, source: str = "Apple Card") -> list[Txn]:
    """Apple Card CSV: Transaction Date, Clearing Date, Description, Merchant,
    Category, Type, Amount (USD), Purchased By.

    Apple Card reports purchases as POSITIVE amounts, so we flip the sign so
    that a purchase is a negative (expense) in our convention.
    """
    out: list[Txn] = []
    for row in _read_rows(path):
        d = _parse_date(row.get("Transaction Date") or row.get("Date") or "")
        amt = _parse_money_signed(row.get("Amount (USD)") or row.get("Amount") or "")
        if d is None or amt is None:
            continue
        raw_cat = _clean(row.get("Category"))
        merchant = _clean(row.get("Merchant"))
        desc = _clean(row.get("Description"))
        # Prefer the merchant name for readability when present.
        description = merchant or desc
        out.append(Txn(
            source=source,
            account="Apple Card",
            date=d,
            amount=-amt,  # Apple Card: positive = purchase -> expense
            description=description,
            category=_map_category(raw_cat),
            raw_category=raw_cat,
        ))
    return out


def parse_chase(path: Path, source: str = "Chase") -> list[Txn]:
    """Chase activity CSV: Transaction Date, Post Date, Description, Category,
    Type, Amount, Memo. Amounts are signed (negative = purchase).
    """
    account = _account_from_filename(path, default="Checking")
    out: list[Txn] = []
    for row in _read_rows(path):
        d = _parse_date(row.get("Transaction Date") or row.get("Date") or "")
        amt = _parse_money_signed(row.get("Amount") or "")
        if d is None or amt is None:
            continue
        raw_cat = _clean(row.get("Category"))
        out.append(Txn(
            source=source,
            account=account,
            date=d,
            amount=amt,
            description=_clean(row.get("Description")),
            category=_map_category(raw_cat),
            raw_category=raw_cat,
        ))
    return out


def _account_from_filename(path: Path, default: str) -> str:
    """Infer an account label from the filename tokens.

    e.g. 'WF-CreditCard.csv' -> 'Credit Card', 'WF-Checking.csv' -> 'Checking'.
    """
    stem = path.stem
    for token in ("CreditCard", "Credit Card", "Checking", "Savings",
                  "Everyday"):
        if token.lower().replace(" ", "") in stem.lower().replace("-", "").replace(" ", ""):
            return token.replace("Card", " Card") if "Card" in token else token
    return default


PARSERS = {
    "wells fargo": parse_wells_fargo,
    "wells-fargo": parse_wells_fargo,
    "wf": parse_wells_fargo,
    "us bank": parse_us_bank,
    "us-bank": parse_us_bank,
    "apple card": parse_apple_card,
    "apple-card": parse_apple_card,
    "chase": parse_chase,
}

# Canonical display name for each parser key.
SOURCES = {
    "wells fargo": "Wells Fargo",
    "wells-fargo": "Wells Fargo",
    "wf": "Wells Fargo",
    "us bank": "US Bank",
    "us-bank": "US Bank",
    "apple card": "Apple Card",
    "apple-card": "Apple Card",
    "chase": "Chase",
}


def detect_parser(path: Path) -> Optional[tuple[callable, str]]:
    """Pick a parser from the filename (case-insensitive).

    Matches on the institution token anywhere in the stem, e.g.
    "wells_fargo_..." -> Wells Fargo, "Apple Card Transactions ..." -> Apple
    Card, "US-Bank - Credit Card ..." -> US Bank, "WF-Checking.csv" ->
    Wells Fargo.
    """
    name = path.stem.lower().replace("-", " ").replace("_", " ")
    for key, fn in PARSERS.items():
        if key == "wf":
            # "wf" is an abbreviation: match it only as a word token so we
            # don't accidentally match other banks.
            if re.search(r"\bwf\b", name):
                return fn, "Wells Fargo"
            continue
        if key.replace("-", " ") in name:
            return fn, SOURCES[key]
    return None


# Canonical bank names, in display order (Wells Fargo, US Bank, Apple Card,
# Chase) — served to the UI so it can offer them in the bank picker.
KNOWN_BANKS: list[str] = []
for _k, _v in SOURCES.items():
    if _v not in KNOWN_BANKS:
        KNOWN_BANKS.append(_v)

# Header signatures for filenames that carry no bank name. Ordered: the first
# bank whose marker matches wins. A bank matches when ANY of its marker sets
# is a subset of the (lowercased) header names. Markers are columns distinctive
# to one institution in either the real bank export or the sample format;
# nothing is matched on generic columns alone (Date/Description/Amount).
HEADER_SIGNATURES: list[tuple[str, tuple[set[str], ...]]] = [
    ("apple card", ({"clearing date"}, {"amount (usd)"}, {"currency"})),
    ("wells fargo", ({"check #"}, {"status"}, {"payment type"})),
    ("chase", ({"post date"}, {"transaction date"})),
    ("us bank", ({"name", "transaction", "memo"},)),  # trailing comma: 1-tuple
]


def detect_from_headers(header_line: str) -> Optional[str]:
    """Return the parser key if the CSV header identifies exactly one bank.

    Best-effort: files with only generic headers (Date/Description/Amount)
    return None and are left to the user's choice in the UI.
    """
    line = (header_line or "").lstrip("\ufeff").strip()
    if not line:
        return None
    try:
        fieldnames = next(csv.reader(line.splitlines()), [])
    except csv.Error:
        return None
    headers = {h.strip().lower() for h in fieldnames if h.strip()}
    if not headers:
        return None
    for key, marker_sets in HEADER_SIGNATURES:
        if isinstance(marker_sets, set):  # tolerate a bare set entry
            marker_sets = (marker_sets,)
        if any(m <= headers for m in marker_sets):
            return key
    return None


# ---------------------------------------------------------------------------
# Import / load into DuckDB
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS transactions (
        id          INTEGER PRIMARY KEY,
        source      VARCHAR,
        account     VARCHAR,
        txn_date    DATE,
        amount      DOUBLE,
        description VARCHAR,
        category    VARCHAR,
        raw_category VARCHAR,
        currency    VARCHAR,
        key         VARCHAR,
        classified  BOOLEAN DEFAULT false
    )
"""

# ---------------------------------------------------------------------------
# Originals stay untouched: user actions live in separate tables and rules
# live in a read-time layer, so the imported rows are never rewritten.
#
#   transactions.category        -> exactly what the CSV import produced
#   category_overrides           -> explicit assignments (manual, LLM, or
#                                   legacy rule rows from before rules
#                                   became read-time)
#   category_rules               -> keyword -> category rules, applied at
#                                   read time (no per-row writes)
#   deleted_transactions         -> soft-deleted rows (hidden from all reads)
#
# Every read goes through the effective_transactions view, which layers:
# explicit override (manual or legacy rule) > first matching enabled rule
# > LLM override > original CSV category. Deleted rows are filtered out.
# ---------------------------------------------------------------------------
CREATE_OVERRIDES_SQL = """
    CREATE TABLE IF NOT EXISTS category_overrides (
        txn_id   INTEGER PRIMARY KEY,
        category VARCHAR,
        source   VARCHAR
    )
"""


def ensure_override_source(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add the source column to category_overrides.

    source records who set the override: 'manual' (user edit), 'llm'
    (auto-classify), 'rule' (custom category rule), NULL for overrides
    that predate source tracking (assigner unknown).
    """
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info('category_overrides')").fetchall()}
    if "source" not in cols:
        con.execute(
            "ALTER TABLE category_overrides ADD COLUMN source VARCHAR")

CREATE_DELETED_SQL = """
    CREATE TABLE IF NOT EXISTS deleted_transactions (
        txn_id INTEGER PRIMARY KEY
    )
"""

CREATE_EFFECTIVE_VIEW_SQL = """
    CREATE OR REPLACE VIEW effective_transactions AS
    SELECT x.id, x.source, x.account, x.txn_date, x.amount, x.description,
           CASE
             WHEN x.override_source IN ('manual', 'rule')
               THEN x.override_category
             WHEN x.rule_category IS NOT NULL THEN x.rule_category
             ELSE COALESCE(x.override_category, x.category)
           END AS category,
           CASE
             WHEN x.override_source IN ('manual', 'rule')
               THEN x.override_source
             WHEN x.rule_category IS NOT NULL THEN 'rule'
             ELSE x.override_source
           END AS override_source,
           x.raw_category, x.currency, x.key, x.classified
    FROM (
      SELECT t.*, o.category AS override_category, o.source AS override_source,
             (SELECT r.category FROM category_rules r
              WHERE r.enabled AND r.pattern <> ''
                -- Match on whitespace-normalized text: bank extracts keep
                -- runs of spaces ('U.S. BANK        WEB PYMT') while the
                -- UI shows (and users copy) single-spaced text. DuckDB's
                -- regexp_replace needs the 'g' flag or only the first
                -- run is replaced.
                AND instr(
                        lower(regexp_replace(t.description, '\\s+', ' ', 'g')),
                        lower(regexp_replace(r.pattern, '\\s+', ' ', 'g'))
                      ) > 0
              ORDER BY r.position, r.id LIMIT 1) AS rule_category
      FROM transactions AS t
      LEFT JOIN category_overrides AS o ON o.txn_id = t.id
      WHERE t.id NOT IN (SELECT txn_id FROM deleted_transactions)
    ) AS x
"""


def parse_file_bytes(name: str, data: bytes,
                     source: Optional[str] = None) -> ParseResult:
    """Parse one uploaded CSV.

    Bank resolution order:
      1. filename token (same rules as detect_parser) — the filename wins;
      2. CSV header signature (detect_from_headers) — distinctive columns
         like "Post Date" (Chase) or "Amount (USD)" (Apple Card);
      3. the ``source`` hint the user picked in the UI (any name in
         KNOWN_BANKS).

    If none of those resolves the bank, the result has needs_source=True
    (and an error) so the caller can ask which bank the file belongs to and
    resubmit it with a hint.
    """
    p_path = Path(name)
    fn: Optional[callable] = None
    src: Optional[str] = None
    note: Optional[str] = None
    hit = detect_parser(p_path)
    if hit is not None:
        fn, src = hit
        note = "filename"
    else:
        head = data[:64]
        if head.startswith(b"\xef\xbb\xbf"):
            head = head[3:]
        first_line = head.split(b"\n", 1)[0].decode("utf-8", "replace")
        key = detect_from_headers(first_line)
        if key is not None:
            fn, src = PARSERS[key], SOURCES[key]
            note = "inferred from file header"
        elif source:
            norm = source.strip().lower()
            match = next((k for k, v in SOURCES.items()
                          if v.lower() == norm), None)
            if match is None:
                return ParseResult(
                    error=f"unknown bank: {source}", needs_source=True)
            fn, src = PARSERS[match], SOURCES[match]
            note = "bank chosen in UI"
        else:
            return ParseResult(error="no known bank in filename",
                               needs_source=True)
    # The parsers take a path (they infer the account from the filename),
    # so stage the bytes in a temp file under the original name.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{p_path.stem or 'upload'}.csv"
        p.write_bytes(data)
        try:
            return ParseResult(txns=fn(p, src), source=src, note=note)
        except Exception as e:  # one malformed file must not sink the batch
            return ParseResult(source=src, error=f"parse error: {e}",
                               note=note)


def merge_txns(con: duckdb.DuckDBPyConnection,
               txns: list[Txn]) -> tuple[int, int]:
    """Merge parsed transactions into the table by stable key.

    Rows whose key already exists (from any earlier upload) are skipped, so
    existing rows — including manual/LLM categories — are never touched.
    Uploads are idempotent: re-sending the same file adds nothing.

    Categories are not written at insert time: the CSV-provided value is
    stored as-is, and rules apply to new rows at read time through the
    effective_transactions view, exactly like existing rows.

    Returns (total_rows, new_rows).
    """
    con.execute(CREATE_TABLE_SQL)
    con.execute(CREATE_OVERRIDES_SQL)
    ensure_override_source(con)
    existing = {k for (k,) in con.execute(
        "SELECT key FROM transactions").fetchall()}
    seen: set[str] = set()
    new: list[Txn] = []
    for t in txns:
        if t.key not in existing and t.key not in seen:
            seen.add(t.key)
            new.append(t)
    if new:
        # Continue the id sequence; ids from earlier rebuilds/merges are
        # unique, so MAX(id)+1 is safe.
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM transactions").fetchone()[0]
        # One transaction: a mid-merge failure must not lose rows that
        # were already in the table.
        con.execute("BEGIN TRANSACTION")
        try:
            # The row stores the CSV-provided category as-is (originals
            # stay unmodified, so re-imports and overrides never fight).
            con.executemany(
                "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [[max_id + i, t.source, t.account, t.date, t.amount,
                  t.description, t.category, t.raw_category, t.currency,
                  t.key, bool(t.category)]
                 for i, t in enumerate(new, start=1)])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], len(new)


def migrate_originals(con: duckdb.DuckDBPyConnection) -> int:
    """Move pre-override categories out of the imported rows.

    Databases created before the override tables existed have rule/LLM/
    manual categories baked into transactions.category. For every row
    whose category no longer matches what the CSV itself provided, the
    current category moves to category_overrides (so it keeps taking
    precedence) and the original import value is restored in the row.

    Idempotent: once every row's category equals its CSV-derived value,
    the migration is a no-op — and nothing writes non-CSV categories
    into transactions afterwards. Returns rows changed.
    """
    con.execute(CREATE_OVERRIDES_SQL)
    ensure_override_source(con)
    moved = 0
    for tid, cat, raw in con.execute(
            "SELECT id, category, raw_category FROM transactions").fetchall():
        original = _map_category(raw) if raw else None
        if cat != original:
            con.execute("BEGIN TRANSACTION")
            try:
                if cat is not None and str(cat).strip() != "":
                    # NULL source: this override predates source tracking,
                    # so who set it (manual, LLM, rule) is unknown.
                    con.execute(
                        "INSERT INTO category_overrides VALUES (?, ?, NULL) "
                        "ON CONFLICT (txn_id) DO UPDATE SET "
                        "category = excluded.category", [tid, cat])
                con.execute(
                    "UPDATE transactions SET category = ? WHERE id = ?",
                    [original, tid])
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            moved += 1
    return moved


def backfill_override_source(con: duckdb.DuckDBPyConnection) -> int:
    """One-time attribution of legacy overrides (source IS NULL).

    Overrides that predate source tracking carry no record of who set
    them. If the override's category is exactly what the deterministic
    rule fallback assigns to the description, it came from the
    auto-classify path (live LLM or its offline fallback) and is
    attributed 'llm'. Anything else — most likely a manual edit — is
    left 'unknown'. Idempotent: rows already attributed no longer
    match, and re-checking the rest only writes on a match.
    """
    from . import llm as llm_mod  # local import: no cycle at module load
    con.execute(CREATE_OVERRIDES_SQL)
    ensure_override_source(con)
    rows = con.execute("""
        SELECT o.txn_id, t.description, o.category
        FROM category_overrides AS o
        JOIN transactions AS t ON t.id = o.txn_id
        WHERE o.source IS NULL
    """).fetchall()
    updated = [
        (tid,) for tid, desc, cat in rows
        if cat and llm_mod.rule_classify(desc) == cat
    ]
    if updated:
        con.executemany(
            "UPDATE category_overrides SET source = 'llm' WHERE txn_id = ?",
            updated)
    return len(updated)
