"""Local LLM classification of unknown transaction categories.

Primary path: call a locally-served OpenAI-compatible chat endpoint
(works with Ollama, oMLX, LM Studio, llama.cpp server, vLLM, ...). The
endpoint, model, and optional API key are read from environment
variables so nothing is hard-wired:
  MERCURY_LLM_ENDPOINT  (default: Ollama on localhost:11434)
  MERCURY_LLM_MODEL     (default: llama3.2)
  MERCURY_LLM_API_KEY   (sent as "Authorization: Bearer ..." when set)

Fallback: if no local model is reachable, fall back to a lightweight
rule-based classifier so the feature still works offline.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import urllib.request
import urllib.error

# Standard category vocabulary: what the auto-classifier may return and
# what the rules panel may target.
STANDARD_CATEGORIES = [
    "Groceries", "Dining", "Transportation", "Housing", "Utilities",
    "Subscriptions", "Entertainment", "Shopping", "Travel", "Health",
    "Insurance", "Investing", "Income", "Fees", "Transfers",
    "Donations", "Education", "Other",
]

# Reserved category: a row marked "Ignored" (by a rule or by hand) is
# excluded from every aggregate (spend, income, net, accounts, periods)
# but stays visible in transaction lists. Ignoring is a human judgment,
# not a merchant guess, so the LLM never returns it; it is appended to
# the pickable list only (UI_CATEGORIES).
IGNORED_CATEGORY = "Ignored"

# What /api/standard-categories returns: every category a UI dropdown
# offers (manual assign, rule targets) plus the reserved "Ignored".
UI_CATEGORIES = STANDARD_CATEGORIES + [IGNORED_CATEGORY]

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

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("MERCURY_LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        endpoint, data=body, headers=headers, method="POST")
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
    ("Donations", ("donat", "charity", "charitable", "goodwill", "red cross",
                   "unicef", "world vision")),
    ("Education", ("tuition", "university", "college", "school", "regents",
                   "chegg", "udemy", "coursera", "pluralsight", "tutoring")),
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
