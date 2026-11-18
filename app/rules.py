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
        position INTEGER DEFAULT 0
    )
"""

MAX_PATTERN_LEN = 200
MAX_CATEGORY_LEN = 64


@dataclass
class Rule:
    id: int
    pattern: str
    category: str
    enabled: bool = True
    position: int = 0


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


def load_rules(con: duckdb.DuckDBPyConnection,
               enabled_only: bool = True) -> list[Rule]:
    """Rules in evaluation order (user drag order, first match wins)."""
    ensure_table(con)
    ensure_rule_position(con)
    sql = ("SELECT id, pattern, category, enabled, position "
           "FROM category_rules")
    if enabled_only:
        sql += " WHERE enabled"
    sql += " ORDER BY position, id"
    return [Rule(i, p, c, bool(e), pos)
            for i, p, c, e, pos in con.execute(sql).fetchall()]


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


def match_category(description: str, rules: list[Rule]) -> Optional[str]:
    """Category from the first matching rule, or None."""
    d = normalize_match_text(description).lower()
    if not d:
        return None
    for r in rules:
        if normalize_match_text(r.pattern).lower() in d:
            return r.category
    return None


def validate(pattern: str, category: str) -> tuple[str, str]:
    """Normalize the pattern, strip inputs, reject bad ones (ValueError -> 400 upstream)."""
    pattern = normalize_match_text(pattern)
    category = (category or "").strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LEN:
        raise ValueError(f"pattern too long (max {MAX_PATTERN_LEN} chars)")
    if not category:
        raise ValueError("category must not be empty")
    if len(category) > MAX_CATEGORY_LEN:
        raise ValueError(f"category too long (max {MAX_CATEGORY_LEN} chars)")
    return pattern, category


def create_rule(con: duckdb.DuckDBPyConnection, pattern: str,
                category: str) -> Rule:
    pattern, category = validate(pattern, category)
    ensure_table(con)
    # Duplicate patterns (case-insensitive) are confusing: one description
    # can only ever yield one category, so forbid the ambiguity.
    for r in load_rules(con, enabled_only=False):
        # Normalize both sides: existing rows may predate canonical
        # storage and still hold raw whitespace runs.
        if normalize_match_text(r.pattern).lower() == pattern.lower():
            raise ValueError(
                f"a rule with pattern '{r.pattern}' already exists (id {r.id})")
    new_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM category_rules").fetchone()[0]
    # New rules append to the end of the priority list (lowest
    # precedence); the user can drag one up where it belongs.
    new_pos = con.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM category_rules"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO category_rules (id, pattern, category, enabled, position) "
        "VALUES (?,?,?,true,?)", [new_id, pattern, category, new_pos])
    return Rule(new_id, pattern, category, True, new_pos)


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

    Counts are per-pattern — one row can match several patterns — so
    they are a display aid, not a total of effective re-categorizations
    (the view applies first-match precedence). Soft-deleted rows are
    excluded via the effective_transactions view.
    """
    matches: dict[int, int] = {r.id: 0 for r in rules}
    if rules:
        normed = [(r.id, normalize_match_text(r.pattern).lower()) for r in rules]
        for _id, desc in con.execute(
                "SELECT id, description FROM effective_transactions").fetchall():
            d = normalize_match_text(desc).lower()
            if not d:
                continue
            for rid, p in normed:
                if p in d:
                    matches[rid] += 1
    return matches
