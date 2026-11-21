#!/usr/bin/env python3
"""Generate realistic sample CSV extracts for all four institutions.

Coverage: full year 2025 (Jan 1 - Dec 31) plus 2026 YTD (Jan 1 - ~Jan 15).
Nothing prior to 2025 is generated.

Files written (overlapping date ranges so the deduplication logic is
exercised):
  - Wells Fargo checking: 2025-01 .. 2026-01  (payroll + brokerage dividends)
  - Chase checking:       2025-01 .. 2026-01  (mirrors some WF rows for dedup)
  - US Bank:              2025-01 .. 2026-01  (rent, interest, fees, transfers)
  - Apple Card:           2025-01 .. 2026-01

Sign convention: only Income and Investing rows are credits (positive);
every merchant/purchase row is a debit (negative), so in the app only
Income and Investing categories come out net positive.

Re-running this script deletes the previously generated sample CSVs in
app/data/extracts first (self-cleaning: old-range files are not left behind).
"""
from __future__ import annotations
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent / "app" / "data" / "extracts"
OUT.mkdir(parents=True, exist_ok=True)

# Self-clean: drop sample CSVs from earlier runs (any date range).
for old in OUT.glob("*.csv"):
    if old.name.startswith(("wells_fargo_", "chase_", "us_bank_", "apple_card_")):
        old.unlink()

START = date(2025, 1, 1)
END = date(2026, 1, 15)

WF = OUT / "wells_fargo_2025-01_to_2026-01.csv"
CHASE = OUT / "chase_2025-01_to_2026-01.csv"
USBANK = OUT / "us_bank_2025-01_to_2026-01.csv"
APPLE = OUT / "apple_card_2025-01_to_2026-01.csv"


def rng(d0: date, d1: date, n: int) -> list[date]:
    days = (d1 - d0).days
    return [d0 + timedelta(days=random.randint(0, days)) for _ in range(n)]


def money(x: float) -> str:
    return f"(${x:,.2f})" if x < 0 else f"${x:,.2f}"


def monthly(day: int, first: date, last: date) -> list[date]:
    """Every occurrence of `day` in the month grid from first to last."""
    out, d = [], date(first.year, first.month, day)
    if d < first:
        d = d.replace(year=d.year + 1) if d.month == 12 else d.replace(month=d.month + 1)
    while d <= last:
        out.append(d)
        nxt_month = d.month % 12 + 1
        nxt_year = d.year + (1 if d.month == 12 else 0)
        d = date(nxt_year, nxt_month, day)
    return out


# --- Wells Fargo -----------------------------------------------------------
wf_rows = []
wf_dates = rng(START, END, 130)
wf_merchants = [
    ("TRADER JOES #4471", "Groceries", -1),
    ("STARBUCKS 000123", "Dining", -1),
    ("SHELL GAS 88213", "Transportation", -1),
    ("NETFLIX.COM", "Subscriptions", -1),
    ("AMC THEATRES", "Entertainment", -1),
    ("CVS PHARMACY", "Health", -1),
    ("COSTCO WHOLESALE", "Groceries", -1),
    ("UBER TRIP", "Transportation", -1),
    ("COMCAST BROADBAND", "Utilities", -1),
    ("WHOLE FOODS", "Groceries", -1),
]
for d in wf_dates:
    m = random.choice(wf_merchants)
    amt = round(random.uniform(4, 180), 2) * m[2]
    wf_rows.append([d.strftime("%Y-%m-%d"), m[0], "Debit", money(amt), m[1], ""])
# Payroll, 15th of every month (2025-01 .. 2026-01): Income
for d in monthly(15, START, END):
    wf_rows.append([d.strftime("%Y-%m-%d"), "ACME CORP PAYROLL", "Deposit",
                    money(3200), "Income", ""])
# Quarterly-ish brokerage dividends: the only Investing credits
for d, amt in [(date(2025, 2, 21), 142.60), (date(2025, 5, 22), 168.35),
               (date(2025, 8, 21), 129.80), (date(2025, 11, 20), 187.45),
               (date(2026, 1, 20), 156.90)]:
    wf_rows.append([d.strftime("%Y-%m-%d"), "FIDELITY DIVIDEND", "Deposit",
                    money(amt), "Investing", ""])
