import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.paper_measurement import MeasurementError, STEP_MS, candle_window, hash_json
from app.paper_routes import PaperConfig, make_paper_router
from app.paper_source import PaperMarket
from app.paper_worker import PaperWorker
from test_paper_measurement import START, profile, source_candles, test_config

COMMIT = "a" * 40


class Market:
    def __init__(self, problem=None):
        self.problem, self.cost_calls = problem, 0

    async def candles(self, pair, window):
        rows = source_candles([100.] * window.expected_count, window.first_open_ms)
        return rows[:-1] if self.problem == "gap" else rows

    async def cost(self, pair, notional):
        self.cost_calls += 1
        if self.problem == "cost":
            raise MeasurementError("cost_profile_unavailable")
        age = {"old": 60001, "future": -1, "limit": 60000}.get(self.problem, 0)
        return profile(START), START + age


def build(problem=None, commit=COMMIT, config=None):
    rpc, market = AsyncMock(), Market(problem)
    worker = PaperWorker(rpc, market, code_commit=commit)
    result = asyncio.run(worker.build(config or test_config(), START + 5 * STEP_MS))
    return result, rpc, market


def test_worker_zero_trade_result_is_proven_empty_and_uses_one_cost_profile():
    (run, trades, positions), rpc, market = build()
    assert run["status"] == "valid" and trades == positions == []
    assert run["trade_count"] == run["open_count"] == 0
    assert run["tape_sha256"] == hash_json([]) and market.cost_calls == 1
    assert rpc.call.await_args.args == ("paper_archive_candles",)
    assert run["raw"]["python"] and run["raw"]["dependencies"]["httpx"]


@pytest.mark.parametrize("problem,commit,reason", [
    (None, None, "unknown_code_version"), ("cost", COMMIT, "cost_profile_unavailable"),
    ("gap", COMMIT, "incomplete_candles"), ("old", COMMIT, "book_too_old"),
    ("future", COMMIT, "book_from_future"),
])
def test_at3_at11_bad_inputs_preserve_missing_tape(problem, commit, reason):
    (run, trades, positions), _, market = build(problem, commit)
    assert run["status"] == "invalid" and run["invalid_reason"] == reason
    assert trades is positions is None and run["trade_count"] is run["open_count"] is None
    assert market.cost_calls == 1


def test_at3_changed_settings_fail_without_simulation():
    test = test_config()
    test["settings"]["stake_amount"] = 99
    (run, trades, _), _, _ = build(config=test)
    assert run["invalid_reason"] == "settings_mismatch" and trades is None


def test_book_age_inclusive_limit():
    assert build("limit")[0][0]["status"] == "valid"


def test_open_position_count_is_not_a_complete_position_tape():
    with patch("app.paper_worker.simulate", return_value={"trades": [], "metrics": {}, "open_positions": 1}):
        (run, trades, positions), _, _ = build()
    assert run["invalid_reason"] == "missing_open_positions"
    assert trades == [] and positions is None and run["open_count"] is None
    assert run["raw"]["engine_open_count"] == 1


def test_at35_worker_passes_db_horizon_to_build_but_never_sends_it_to_persist():
    test = {**test_config(), "id": str(uuid4()), "pending_run_ids": [], "shadow": False, "lease_ms": 120000}
    claim = {"id": str(uuid4()), "lease_token": str(uuid4()), "test_end_ms": START + STEP_MS}
    async def call(name, **kw):
        if name == "paper_claim_run":
            return {"outcome": "claimed", "claim": claim}
        if name == "paper_persist_run":
            assert "test_end_ms" not in kw["p_run"] and "as_of_ms" not in kw["p_run"]
            return {"outcome": "run_created", "run": {"id": "r", "test_end_ms": claim["test_end_ms"], "status": "valid", "invalid_reason": None}}
        raise AssertionError(name)
    rpc = AsyncMock(); rpc.call.side_effect = call
    market = AsyncMock(); market.latest.return_value = START
    worker = PaperWorker(rpc, market, code_commit=COMMIT)
    worker.build = AsyncMock(return_value=({"test_end_ms": claim["test_end_ms"]}, [], []))
    assert asyncio.run(worker._test(test, str(uuid4())))["outcome"] == "run_created"
    worker.build.assert_awaited_once_with(test, claim["test_end_ms"])


def test_pending_origin_blocks_claim_and_market_io():
    rpc, market = AsyncMock(), AsyncMock()
    rpc.call.return_value = {"outcome": "evaluation_pending"}
    worker = PaperWorker(rpc, market, code_commit=COMMIT)
    worker.assess = AsyncMock(return_value={"outcome": "origin_pending"})
    result = asyncio.run(worker._test({"id": "test", "pending_run_ids": ["earlier", "later"]}, "tick"))
    assert result["outcome"] == "evaluation_pending"
    worker.assess.assert_awaited_once_with("earlier")
    market.latest.assert_not_awaited()
    assert rpc.call.await_args.args == ("paper_worker_outcome",)


def test_invalid_tick_does_not_read_market_or_create_claim():
    rpc, market = AsyncMock(), AsyncMock()
    rpc.call.return_value = {"outcome": "invalid_tick"}
    worker = PaperWorker(rpc, market, code_commit=COMMIT)
    assert asyncio.run(worker.tick(str(uuid4())))["outcome"] == "invalid_tick"
    market.latest.assert_not_awaited()


