"""User-defined merchant rules: keyword in description -> merchant.

A merchant is a free-form label (a store name) that a transaction can be
tagged with — like a category, but without a fixed vocabulary. Rules are
a pure read-time layer, exactly like the category rules in rules.py: a
rule matches when its pattern occurs (case-insensitive substring,
whitespace-normalized on both sides) in a transaction's description, and
the effective_transactions view (ingest.py) applies the FIRST matching
enabled rule in ``position`` order whenever it computes an effective
merchant.

Rules write nothing per-row: saving, editing or deleting a rule instantly
reshapes the merchant layer, and the original import is never touched.

Optional scope: a rule may be restricted to one ``source`` (bank) and/or
one ``account`` (the account's CURRENT display label). NULL = All.

Precedence, everywhere: an explicit assignment in merchant_overrides
(manual edit or legacy rule override) beats the rule layer; the rule
layer beats an LLM override; everything beats the (nonexistent) CSV
merchant — the imported rows carry no merchant at all, so the merchant
layer is entirely user/LLM/rule-defined.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import duckdb

CREATE_MERCHANT_RULES_SQL = """
    CREATE TABLE IF NOT EXISTS merchant_rules (
        id       INTEGER PRIMARY KEY,
        pattern  VARCHAR,
        merchant VARCHAR,
        enabled  BOOLEAN DEFAULT true,
        position INTEGER DEFAULT 0,
        -- Optional scope (NULL = All): restrict the rule to one bank
        -- and/or one account. The account is matched against the row's
        -- display label (the import's current label wins — see the
        -- effective_transactions view).
        source   VARCHAR,
        account  VARCHAR
    )
"""

CREATE_MERCHANT_OVERRIDES_SQL = """
    CREATE TABLE IF NOT EXISTS merchant_overrides (
        txn_id  INTEGER PRIMARY KEY,
        merchant VARCHAR,
        source  VARCHAR
    )
"""

MAX_PATTERN_LEN = 200
MAX_MERCHANT_LEN = 100
MAX_SOURCE_LEN = 64
MAX_ACCOUNT_LEN = 100


@dataclass
class MerchantRule:
    id: int
    pattern: str
    merchant: str
    enabled: bool = True
    position: int = 0
    source: Optional[str] = None
    account: Optional[str] = None


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_MERCHANT_RULES_SQL)


def ensure_overrides_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_MERCHANT_OVERRIDES_SQL)


def ensure_rule_position(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add the position column and backfill it."""
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info('merchant_rules')").fetchall()}
    if "position" not in cols:
        con.execute(
            "ALTER TABLE merchant_rules ADD COLUMN position INTEGER")
        con.execute("UPDATE merchant_rules SET position = id")


