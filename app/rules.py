"""User-defined category rules: keyword in description -> category.

Rules are a pure read-time layer: a rule matches when its pattern occurs
(case-insensitive substring) in a transaction's description, and the
effective_transactions view (ingest.py) applies the FIRST matching
enabled rule (id order) whenever it computes an effective category.
Rules write nothing per-row: saving, editing or deleting a rule
instantly reshapes every report, and the original import is never
touched.

Precedence, everywhere: an explicit assignment in category_overrides
(manual edit, LLM auto-classify, or a legacy 'rule' override written
before rules became read-time) beats the rule layer; the rule layer
beats an LLM override; everything beats the CSV-provided category.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb

CREATE_RULES_SQL = """
    CREATE TABLE IF NOT EXISTS category_rules (
        id       INTEGER PRIMARY KEY,
        pattern  VARCHAR,
        category VARCHAR,
        enabled  BOOLEAN DEFAULT true
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


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(CREATE_RULES_SQL)


def load_rules(con: duckdb.DuckDBPyConnection,
               enabled_only: bool = True) -> list[Rule]:
    """Rules in evaluation order (id = creation order, first match wins)."""
    ensure_table(con)
    sql = "SELECT id, pattern, category, enabled FROM category_rules"
    if enabled_only:
        sql += " WHERE enabled"
    sql += " ORDER BY id"
    return [Rule(i, p, c, bool(e))
            for i, p, c, e in con.execute(sql).fetchall()]


def match_category(description: str, rules: list[Rule]) -> Optional[str]:
    """Category from the first matching rule, or None."""
    d = (description or "").lower()
    if not d:
        return None
    for r in rules:
        if r.pattern.lower() in d:
            return r.category
    return None


def validate(pattern: str, category: str) -> tuple[str, str]:
    """Strip inputs and reject bad ones (ValueError -> 400 upstream)."""
    pattern = (pattern or "").strip()
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
        if r.pattern.lower() == pattern.lower():
            raise ValueError(
                f"a rule with pattern '{r.pattern}' already exists (id {r.id})")
    new_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM category_rules").fetchone()[0]
    con.execute("INSERT INTO category_rules VALUES (?,?,?,true)",
                [new_id, pattern, category])
    return Rule(new_id, pattern, category, True)


def delete_rule(con: duckdb.DuckDBPyConnection, rule_id: int) -> bool:
    """True if a row was deleted."""
    ensure_table(con)
    cur = con.execute(
        "DELETE FROM category_rules WHERE id = ?", [rule_id])
    return (cur.rowcount or 0) > 0


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
        for _id, desc in con.execute(
                "SELECT id, description FROM effective_transactions").fetchall():
            d = (desc or "").lower()
            for r in rules:
                if r.pattern.lower() in d:
                    matches[r.id] += 1
    return matches