wf_rows.sort(key=lambda r: r[0])
with open(WF, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Type", "Amount", "Category", "Payment Type"])
    w.writerows(wf_rows)

# --- Chase -----------------------------------------------------------------
# Reuse ~15 of the WF rows (same description/amount/date) to force dedup overlap,
# plus its own rows.
chase_rows = []
overlap = random.sample(wf_rows, 15)  # these will dedup against WF
for r in overlap:
    chase_rows.append([r[0], r[1], r[4], r[3]])  # Date, Desc, Category, Amount
ch_dates = rng(START, END, 120)
ch_merchants = [
    ("DOORDASH #99120", "Dining"),
    ("GAP STORE", "Shopping"),
    ("SAMS CLUB", "Groceries"),
    ("LYFT RIDE", "Transportation"),
    ("SPOTIFY", "Subscriptions"),
    ("UNION PLAZA GYM", "Health"),
    ("VERIZON WIRELESS", "Utilities"),
    ("AMAZON MARKETPLACE", "Shopping"),
]
for d in ch_dates:
    m = random.choice(ch_merchants)
    amt = round(random.uniform(5, 220), 2)
    chase_rows.append([d.strftime("%Y-%m-%d"), m[0], m[1], money(-amt)])
# Payroll staggered one month behind WF (2025-02 .. 2026-01): Income
for d in monthly(15, date(2025, 2, 1), END):
    chase_rows.append([d.strftime("%Y-%m-%d"), "ACME CORP PAYROLL", "Income",
                       money(3200)])
chase_rows.sort(key=lambda r: r[0])
with open(CHASE, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Transaction Date", "Description", "Category", "Amount"])
    w.writerows(chase_rows)

# --- US Bank ---------------------------------------------------------------
# The parser builds descriptions from the Name + Memo columns (real US Bank
# credit-card export layout), so the data lives there — the row then comes in
# uncategorized and the LLM/rule/manual classification does the work.
usb_rows = []
def usb(desc: str, amt: float, d: date, typ: str, memo: str = "") -> None:
    usb_rows.append([d.strftime("%Y-%m-%d"), typ, desc, memo, money(amt)])
# Monthly rhythm: rent on the 1st, service fee on the 3rd, interest on the 28th
for d in monthly(1, START, END):
    usb("RENT", -1800, d, "Auto Pay")
for d in monthly(3, START, END):
    usb("MONTHLY SERVICE FEE", -15, d, "Service Fee")
for d in monthly(28, START, END):
    usb("SAVINGS INTEREST", 12.5, d, "Interest")
for d in rng(START, END, 8):
    usb("INTERNAL TRANSFER", -500, d, "Transfer", "FROM CHECKING")
usb_rows.sort(key=lambda r: r[0])
with open(USBANK, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Transaction", "Name", "Memo", "Amount"])
    w.writerows(usb_rows)

# --- Apple Card ------------------------------------------------------------
ap_rows = []
ap_dates = rng(START, END, 120)
ap_merchants = [
    ("APPLE.COM/BILL", "Subscriptions"),
    ("ICLOUD STORAGE", "Subscriptions"),
    ("TARGET #2201", "Shopping"),
    ("BLUE BOTTLE COFFEE", "Dining"),
    ("AIRBNB RESERVATION", "Travel"),
    ("DELTA AIRLINES", "Travel"),
    ("SNAPCHAT", "Entertainment"),
    ("NETFLIX", "Subscriptions"),
]
for d in ap_dates:
    m = random.choice(ap_merchants)
    amt = round(random.uniform(3, 300), 2)
    # Apple Card reports purchases as POSITIVE amounts (real export
    # convention); the parser flips them to expenses. Writing them negated
    # here would double-flip and store the purchases as credits.
    ap_rows.append([d.strftime("%Y-%m-%d"), m[0], money(amt), "USD", m[1]])
ap_rows.sort(key=lambda r: r[0])
with open(APPLE, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Amount", "Currency", "Category"])
    w.writerows(ap_rows)

print(f"Wrote {WF.name}: {len(wf_rows)} rows")
print(f"Wrote {CHASE.name}: {len(chase_rows)} rows")
print(f"Wrote {USBANK.name}: {len(usb_rows)} rows")
print(f"Wrote {APPLE.name}: {len(ap_rows)} rows")