def ensure_rule_scope(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add the optional source/account scope columns."""
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info('merchant_rules')").fetchall()}
    if "source" not in cols:
        con.execute("ALTER TABLE merchant_rules ADD COLUMN source VARCHAR")
    if "account" not in cols:
        con.execute(
            "ALTER TABLE merchant_rules ADD COLUMN account VARCHAR")


def load_rules(con: duckdb.DuckDBPyConnection,
               enabled_only: bool = True) -> list[MerchantRule]:
    """All rules in priority order. ``enabled_only`` skips disabled ones."""
    try:
        ensure_table(con)
    except duckdb.InvalidInputException:
        pass  # read-only connection — the table either exists or the
        # SELECT below raises anyway
    ensure_rule_position(con)
    ensure_rule_scope(con)
    sql = ("SELECT id, pattern, merchant, enabled, position, source, account "
           "FROM merchant_rules")
    if enabled_only:
        sql += " WHERE enabled"
    sql += " ORDER BY position, id"
    return [MerchantRule(i, p, m, bool(e), pos, s, a)
            for i, p, m, e, pos, s, a in con.execute(sql).fetchall()]


def normalize_match_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def scope_matches(rule_source: Optional[str], rule_account: Optional[str],
                  txn_source: Optional[str],
                  txn_account: Optional[str]) -> bool:
    return ((rule_source is None or rule_source == txn_source)
            and (rule_account is None or rule_account == txn_account))


def matching_rules(description: str, rules: list[MerchantRule],
                   source: Optional[str] = None,
                   account: Optional[str] = None) -> list[MerchantRule]:
    """Every rule whose pattern matches the description AND whose scope
    admits the transaction. In evaluation order — the first entry is the
    winner (the rule the effective_transactions view applies)."""
    d = normalize_match_text(description).lower()
    if not d:
        return []
    return [r for r in rules
            if scope_matches(r.source, r.account, source, account)
            and normalize_match_text(r.pattern).lower() in d]


def validate(pattern: str, merchant: str,
             source: Optional[str] = None,
             account: Optional[str] = None
             ) -> tuple[str, str, Optional[str], Optional[str]]:
    pattern = normalize_match_text(pattern)
    merchant = (merchant or "").strip()
    source = (source or "").strip() or None
    account = (account or "").strip() or None
    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LEN:
        raise ValueError(f"pattern too long (max {MAX_PATTERN_LEN} chars)")
    if not merchant:
        raise ValueError("merchant must not be empty")
    if len(merchant) > MAX_MERCHANT_LEN:
        raise ValueError(f"merchant too long (max {MAX_MERCHANT_LEN} chars)")
    if source and len(source) > MAX_SOURCE_LEN:
        raise ValueError(f"source too long (max {MAX_SOURCE_LEN} chars)")
    if account and len(account) > MAX_ACCOUNT_LEN:
        raise ValueError(f"account too long (max {MAX_ACCOUNT_LEN} chars)")
    return pattern, merchant, source, account


def create_rule(con: duckdb.DuckDBPyConnection, pattern: str,
                merchant: str,
                source: Optional[str] = None,
                account: Optional[str] = None) -> MerchantRule:
    pattern, merchant, source, account = validate(
        pattern, merchant, source, account)
    ensure_table(con)
    scope = (source, account)
    for r in load_rules(con, enabled_only=False):
        if (normalize_match_text(r.pattern).lower() == pattern.lower()
                and (r.source, r.account) == scope):
            raise ValueError(
                f"a rule with pattern '{r.pattern}' already exists (id {r.id})"
                + (" — same source/account scope"
                   if any(scope) else ""))
    new_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM merchant_rules").fetchone()[0]
    new_pos = con.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM merchant_rules"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO merchant_rules "
        "(id, pattern, merchant, enabled, position, source, account) "
        "VALUES (?,?,?,true,?,?,?)",
        [new_id, pattern, merchant, new_pos, source, account])
    return MerchantRule(new_id, pattern, merchant, True, new_pos, source, account)


def update_rule_merchant(con: duckdb.DuckDBPyConnection, rule_id: int,
                         merchant: str) -> MerchantRule | None:
    """Change the merchant an existing rule assigns. Only the merchant
    moves — the pattern, scope, and priority are untouched."""
    ensure_table(con)
    merchant = (merchant or "").strip()
    if not merchant:
        raise ValueError("merchant must not be empty")
    if len(merchant) > MAX_MERCHANT_LEN:
        raise ValueError(f"merchant too long (max {MAX_MERCHANT_LEN} chars)")
    cur = con.execute(
        "UPDATE merchant_rules SET merchant = ? WHERE id = ?",
        [merchant, rule_id])
    if (cur.rowcount or 0) == 0:
        return None
    for r in load_rules(con, enabled_only=False):
        if r.id == rule_id:
            return r
    return None


def delete_rule(con: duckdb.DuckDBPyConnection, rule_id: int) -> bool:
    ensure_table(con)
    cur = con.execute(
        "DELETE FROM merchant_rules WHERE id = ?", [rule_id])
    return (cur.rowcount or 0) > 0


def reorder_rules(con: duckdb.DuckDBPyConnection,
                  ordered_ids: list[int]) -> list[MerchantRule]:
    ensure_table(con)
    ensure_rule_position(con)
    existing = [row[0] for row in
                con.execute("SELECT id FROM merchant_rules").fetchall()]
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("each rule may appear only once in the new order")
    if set(ordered_ids) != set(existing):
        missing = sorted(set(existing) - set(ordered_ids))
        unknown = sorted(set(ordered_ids) - set(existing))
        if missing:
            raise ValueError(
                f"order is missing rule id(s): {missing} — refresh and retry")
        raise ValueError(f"unknown rule id(s): {unknown} — refresh and retry")
    for pos, rid in enumerate(ordered_ids):
        con.execute("UPDATE merchant_rules SET position = ? WHERE id = ?",
                    [pos, rid])
    return load_rules(con, enabled_only=False)


def set_merchant(con: duckdb.DuckDBPyConnection, txn_id: int,
                 merchant: Optional[str],
                 source: str = "manual") -> bool:
    """Set the effective merchant for a transaction.

    ``merchant`` empty/None removes the override (row reverts to rule
    layer / LLM / nothing). ``source`` records who set it: 'user' for a
    manual edit, 'llm' for an auto-classify. Returns False if the
    transaction does not exist.
    """
    ensure_overrides_table(con)
    if not con.execute(
            "SELECT 1 FROM transactions WHERE id = ?", [txn_id]).fetchone():
        return False
    merchant = (merchant or "").strip() or None
    if merchant is None:
        con.execute("DELETE FROM merchant_overrides WHERE txn_id = ?",
                    [txn_id])
        return True
    if len(merchant) > MAX_MERCHANT_LEN:
        raise ValueError(f"merchant too long (max {MAX_MERCHANT_LEN} chars)")
    con.execute("INSERT INTO merchant_overrides (txn_id, merchant, source) "
                "VALUES (?,?,?) "
                "ON CONFLICT (txn_id) DO UPDATE SET merchant = excluded.merchant, "
                "source = excluded.source",
                [txn_id, merchant, source])
    return True


def clear_overrides(con: duckdb.DuckDBPyConnection,
                    txn_ids: list[int]) -> int:
    """Drop stored merchant overrides for a list of transactions."""
    ensure_overrides_table(con)
    if not txn_ids:
        return 0
    cur = con.execute(
        "DELETE FROM merchant_overrides WHERE txn_id IN (SELECT * FROM unnest(?))",
        [txn_ids])
    return cur.rowcount or 0


def load_overrides(con: duckdb.DuckDBPyConnection) -> dict[int, dict]:
    """txn_id -> {merchant, source} for every stored override."""
    ensure_overrides_table(con)
    return {r[0]: {"merchant": r[1], "source": r[2]} for r in con.execute(
        "SELECT txn_id, merchant, source FROM merchant_overrides").fetchall()}


def match_counts(con: duckdb.DuckDBPyConnection,
                 rules: list[MerchantRule]) -> dict[int, int]:
    """How many transactions each rule's pattern matches (per rule).

    Counts are per-pattern (within the rule's scope) — one row can match
    several patterns — so they are a display aid, not a total of
    effective re-merchantings (the view applies first-match precedence).
    Scope: a scoped rule only counts rows it admits. Soft-deleted rows
    are excluded via the effective_transactions view.
    """
    matches: dict[int, int] = {r.id: 0 for r in rules}
    if rules:
        normed = [(r.id, normalize_match_text(r.pattern).lower(),
                   r.source, r.account) for r in rules]
        for _id, desc, src, acct in con.execute(
                "SELECT id, description, source, account "
                "FROM effective_transactions").fetchall():
            d = normalize_match_text(desc).lower()
            if not d:
                continue
            for rid, p, r_src, r_acct in normed:
                if scope_matches(r_src, r_acct, src, acct) and p in d:
                    matches[rid] += 1
    return matches


# ---------------------------------------------------------------------------
# Distinct merchants: colors, rename, remove
# ---------------------------------------------------------------------------
CREATE_MERCHANT_COLORS_SQL = """
    CREATE TABLE IF NOT EXISTS merchant_colors (
        merchant VARCHAR PRIMARY KEY,
        color    VARCHAR
    )
"""

# A spread of visually distinct, saturated-but-not-neon colors. A new
# merchant gets one at random; the user can change it from the panel.
_MERCHANT_PALETTE = [
    '#e5533d', '#3d8be5', '#3da35d', '#d9a02b', '#9b59b6',
    '#1fa3a3', '#e07b39', '#c43d7c', '#5b8c3e', '#4a6fa5',
    '#b05c2a', '#2a9d8f', '#8a4fd0', '#d63d6e', '#6d8a2a', '#3a86c8',
]

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def ensure_colors_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_MERCHANT_COLORS_SQL)


def load_colors(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    ensure_colors_table(con)
    return {r[0]: r[1] for r in con.execute(
        "SELECT merchant, color FROM merchant_colors").fetchall()}


def color_for(con: duckdb.DuckDBPyConnection, merchant: str) -> str:
    """The merchant's stored color, or a random palette pick (persisted)."""
    ensure_colors_table(con)
    row = con.execute(
        "SELECT color FROM merchant_colors WHERE merchant = ?",
        [merchant]).fetchone()
    if row:
        return row[0]
    import random
    color = random.choice(_MERCHANT_PALETTE)
    con.execute("INSERT INTO merchant_colors (merchant, color) VALUES (?,?)",
                [merchant, color])
    return color


def set_color(con: duckdb.DuckDBPyConnection, merchant: str,
              color: str) -> str:
    merchant = (merchant or "").strip()
    color = (color or "").strip()
    if not merchant:
        raise ValueError("merchant must not be empty")
    if not _COLOR_RE.match(color):
        raise ValueError("color must be a #rrggbb hex value")
    ensure_colors_table(con)
    con.execute("INSERT INTO merchant_colors (merchant, color) VALUES (?,?) "
                "ON CONFLICT (merchant) DO UPDATE SET color = excluded.color",
                [merchant, color])
    return color


def rename_merchant(con: duckdb.DuckDBPyConnection, old: str,
                    new: str) -> dict:
    """Re-tag every row carrying `old` to `new`: stored overrides, rule
    layer, and the color entry. Rules/overrides are rewritten, not copied —
    a renamed merchant does not leave its old label behind."""
    old = (old or "").strip()
    new = (new or "").strip()
    if not old:
        raise ValueError("old merchant must not be empty")
    if not new:
        raise ValueError("new merchant must not be empty")
    if len(new) > MAX_MERCHANT_LEN:
        raise ValueError(f"merchant too long (max {MAX_MERCHANT_LEN} chars)")
    if old == new:
        return {"old": old, "new": new, "overrides": 0, "rules": 0}
    ensure_table(con)
    ensure_overrides_table(con)
    n_overrides = con.execute(
        "UPDATE merchant_overrides SET merchant = ? WHERE merchant = ?",
        [new, old]).rowcount or 0
    n_rules = con.execute(
        "UPDATE merchant_rules SET merchant = ? WHERE merchant = ?",
        [new, old]).rowcount or 0
    # Color: the target name keeps its own color if it has one; otherwise
    # it inherits the old merchant's color.
    ensure_colors_table(con)
    old_color = con.execute(
        "SELECT color FROM merchant_colors WHERE merchant = ?", [old]).fetchone()
    new_color = con.execute(
        "SELECT color FROM merchant_colors WHERE merchant = ?", [new]).fetchone()
    if old_color and not new_color:
        con.execute("INSERT INTO merchant_colors (merchant, color) VALUES (?,?) "
                    "ON CONFLICT (merchant) DO UPDATE SET color = excluded.color",
                    [new, old_color[0]])
    con.execute("DELETE FROM merchant_colors WHERE merchant = ?", [old])
    return {"old": old, "new": new, "overrides": n_overrides, "rules": n_rules}


def remove_merchant(con: duckdb.DuckDBPyConnection, merchant: str) -> dict:
    """Drop every trace of a merchant: overrides (rows revert to the rule
    layer/nothing), rules (merchant layer disappears), and the color entry.
    Returns counts of what was removed."""
    merchant = (merchant or "").strip()
    if not merchant:
        raise ValueError("merchant must not be empty")
    ensure_table(con)
    ensure_overrides_table(con)
    n_overrides = con.execute(
        "DELETE FROM merchant_overrides WHERE merchant = ?",
        [merchant]).rowcount or 0
    n_rules = con.execute(
        "DELETE FROM merchant_rules WHERE merchant = ?",
        [merchant]).rowcount or 0
    ensure_colors_table(con)
    n_colors = con.execute(
        "DELETE FROM merchant_colors WHERE merchant = ?",
        [merchant]).rowcount or 0
    return {"merchant": merchant, "overrides": n_overrides,
            "rules": n_rules, "color": n_colors}


def distinct_merchants(con: duckdb.DuckDBPyConnection,
                       limit: int = 1000) -> list[dict]:
    """All distinct effective merchants (ignored rows excluded), newest-
    nowhere sorted by gross debit spend descending. Each entry carries the
    stored-or-randomly-assigned color and the aggregate stats."""
    rows = con.execute("""
        SELECT v.merchant, COUNT(*) AS n,
               SUM(v.amount) AS net,
               SUM(CASE WHEN v.amount < 0 THEN -v.amount ELSE 0 END) AS spend
        FROM effective_transactions AS v
        WHERE v.merchant IS NOT NULL AND v.merchant <> ''
          AND COALESCE(v.category, '') <> 'Ignored'
          AND COALESCE(v.date_ignored, false) = false
        GROUP BY v.merchant
        ORDER BY spend DESC, merchant ASC
        LIMIT ?
    """, [limit]).fetchall()
    ensure_colors_table(con)
    out = []
    for merchant, n, net, spend in rows:
        out.append({
            "merchant": merchant,
            "n": int(n),
            "net": float(net or 0),
            "spend": float(spend or 0),
            "color": color_for(con, merchant),
        })
    return out
