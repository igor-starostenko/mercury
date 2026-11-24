"""In-flight classify guard: a second concurrent run is rejected with
409, and the status endpoint reports the running state."""
import app.main as main
from app.main import _classify_lock, _classify_state

import pytest
from fastapi import HTTPException


def _set_in_flight(kind):
    _classify_state["running"] = True
    _classify_state["kind"] = kind
    assert _classify_lock.acquire(blocking=False)


def _clear():
    _classify_state["running"] = False
    _classify_state["kind"] = None
    _classify_lock.release()


def test_status_endpoint_reports_not_running_by_default():
    assert main.classify_status() == {"running": False, "kind": None}


def test_status_endpoint_reports_in_flight():
    _set_in_flight("category")
    try:
        assert main.classify_status() == {"running": True, "kind": "category"}
    finally:
        _clear()


def test_second_category_run_rejected_with_409(monkeypatch):
    monkeypatch.setenv("MERCURY_LLM_ENDPOINT", "http://localhost:1/v1")
    monkeypatch.setenv("MERCURY_LLM_MODEL", "test-model")
    _set_in_flight("category")
    try:
        with pytest.raises(HTTPException) as exc:
            main.classify()
        assert exc.value.status_code == 409
    finally:
        _clear()


def test_second_merchant_run_rejected_with_409():
    _set_in_flight("merchant")
    try:
        with pytest.raises(HTTPException) as exc:
            main.classify_merchants()
        assert exc.value.status_code == 409
    finally:
        _clear()
