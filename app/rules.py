"""User-defined category rules: keyword in description -> category.

Rules are a pure read-time layer: a rule matches when its pattern occurs
(case-insensitive substring, whitespace-normalized on both sides) in a
transaction's description, and the
effective_transactions view (ingest.py) applies the FIRST matching
enabled rule in ``position`` order (the user rearranges rows by
dragging them in the UI; ties fall back to id) whenever it computes an
effective category.
Rules write nothing per-row: saving, editing or deleting a rule
instantly reshapes every report, and the original import is never
touched.

Optional scope: a rule may be restricted to one ``source`` (bank) and/or
one ``account`` (the account's CURRENT display label — an import rename
re-scopes its rows read-time, like everything else). NULL = All, so
unscoped rules behave exactly as before. The account scope therefore
follows Manage Imports renames: it targets the label the user sees, not
the value parsed at import time.

Precedence, everywhere: an explicit assignment in category_overrides
(manual edit, LLM auto-classify, or a legacy 'rule' override written
before rules became read-time) beats the rule layer; the rule layer
beats an LLM override; everything beats the CSV-provided category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import duckdb

CREATE_RULES_SQL = """
    CREATE TABLE IF NOT EXISTS category_rules (
        id       INTEGER PRIMARY KEY,
        pattern  VARCHAR,
        category VARCHAR,
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

MAX_PATTERN_LEN = 200
MAX_CATEGORY_LEN = 64
MAX_SOURCE_LEN = 64
MAX_ACCOUNT_LEN = 100


@dataclass
class Rule:
    id: int
    pattern: str
    category: str
    enabled: bool = True
    position: int = 0
    source: Optional[str] = None
    account: Optional[str] = None


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_RULES_SQL)