def test_timeout_records_error_and_never_resumes_persist():
    test = {**test_config(), "id": "test", "pending_run_ids": [], "shadow": False, "lease_ms": 120000}
    claim = {"id": "claim", "lease_token": "token", "test_end_ms": START+STEP_MS}
    calls = []
    async def call(name, **kw):
        calls.append((name, kw))
        if name == "paper_worker_page":
            return {"outcome": "ready", "tests": [test]}
        if name == "paper_claim_run":
            return {"outcome": "claimed", "claim": claim}
        if name == "paper_worker_outcome":
            return {"outcome": "error"}
        raise AssertionError(name)
    async def never_complete(*args):
        await asyncio.Event().wait()
    rpc, market = AsyncMock(), AsyncMock()
    rpc.call.side_effect = call
    market.latest.return_value = START
    worker = PaperWorker(rpc, market, code_commit=COMMIT, timeout_seconds=.02)
    worker.build = never_complete
    with pytest.raises(TimeoutError):
        asyncio.run(worker.tick("tick"))
    assert calls[-1][0] == "paper_worker_outcome"
    assert calls[-1][1]["p_reason"] == "worker_timeout"
    assert calls[-1][1]["p_lease_token"] == "token"
    assert not any(name == "paper_persist_run" for name, _ in calls)


def test_unverifiable_candidate_closes_without_later_market_work():
    rpc, market = AsyncMock(), AsyncMock()
    rpc.call.return_value = {"outcome": "test_closed"}
    worker = PaperWorker(rpc, market, code_commit=COMMIT)
    worker.assess = AsyncMock(return_value={"outcome": "assessed", "verdict": {"verdict": "INVALID"}})
    assert asyncio.run(worker._test({"id": "test", "pending_run_ids": ["earlier", "later"]}, "tick"))["outcome"] == "test_closed"
    worker.assess.assert_awaited_once_with("earlier")
    market.latest.assert_not_awaited()


@pytest.fixture
def endpoint():
    worker = AsyncMock()
    worker.tick.return_value = {"outcome": "processed"}
    worker.diagnostic.return_value = {"outcome": "diagnostic_created"}
    @asynccontextmanager
    async def factory(config):
        yield worker
    app = FastAPI()
    app.include_router(make_paper_router(lambda: PaperConfig("cron-secret", "diagnostic-secret", "", "", COMMIT), factory))
    with TestClient(app) as client:
        yield client, worker


@pytest.mark.parametrize("path,body", [("/measure/tick", {"tick_id": str(uuid4())}),
                                     ("/diagnostic/run", {"test_id": str(uuid4())})])
def test_at9_missing_or_wrong_secret_does_not_invoke_worker(endpoint, path, body):
    client, worker = endpoint
    for headers in ({}, {"Authorization": "Bearer incorrect"}):
        assert client.post("/api/paper" + path, json=body, headers=headers).status_code == 401
    worker.tick.assert_not_awaited(); worker.diagnostic.assert_not_awaited()


def test_automatic_secret_cannot_run_diagnostic(endpoint):
    client, worker = endpoint
    r = client.post("/api/paper/diagnostic/run", json={"test_id": str(uuid4())}, headers={"Authorization": "Bearer cron-secret"})
    assert r.status_code == 401
    worker.diagnostic.assert_not_awaited()


def test_at35_http_rejects_worker_horizon_and_caller_settings(endpoint):
    client, worker = endpoint
    r = client.post("/api/paper/measure/tick", json={"tick_id": str(uuid4()), "test_end_ms": 123},
                    headers={"Authorization": "Bearer cron-secret"})
    assert r.status_code == 422
    worker.tick.assert_not_awaited()


def test_endpoint_accepts_authorized_tick(endpoint):
    client, worker = endpoint
    tick_id = str(uuid4())
    assert client.post("/api/paper/measure/tick", json={"tick_id": tick_id},
        headers={"Authorization": "Bearer cron-secret"}).json() == {"outcome": "processed"}
    worker.tick.assert_awaited_once_with(tick_id)


def test_feature_defaults_off_and_rejects_hosted_url(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.delenv("PAPER_MEASUREMENT_ENABLED", raising=False)
    with pytest.raises(HTTPException) as error:
        PaperConfig.environment()
    assert error.value.detail == "paper_measurement_disabled"
    monkeypatch.setenv("PAPER_MEASUREMENT_ENABLED", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("PAPER_SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(HTTPException) as error:
        PaperConfig.environment()
    assert error.value.detail == "paper_environment_not_authorized"


def test_market_preserves_confirmation_and_latest_closed_boundary():
    rows = [[str(START+STEP_MS), "100", "101", "99", "100", "10", "1000", "1000", "0"],
            [str(START), "100", "101", "99", "100", "10", "1000", "1000", "1"]]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "0", "data": rows}))) as c:
            return await PaperMarket(c).latest("TEST/USDT")
    assert asyncio.run(run()) == START
    assert PaperMarket._candle(rows[0])["confirm"] == 0


def test_market_pagination_keeps_duplicates_for_validation():
    row = [str(START), "100", "101", "99", "100", "10", "1000", "1000", "1"]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "0", "data": [row, row]}))) as c:
            return await PaperMarket(c).candles("TEST/USDT", candle_window(START, START+STEP_MS, 0))
    assert len(asyncio.run(run())) == 2
