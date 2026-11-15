"""Local LLM classification of unknown transaction categories.

Primary path: call a locally-served OpenAI-compatible chat endpoint
(works with Ollama, LM Studio, llama.cpp server, vLLM, ...). The endpoint
and model are read from environment variables so nothing is hard-wired.

Fallback: if no local model is reachable, fall back to a lightweight
rule-based classifier so the feature still works offline.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import urllib.request
import urllib.error

# Standard category vocabulary the UI dropdown offers.
STANDARD_CATEGORIES = [
    "Groceries", "Dining", "Transportation", "Housing", "Utilities",
    "Subscriptions", "Entertainment", "Shopping", "Travel", "Health",
    "Insurance", "Investing", "Income", "Fees", "Transfers", "Other",
]

DEFAULT_ENDPOINT = os.environ.get("MERCURY_LLM_ENDPOINT", "http://localhost:11434/v1/chat/completions")
DEFAULT_MODEL = os.environ.get("MERCURY_LLM_MODEL", "llama3.2")


def classify_batch_llm(descriptions: list[str]) -> list[Optional[str]]:
    """Classify a batch of descriptions via the local LLM.

    Returns a list the same length as `descriptions`; each entry is a
    standard category name or None if the model was unsure.
    """
    if not descriptions:
        return []
    try:
        return _call_local_llm(descriptions)
    except Exception as exc:  # noqa: BLE001
        print(f"  local LLM unavailable ({exc}); using rule-based fallback")
        return [rule_classify(d) for d in descriptions]


def _call_local_llm(descriptions: list[str]) -> list[Optional[str]]:
    endpoint = os.environ.get("MERCURY_LLM_ENDPOINT", DEFAULT_ENDPOINT)
    model = os.environ.get("MERCURY_LLM_MODEL", DEFAULT_MODEL)

    numbered = "\n".join(f"{i}. {d}" for i, d in enumerate(descriptions))
    prompt = (
        "You are a personal-finance categorization assistant. "
        "Classify each transaction description into exactly one of these "
        "categories: " + ", ".join(STANDARD_CATEGORIES) + ".\n"
        "If a description is income (paycheck, interest, refund) use "
        "\"Income\". If you cannot decide, use \"Other\".\n"
        "Return ONLY a JSON array of " + str(len(descriptions)) + " strings, "
        "in the same order, no commentary.\n\n"
        "Descriptions:\n" + numbered
    )

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach {endpoint}: {exc}") from exc

    text = data["choices"][0]["message"]["content"]
    return _parse_json_array(text, len(descriptions))


def _parse_json_array(text: str, expected: int) -> list[Optional[str]]:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        return [None] * expected
    out: list[Optional[str]] = []
    for i in range(expected):
        val = arr[i] if i < len(arr) else None
        if isinstance(val, str):
            val = val.strip().title()
            # snap to the closest standard category
            val = _snap(val)
        out.append(val)
    return out


def _snap(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    for cat in STANDARD_CATEGORIES:
        if cat.lower() == name.lower():
            return cat
    return name or None


# ---------------------------------------------------------------------------
# Rule-based fallback (offline)
# ---------------------------------------------------------------------------
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Groceries", ("grocer", "trader", "whole foods", "costco", "safeway",
                   "kroger", "aldi", "target", "walmart", "meat", "dairy")),
    ("Dining", ("restaurant", "cafe", "coffee", "starbucks", "chipotle",
                "mcdonald", "doordash", "grubhub", "bar", "pizza", "sushi")),
    ("Transportation", ("uber", "lyft", "shell", "chevron", "exxon", "gas",
                        "transit", "metro", "parking", "toll", "taxi")),
    ("Subscriptions", ("netflix", "spotify", "apple.com", "icloud", "youtube",
                       "hulu", "disney", "subscription", "adobe", "notion")),
    ("Entertainment", ("movie", "cinema", "amc", "steam", "playstation",
                       "xbox", "concert", "ticket")),
    ("Health", ("pharmacy", "cvs", "walgreens", "doctor", "dental",
                "gym", "fitness", "clinic")),
    ("Utilities", ("electric", "water", "internet", "comcast", "verizon",
                   "att", "phone", "utility", "gas bill")),
    ("Travel", ("airline", "delta", "united", "airbnb", "hotel", "expedia",
                "rental car")),
    ("Fees", ("fee", "interest", "atm", "service charge")),
    ("Income", ("payroll", "paycheck", "deposit", "refund", "interest earned",
                "dividend", "salary")),
    ("Transfers", ("transfer", "internal", "from checking")),
]


def rule_classify(description: str) -> Optional[str]:
    desc = (description or "").lower()
    if not desc:
        return None
    for cat, needles in _RULES:
        if any(n in desc for n in needles):
            return cat
    return None