def ensure_rule_position(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add the position column and backfill it.

    position is the user-controlled priority (drag order in the UI).
    Old databases are ordered purely by id, so position := id keeps
    every existing effective category exactly as it was.
    """
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info('category_rules')").fetchall()}
    if "position" not in cols:
        con.execute(
            "ALTER TABLE category_rules ADD COLUMN position INTEGER")
        con.execute("UPDATE category_rules SET position = id")


def ensure_rule_scope(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add the optional source/account scope columns.

    No backfill is needed — NULL means "All", so every existing rule
    keeps its exact effective behavior on databases that predate the
    scope. Must run before the effective_transactions view is (re)
    created: the view's rule subquery references these columns.
    """
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info('category_rules')").fetchall()}
    if "source" not in cols:
        con.execute("ALTER TABLE category_rules ADD COLUMN source VARCHAR")
    if "account" not in cols:
        con.execute(
            "ALTER TABLE category_rules ADD COLUMN account VARCHAR")


def load_rules(con: duckdb.DuckDBPyConnection,
               enabled_only: bool = True) -> list[Rule]:
    """All rules in priority order. ``enabled_only`` skips disabled ones."""
    try:
        ensure_table(con)
    except duckdb.InvalidInputException:
        pass  # read-only connection — the table either exists or the
        # SELECT below raises anyway
    ensure_rule_position(con)
    ensure_rule_scope(con)
    sql = ("SELECT id, pattern, category, enabled, position, source, account "
           "FROM category_rules")
    if enabled_only:
        sql += " WHERE enabled"
    sql += " ORDER BY position, id"
    return [Rule(i, p, c, bool(e), pos, s, a)
            for i, p, c, e, pos, s, a in con.execute(sql).fetchall()]


def normalize_match_text(value: Optional[str]) -> str:
    """Canonical text for rule matching: whitespace runs -> single space.

    Descriptions are stored as imported — real bank extracts keep runs
    of spaces (e.g. 'U.S. BANK        WEB PYMT') — while the UI renders
    them collapsed, so patterns copied from the UI use single spaces.
    Matching in this canonical form (callers case-fold with .lower())
    makes rules robust to that; it mirrors the whitespace normalization
    of the dedup key (ingest.build_key).
    """
    return re.sub(r"\s+", " ", (value or "")).strip()


def scope_matches(rule_source: Optional[str], rule_account: Optional[str],
                  txn_source: Optional[str],
                  txn_account: Optional[str]) -> bool:
    """Whether a rule's scope admits a transaction.

    NULL scope = All: that dimension never filters. A set scope must
    equal the transaction's value exactly (both sides come from the UI's
    dropdowns over live data, so there is no normalization concern).
    """
    return ((rule_source is None or rule_source == txn_source)
            and (rule_account is None or rule_account == txn_account))


def matching_rules(description: str, rules: list[Rule],
                   source: Optional[str] = None,
                   account: Optional[str] = None) -> list[Rule]:
    """Every rule whose pattern matches the description AND whose scope
    admits the transaction (source/account are the row's values; pass
    them so scoped rules are evaluated exactly like the view does). In
    evaluation order — the first entry is the winner (the rule the
    effective_transactions view applies); the rest are shadowed by it."""
    d = normalize_match_text(description).lower()
    if not d:
        return []
    return [r for r in rules
            if scope_matches(r.source, r.account, source, account)
            and normalize_match_text(r.pattern).lower() in d]


def match_category(description: str, rules: list[Rule],
                   source: Optional[str] = None,
                   account: Optional[str] = None) -> Optional[str]:
    """Category from the first matching rule, or None."""
    matched = matching_rules(description, rules,
                             source=source, account=account)
    return matched[0].category if matched else None


def validate(pattern: str, category: str,
             source: Optional[str] = None,
             account: Optional[str] = None
             ) -> tuple[str, str, Optional[str], Optional[str]]:
    """Normalize and strip inputs, reject bad ones (ValueError -> 400
    upstream). Empty scope strings become None (All)."""
    pattern = normalize_match_text(pattern)
    category = (category or "").strip()
    source = (source or "").strip() or None
    account = (account or "").strip() or None
    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LEN:
        raise ValueError(f"pattern too long (max {MAX_PATTERN_LEN} chars)")
    if not category:
        raise ValueError("category must not be empty")
    if len(category) > MAX_CATEGORY_LEN:
        raise ValueError(f"category too long (max {MAX_CATEGORY_LEN} chars)")
    if source and len(source) > MAX_SOURCE_LEN:
        raise ValueError(f"source too long (max {MAX_SOURCE_LEN} chars)")
    if account and len(account) > MAX_ACCOUNT_LEN:
        raise ValueError(f"account too long (max {MAX_ACCOUNT_LEN} chars)")
    return pattern, category, source, account


def create_rule(con: duckdb.DuckDBPyConnection, pattern: str,
                category: str,
                source: Optional[str] = None,
                account: Optional[str] = None) -> Rule:
    pattern, category, source, account = validate(
        pattern, category, source, account)
    ensure_table(con)
    # Duplicate pattern+scope (case-insensitive) is confusing: one row
    # can only ever yield one category, so forbid the ambiguity. The
    # same pattern is fine under DIFFERENT scopes (e.g. one merchant
    # mapped per bank).
    scope = (source, account)
    for r in load_rules(con, enabled_only=False):
        # Normalize both sides: existing rows may predate canonical
        # storage and still hold raw whitespace runs.
        if (normalize_match_text(r.pattern).lower() == pattern.lower()
                and (r.source, r.account) == scope):
            raise ValueError(
                f"a rule with pattern '{r.pattern}' already exists (id {r.id})"
                + (" — same source/account scope"
                   if any(scope) else ""))
    new_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM category_rules").fetchone()[0]
    # New rules append to the end of the priority list (lowest
    # precedence); the user can drag one up where it belongs.
    new_pos = con.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM category_rules"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO category_rules "
        "(id, pattern, category, enabled, position, source, account) "
        "VALUES (?,?,?,true,?,?,?)",
        [new_id, pattern, category, new_pos, source, account])
    return Rule(new_id, pattern, category, True, new_pos, source, account)


