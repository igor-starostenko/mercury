#!/usr/bin/env python3
"""Generate realistic sample CSV extracts for all four institutions.

Creates overlapping date ranges so the deduplication logic is exercised:
  - Wells Fargo checking: 2025-09 .. 2025-11
  - Chase checking:        2025-10 .. 2025-12   (overlaps WF Oct/Nov)
  - US Bank savings:       2025-11 .. 2025-12
  - Apple Card:            2025-09 .. 2025-12

Wells Fargo and Chase both have overlapping Oct/Nov with a few identical
transactions (same account activity mirrored) to demonstrate dedup.
"""
from __future__ import annotations
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent / "app" / "data" / "extracts"
OUT.mkdir(parents=True, exist_ok=True)

WF = OUT / "wells_fargo_2025-09_to_2025-11.csv"
CHASE = OUT / "chase_2025-10_to_2025-12.csv"
USBANK = OUT / "us_bank_2025-11_to_2025-12.csv"
APPLE = OUT / "apple_card_2025-09_to_2025-12.csv"


def rng(d0: date, d1: date, n: int) -> list[date]:
    days = (d1 - d0).days
    return [d0 + timedelta(days=random.randint(0, days)) for _ in range(n)]


def money(x: float) -> str:
    return f"(${x:,.2f})" if x < 0 else f"${x:,.2f}"


# --- Wells Fargo -----------------------------------------------------------
wf_rows = []
wf_dates = rng(date(2025, 9, 1), date(2025, 11, 30), 40)
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
# Add a couple of income rows (payroll)
for d in [date(2025, 9, 15), date(2025, 10, 15), date(2025, 11, 15)]:
    wf_rows.append([d.strftime("%Y-%m-%d"), "ACME CORP PAYROLL", "Deposit",
                    money(3200), "Income", ""])
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
ch_dates = rng(date(2025, 10, 1), date(2025, 12, 31), 35)
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
for d in [date(2025, 10, 15), date(2025, 11, 15), date(2025, 12, 15)]:
    chase_rows.append([d.strftime("%Y-%m-%d"), "ACME CORP PAYROLL", "Income",
                       money(3200)])
chase_rows.sort(key=lambda r: r[0])
with open(CHASE, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Transaction Date", "Description", "Category", "Amount"])
    w.writerows(chase_rows)

# --- US Bank ---------------------------------------------------------------
usb_rows = []
usb_dates = rng(date(2025, 11, 1), date(2025, 12, 31), 20)
usb_merchants = [
    ("INTERNAL TRANSFER FROM CHECKING", "Transfers"),
    ("SAVINGS INTEREST", "Income"),
    ("MONTHLY SERVICE FEE", "Fees"),
    ("AUTO PAY - RENT", "Housing"),
]
for d in usb_dates:
    m = random.choice(usb_merchants)
    if m[1] == "Income":
        amt = 12.5
    elif m[1] == "Fees":
        amt = -15
    elif m[1] == "Housing":
        amt = -1800
    else:
        amt = -500
    usb_rows.append([d.strftime("%Y-%m-%d"), m[0], m[1], money(amt)])
usb_rows.sort(key=lambda r: r[0])
with open(USBANK, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Type", "Amount", "Category"])
    w.writerows(usb_rows)

# --- Apple Card ------------------------------------------------------------
ap_rows = []
ap_dates = rng(date(2025, 9, 1), date(2025, 12, 31), 45)
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
    ap_rows.append([d.strftime("%Y-%m-%d"), m[0], money(-amt), "USD", m[1]])
ap_rows.sort(key=lambda r: r[0])
with open(APPLE, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Amount", "Currency", "Category"])
    w.writerows(ap_rows)

print(f"Wrote {WF.name}: {len(wf_rows)} rows")
print(f"Wrote {CHASE.name}: {len(chase_rows)} rows")
print(f"Wrote {USBANK.name}: {len(usb_rows)} rows")
print(f"Wrote {APPLE.name}: {len(ap_rows)} rows")
