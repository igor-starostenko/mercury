"""User-defined category rules: keyword in description -> category.

Rules are the user's bulk override mechanism. A rule matches when its
pattern occurs (case-insensitive substring) in a transaction's
description. Rules are evaluated in id order (creation order) and the
FIRST match wins.

Precedence, everywhere: any user assignment (rule, LLM, manual edit)
lives in the category_overrides table and beats the CSV-provided
category stored in the row itself; among user assignments the last
write wins. The original import in transactions.category is never
rewritten. "Apply" writes the winning rule's category as an override
for every matching row; new uploads get rule overrides at insert time.
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


def rule_stats(con: duckdb.DuckDBPyConnection,
               rules: list[Rule]) -> tuple[dict[int, int], int]:
    """Exact preview of an apply run.

    Returns (per-rule match counts, total rows whose category would
    change). Match counts are per-pattern (a row can match several
    patterns); the change total honours first-match precedence,
    compared against the effective (override-aware) category, and
    excludes soft-deleted rows.
    """
    matches: dict[int, int] = {r.id: 0 for r in rules}
    would_change = 0
    if rules:
        for _id, desc, cat in con.execute(
                "SELECT id, description, category "
                "FROM effective_transactions").fetchall():
            d = (desc or "").lower()
            final = None
            for r in rules:
                if r.pattern.lower() in d:
                    matches[r.id] += 1
                    if final is None:
                        final = r.category
            if final is not None and final != cat:
                would_change += 1
    return matches, would_change


def apply_rules(con: duckdb.DuckDBPyConnection,
                rules: list[Rule]) -> int:
    """Set the winning rule's category on every matching row.

    Categories are written to the override table, which takes
    precedence over the imported (CSV) category — the original import
    in transactions.category is never rewritten. Returns the number of
    rows whose effective category actually changed.
    """
    if not rules:
        return 0
    updates: list[tuple] = []
    for tid, desc, cat in con.execute(
            "SELECT id, description, category "
            "FROM effective_transactions").fetchall():
        final = match_category(desc, rules)
        if final is not None and final != cat:
            updates.append((tid, final))
    if updates:
        # One transaction: a mid-apply failure must not leave half the
        # rows re-categorized.
        con.execute("BEGIN TRANSACTION")
        try:
            con.executemany(
                "INSERT INTO category_overrides VALUES (?, ?, 'rule') "
                "ON CONFLICT (txn_id) DO UPDATE SET category = excluded.category, "
                "source = excluded.source", updates)
            con.executemany(
                "UPDATE transactions SET classified = true WHERE id = ?",
                [(tid,) for tid, _ in updates])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return len(updates)