def update_rule_category(con: duckdb.DuckDBPyConnection, rule_id: int,
                         category: str) -> Rule | None:
    """Change the category an existing rule assigns.

    Only the category moves — the pattern, scope, and priority (position)
    are untouched, so the rule keeps matching the same rows and still
    wins/loses the same precedence battles; every report the category
    feeds reshapes at read time, like any other rule change. The new
    category may be the reserved ``Ignored`` (the rule moves between the
    rules and Ignore panels in the UI). Returns None if no rule has
    ``rule_id`` (-> 404 upstream); ValueError on a bad category (-> 400).
    """
    ensure_table(con)
    category = (category or "").strip()
    if not category:
        raise ValueError("category must not be empty")
    if len(category) > MAX_CATEGORY_LEN:
        raise ValueError(f"category too long (max {MAX_CATEGORY_LEN} chars)")
    cur = con.execute(
        "UPDATE category_rules SET category = ? WHERE id = ?",
        [category, rule_id])
    if (cur.rowcount or 0) == 0:
        return None
    for r in load_rules(con, enabled_only=False):
        if r.id == rule_id:
            return r
    return None


def delete_rule(con: duckdb.DuckDBPyConnection, rule_id: int) -> bool:
    """True if a row was deleted."""
    ensure_table(con)
    cur = con.execute(
        "DELETE FROM category_rules WHERE id = ?", [rule_id])
    return (cur.rowcount or 0) > 0


def reorder_rules(con: duckdb.DuckDBPyConnection,
                  ordered_ids: list[int]) -> list[Rule]:
    """Persist a new priority order (drag-and-drop from the UI).

    ``ordered_ids`` must list every rule id exactly once, highest
    priority first; positions are rewritten densely (0..n-1).
    ValueError (-> 400) on duplicates, unknown ids, or a missing rule
    (stale client state — refresh and re-drag).
    """
    ensure_table(con)
    ensure_rule_position(con)
    existing = [row[0] for row in
                con.execute("SELECT id FROM category_rules").fetchall()]
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
        con.execute("UPDATE category_rules SET position = ? WHERE id = ?",
                    [pos, rid])
    return load_rules(con, enabled_only=False)


def match_counts(con: duckdb.DuckDBPyConnection,
                 rules: list[Rule]) -> dict[int, int]:
    """How many transactions each rule's pattern matches (per rule).

    Counts are per-pattern (within the rule's scope) — one row can
    match several patterns — so they are a display aid, not a total of
    effective re-categorizations (the view applies first-match
    precedence). Scope: a scoped rule only counts rows it admits.
    Soft-deleted rows are excluded via the effective_transactions view.
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


def net_by_rule(con: duckdb.DuckDBPyConnection,
                rules: list[Rule]) -> dict[int, float]:
    """Net amount per rule: signed sum of the amounts of the rows the
    rule actually categorizes — only rows where the RULE LAYER is the
    effective source (effective ``override_source = 'rule'``), and
    among those, only the rows where it is the FIRST matching enabled
    pattern in priority order (the same winner the effective_transactions
    view picks for ``rule_category``).

    So a row matching several patterns is counted once, for its winning
    rule only (nets never double-count across rules — the adjacent
    match counts do, they are per-pattern), and a row the user manually
    re-categorized does not count at all. Stored 'llm' and legacy-NULL
    overrides lose to a rule match in the view; stored 'manual' (and
    legacy 'rule') overrides win, so those rows are excluded here.
    Signed: expenses negative, income positive. Soft-deleted rows are
    excluded via the effective_transactions view. Scope: a row only
    counts for a rule the row's source/account admits — the first-match
    winner is computed per row among its admissible rules, exactly like
    the effective_transactions view.
    """
    nets: dict[int, float] = {r.id: 0.0 for r in rules}
    if rules:
        normed = [(r.id, normalize_match_text(r.pattern).lower(),
                   r.source, r.account) for r in rules]
        for desc, amount, src, t_src, t_acct in con.execute(
                "SELECT description, amount, override_source, source, account "
                "FROM effective_transactions").fetchall():
            if src != 'rule':
                continue  # effective category did not come from the rule layer
            d = normalize_match_text(desc).lower()
            if not d or amount is None:
                continue
            for rid, p, r_src, r_acct in normed:
                if (p and p in d
                        and scope_matches(r_src, r_acct, t_src, t_acct)):
                    nets[rid] += float(amount)
                    break  # first match wins — shadowed rules get nothing
    return nets
