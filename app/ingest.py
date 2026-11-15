"""Family Finance - local personal finance planner.

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
DB_PATH = BASE_DIR / "familyfinance.duckdb"


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


def load_all(con: duckdb.DuckDBPyConnection) -> int:
    """Import every CSV in INPUT_DIR into the transactions table.

    Rebuilds the table from scratch so repeated imports are idempotent.
    Returns the number of (deduplicated) transactions stored.
    """
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in INPUT_DIR.iterdir()
                   if p.suffix.lower() in (".csv", ".txt"))
    txns: list[Txn] = []
    for path in files:
        hit = detect_parser(path)
        if hit is None:
            print(f"  ! skipping {path.name} (no known format)")
            continue
        fn, source = hit
        parsed = fn(path, source)
        print(f"  {path.name}: {len(parsed)} rows from {source}")
        txns.extend(parsed)

    if not txns:
        # Nothing found in the input folder: make sure the table exists but
        # NEVER wipe what's already there (an empty folder is not an empty
        # account). Returns 0 so the API reports "imported: 0".
        con.execute(CREATE_TABLE_SQL)
        return 0

    _rebuild(con, txns)
    return con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


def _rebuild(con: duckdb.DuckDBPyConnection, txns: list[Txn]) -> None:
    con.execute(CREATE_TABLE_SQL)
    # Keep an existing classification if we re-import the same transaction.
    existing: dict[str, str] = {}
    try:
        has_rows = con.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
    except duckdb.Error:
        has_rows = 0
    if has_rows > 0:
        for k, cat, classified in con.execute(
                "SELECT key, category, classified FROM transactions").fetchall():
            if classified and cat:
                existing[k] = cat

    # One transaction: a failure mid-rebuild must never leave the table
    # empty behind (DuckDB 2.x raises on an empty executemany).
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM transactions")
        if txns:
            con.executemany(
                "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [[i, t.source, t.account, t.date, t.amount, t.description,
                  # Re-import must keep a manually-assigned category
                  # (see the snapshot above) instead of clobbering it.
                  existing.get(t.key, t.category),
                  t.raw_category, t.currency, t.key,
                  t.key in existing or bool(t.category)]
                 for i, t in enumerate(txns, start=1)])

        # Deduplicate: keep one row per key.
        con.execute("""
            CREATE OR REPLACE TABLE transactions AS
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY key
                    ORDER BY id
                ) AS rn
                FROM transactions
            ) t
            WHERE rn = 1
        """)
        con.execute("ALTER TABLE transactions RENAME TO _dedup")
        con.execute("""
            CREATE OR REPLACE TABLE transactions AS
            SELECT id, source, account, txn_date, amount, description,
                   category, raw_category, currency, key, classified
            FROM _dedup
        """)
        con.execute("DROP TABLE _dedup")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
