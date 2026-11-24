"""In-flight classify guard: a second concurrent run is rejected with
409, and /api/classify/status reports the running state."""
import app.main as main
import pytest
from fastapi.testclient import TestClient

from app.main import app, _classify_lock, _classify_state


@pytest.fixture
def client(monkeypatch):
    # Keep the DB out of this test; we never reach the endpoints.
    monkeypatch.setenv("MERCURY_LLM_ENDPOINT", "http://localhost:1/v1")
    monkeypatch.setenv("MERCURY_LLM_MODEL", "test-model")
    return TestClient(app)


def test_status_endpoint_reports_not_running():
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get("/api/classify/status")
        assert r.status_code == 200
        assert r.json() == {"running": False, "kind": None}


def test_second_run_rejected_with_409(client):
    # Simulate a run already in flight (holds the lock).
    assert _classify_lock.acquire(blocking=False)
    _classify_state["running"] = True
    _classify_state["kind"] = "category"
    try:
        r = client.post("/api/classify")
        assert r.status_code == 409
        r2 = client.get("/api/classify/status")
        assert r2.json() == {"running": True, "kind": "category"}
    finally:
        _classify_state["running"] = False
        _classify_state["kind"] = None
        _classify_lock.release()
