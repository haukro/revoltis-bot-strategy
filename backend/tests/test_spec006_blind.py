from fastapi.testclient import TestClient

from app.main import app
from app.main import _blind_market_category, _blind_reconciliation_category, _blind_worker_category


def test_blind_worker_projection_has_only_coarse_age_category():
    assert _blind_worker_category([
        {"worker_name": "paper-outbox-worker", "last_success_age_category": "FRESH", "last_error_code": "SECRET"},
        {"worker_name": "paper-reconciliation-worker", "last_success_age_category": "LATE"},
    ]) == "LATE"
    assert _blind_worker_category([
        {"worker_name": "secret", "last_success_age_category": "NEVER"},
    ]) == "STALE"


def test_blind_reconciliation_projection_is_category_only():
    assert _blind_reconciliation_category({
        "latest": {"status": "PASSED", "audit_integrity_passed": True},
        "open_issue_counts": [],
    }) == "PASS"
    assert _blind_reconciliation_category({
        "latest": {"status": "WARNING", "audit_integrity_passed": True},
        "open_issue_counts": [{"severity": "WARNING", "count": 999, "check_code": "B_SECRET"}],
    }) == "WARNING"
    assert _blind_reconciliation_category({
        "latest": {"status": "FAILED", "audit_integrity_passed": False},
        "open_issue_counts": [{"severity": "CRITICAL", "count": 1}],
    }) == "CRITICAL"


def test_blind_market_projection_hides_counts():
    assert _blind_market_category({"HEALTHY": 10, "STALE": 0, "DEGRADED": 0, "HALTED": 0}) == "HEALTHY"
    assert _blind_market_category({"HEALTHY": 9, "STALE": 1, "DEGRADED": 0, "HALTED": 0}) == "DEGRADED"



def test_spec006_smoke_is_safe_without_preview_supabase(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/paper/spec-006/smoke")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["runtime_b_enabled"] is False
    assert payload["blind_safe"] is True
    assert payload["live_trading"] is False



def test_locked_regression_smoke_endpoints_stay_green(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with TestClient(app) as client:
        spec004 = client.get("/api/paper/spec-004/smoke")
        spec005 = client.get("/api/paper/spec-005/smoke")
        frozen_b = client.get("/api/research/tsmom-b-v1/smoke")

    assert spec004.status_code == 200
    assert spec004.json()["status"] == "passed"
    assert spec005.status_code == 200
    assert spec005.json()["status"] == "passed"
    assert frozen_b.status_code == 200
    payload = frozen_b.json()
    assert payload["status"] == "passed"
    assert len(payload["checks"]) == 11
    assert all(payload["checks"].values())
    assert payload["official_data_touched"] is False
