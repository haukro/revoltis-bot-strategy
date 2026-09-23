from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import DEFAULT, app


PAIRS = ["UNI/USDT", "ZEC/USDT", "SUI/USDT", "PEPE/USDT", "NEAR/USDT"]


def version_record(version_id="locked", hours=12):
    return {
        "id": version_id, "created_at": datetime.now(UTC).isoformat(),
        "settings": {**DEFAULT.model_dump(), "selected_pairs": PAIRS, "stake_amount": 35,
                     "_universe": {"pairs": PAIRS, "lock": {
                         "expires_at": (datetime.now(UTC) + timedelta(hours=hours)).isoformat()}}},
    }


def storage(tables):
    async def get(table, query=""):
        return deepcopy(tables.get(table, []))

    async def save(table, record):
        tables.setdefault(table, []).insert(0, deepcopy(record))
        return deepcopy(record)

    return patch("app.main.supabase_get", new=AsyncMock(side_effect=get)), patch(
        "app.main.supabase_upsert", new=AsyncMock(side_effect=save))


def test_apply_then_reload_uses_version_even_with_stale_default_settings():
    tables = {"strategy_settings": [{"settings": DEFAULT.model_dump(), "updated_at": "2020-01-01"}]}
    get, save = storage(tables)
    version = version_record()
    with get, save, TestClient(app) as client:
        applied = client.post("/api/strategy-versions", json={
            "name": "OKX test", "settings": version["settings"],
            "universe": version["settings"]["_universe"],
        })
        assert applied.status_code == 200
        # No second PUT /strategy. This reproduces the old stale settings row.
        for url in ["/api/dashboard", "/api/strategy-context"]:
            reloaded = client.get(url).json()
            assert reloaded["strategy"]["selected_pairs"] == PAIRS
            assert reloaded["strategy"]["stake_amount"] == 35
            assert reloaded["active_universe"] == applied.json()["active_universe"]
        assert len(tables["strategy_versions"]) == 1
        assert tables["strategy_settings"][0]["settings"]["selected_pairs"] != PAIRS


def test_new_parameter_edits_survive_reload_but_outside_pairs_are_rejected():
    tables = {"strategy_versions": [version_record()]}
    get, save = storage(tables)
    with get, save, TestClient(app) as client:
        config = client.get("/api/strategy-context").json()["strategy"]
        assert client.put("/api/strategy", json={**config, "stake_amount": 42}).status_code == 200
        assert client.get("/api/dashboard").json()["strategy"]["stake_amount"] == 42
        assert client.put("/api/strategy", json=DEFAULT.model_dump()).status_code == 409


def test_expired_latest_version_never_resurrects_an_older_active_lock():
    tables = {"strategy_versions": [version_record("expired", -1), version_record("old", 12)]}
    get, save = storage(tables)
    with get, save, TestClient(app) as client:
        context = client.get("/api/strategy-context").json()
        assert context["active_universe"] is None
        assert context["strategy"]["selected_pairs"] == PAIRS
        assert client.put("/api/strategy", json=DEFAULT.model_dump()).status_code == 200
        assert client.get("/api/dashboard").json()["strategy"]["selected_pairs"] == DEFAULT.selected_pairs


def test_mismatched_proposal_is_not_saved():
    tables = {}
    get, save = storage(tables)
    with get, save, TestClient(app) as client:
        response = client.post("/api/strategy-versions", json={
            "name": "Bad proposal", "settings": DEFAULT.model_dump(),
            "universe": version_record()["settings"]["_universe"],
        })
        assert response.status_code == 422
        assert not tables


def test_version_change_stops_optimizer_before_market_download(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    tables = {"strategy_versions": [version_record("new-version")]}
    get, save = storage(tables)
    with get, save, patch("app.main.load_okx_candles", new=AsyncMock()) as download, TestClient(app) as client:
        response = client.post("/api/optimizer/run", json={
            "version_id": "previous-version", "settings": DEFAULT.model_dump(), "pairs": ["NEAR/USDT"],
        })
        assert response.json()["status"] == "failed"
        assert "verzia sa zmenila" in response.json()["message"]
        download.assert_not_called()
