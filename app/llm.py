"""Local LLM classification of unknown transaction categories.

Primary path: call a locally-served OpenAI-compatible chat endpoint
(works with Ollama, oMLX, LM Studio, llama.cpp server, vLLM, ...). The
endpoint, model, and optional API key are read from environment
variables so nothing is hard-wired:
  MERCURY_LLM_ENDPOINT  (default: Ollama on localhost:11434)
  MERCURY_LLM_MODEL     (default: llama3.2)
  MERCURY_LLM_API_KEY   (sent as "Authorization: Bearer ..." when set)

Fallback: if the endpoint is configured but unreachable, fall back to a
lightweight rule-based classifier so the feature still works offline. If
no endpoint is configured at all (MERCURY_LLM_ENDPOINT unset), no
fallback runs: classify_batch_llm raises LLMNotConfigured and the UI
greys out the Classify button.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import urllib.request
import urllib.error

# Standard category vocabulary: what the auto-classifier may return and
# what the rules panel may target.
STANDARD_CATEGORIES = [
    "Groceries", "Dining", "Transportation", "Housing", "Utilities",
    "Subscriptions", "Entertainment", "Shopping", "Travel", "Health",
    "Beauty", "Insurance", "Investing", "Income", "Fees", "Transfers",
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

# Descriptions per LLM request. One request for ALL unknowns breaks at
# real scale: a 600-item prompt makes Qwen3-style models spend thousands
# of reasoning tokens before the JSON array, the combined output exceeds
# the server's generation budget, the array is truncated mid-list, and
# the whole run silently ends classified: 0. Small batches keep each
# response tiny and let one bad batch affect only itself.
BATCH_SIZE = 25

# Per-batch HTTP timeout: a thinking model can take a while on one batch
# even before it truncates.
REQUEST_TIMEOUT = 180

# Does the endpoint accept chat_template_kwargs? Probed on the first
# request; cached for the process lifetime so servers that 400 on
# unknown fields are not re-probed on every batch. None = not yet probed.
_template_kwargs_supported: Optional[bool] = None


class LLMNotConfigured(RuntimeError):
    """Raised when classify is requested but no LLM endpoint is configured."""


def llm_configured() -> bool:
    """True when the user explicitly configured an LLM endpoint.

    A set MERCURY_LLM_ENDPOINT is the signal that a model is meant to be
    used; the default endpoint/model values are just placeholders.
    """
    return bool(os.environ.get("MERCURY_LLM_ENDPOINT"))


def classify_batch_llm(descriptions: list[str]) -> list[Optional[str]]:
    """Classify a batch of descriptions via the local LLM.

    Returns a list the same length as `descriptions`; each entry is a
    standard category name or None if the model was unsure.

    Descriptions go to the endpoint in batches of BATCH_SIZE: a single
    request for hundreds of descriptions produces a response that
    thinking models truncate (reasoning tokens + a huge JSON array
    exceed the server's output budget), which used to silently zero the
    whole run. A failing batch falls back to the deterministic keyword
    rules on its own; the rest of the run continues.

    Raises LLMNotConfigured when no endpoint is configured (no silent
    keyword fallback in that case).
    """
    if not descriptions:
        return []
    if not llm_configured():
        raise LLMNotConfigured(
            "LLM is not configured — set MERCURY_LLM_ENDPOINT (and "
            "MERCURY_LLM_MODEL) and restart the server")
    results: list[Optional[str]] = []
    n_batches = (len(descriptions) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(n_batches):
        batch = descriptions[i * BATCH_SIZE:(i + 1) * BATCH_SIZE]
        try:
            batch_results = _call_local_llm(batch)
        except Exception as exc:  # noqa: BLE001
            print(f"  local LLM unavailable ({exc}); rule-based fallback "
                  f"for batch {i + 1}/{n_batches} ({len(batch)} descriptions)")
            batch_results = [rule_classify(d) for d in batch]
        else:
            print(f"  LLM batch {i + 1}/{n_batches} done ({len(batch)} descriptions)")
        results.extend(batch_results)
    return results


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

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("MERCURY_LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def post(payload: dict) -> dict:
        req = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise  # let the caller inspect .code
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach {endpoint}: {exc}") from exc

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    global _template_kwargs_supported
    if _template_kwargs_supported is not False:
        # Qwen3-style models answer with thousands of reasoning tokens
        # first, which eats the server's output budget and truncates the
        # JSON array. Asking the chat template to disable thinking
        # (supported by oMLX/mlx-lm) turns a ~40s batch into a ~2s one;
        # servers that reject the field get one 400-then-plain retry.
        try:
            data = post(dict(payload, chat_template_kwargs={
                "enable_thinking": False}))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 422):
                _template_kwargs_supported = False
                data = post(payload)
            else:
                raise
    else:
        data = post(payload)

    text = data["choices"][0]["message"]["content"]
    return _parse_json_array(text, len(descriptions))


def _parse_json_array(text: str, expected: int) -> list[Optional[str]]:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    candidate = text[start:end + 1] if (start != -1 and end > start) else text
    arr = None
    try:
        arr = json.loads(candidate)
    except json.JSONDecodeError:
        arr = None
    if isinstance(arr, list):
        out: list[Optional[str]] = []
        for i in range(expected):
            val = arr[i] if i < len(arr) else None
            if isinstance(val, str):
                val = _snap(val.strip().title())
            out.append(val)
        return out
    # Strict parse failed: truncated array (server output budget),
    # commentary, or markdown. Salvage the quoted strings in order — a
    # truncated response keeps its EARLIER answers intact, so this
    # recovers what a whole-batch [None] * expected would have thrown away.
    vals = re.findall(r'"([^"]*)"', text)
    out = []
    for i in range(expected):
        val = vals[i].strip().title() if i < len(vals) else None
        out.append(_snap(val))
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
    # Collapse whitespace runs: bank descriptions keep multiple spaces
    # (e.g. 'U.S. BANK        WEB PYMT'), so multi-word needles must
    # match the canonical form, not the raw text.
    desc = re.sub(r"\s+", " ", (description or "")).strip().lower()
    if not desc:
        return None
    for cat, needles in _RULES:
        if any(n in desc for n in needles):
            return cat
    return None
