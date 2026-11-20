"""LLM config gate: BOTH endpoint and model required, no defaults (PR #43)."""

import pytest

from app import llm


@pytest.mark.parametrize(
    "endpoint,model,expected",
    [
        (None, None, False),
        ("http://localhost:11434/v1/chat/completions", None, False),
        (None, "llama3.2", False),
        ("http://localhost:11434/v1/chat/completions", "llama3.2", True),
        ("", "llama3.2", False),
        ("   ", "llama3.2", False),
        ("http://localhost:11434/v1/chat/completions", "   ", False),
    ],
)
def test_llm_configured_requires_both(monkeypatch, endpoint, model, expected):
    for name, value in (
        ("MERCURY_LLM_ENDPOINT", endpoint),
        ("MERCURY_LLM_MODEL", model),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert llm.llm_configured() is expected


def test_no_default_constants(monkeypatch):
    """The built-in localhost:11434 / llama3.2 fallbacks are gone."""
    for name in ("DEFAULT_ENDPOINT", "DEFAULT_MODEL"):
        assert not hasattr(llm, name), f"{name} should no longer exist"
    monkeypatch.delenv("MERCURY_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("MERCURY_LLM_MODEL", raising=False)
    assert llm.llm_endpoint() == ""
    assert llm.llm_model() == ""
