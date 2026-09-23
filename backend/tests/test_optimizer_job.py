import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.main import OptimizerRequest, StrategySettings, optimizer_jobs, run_optimizer_job


def test_job_rejects_missing_lock_before_fetching_any_candles():
    request = OptimizerRequest(settings=StrategySettings(), pairs=["NEAR/USDT"])
    optimizer_jobs["test-no-lock"] = {"created_at": "test"}
    with patch("app.main.supabase_get", new=AsyncMock(return_value=[])), \
         patch("app.main.load_okx_candles", new=AsyncMock()) as fetch:
        asyncio.run(run_optimizer_job("test-no-lock", request))
    assert optimizer_jobs["test-no-lock"]["status"] == "failed"
    assert "zamknutý" in optimizer_jobs["test-no-lock"]["message"]
    fetch.assert_not_called()


def test_job_rejects_outside_coin_instead_of_silently_filtering_request():
    versions = [{"settings": {"_universe": {"pairs": ["NEAR/USDT"], "lock": {
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}}}}]
    request = OptimizerRequest(settings=StrategySettings(), pairs=["NEAR/USDT", "BONK/USDT"])
    optimizer_jobs["test-other-coin"] = {"created_at": "test"}
    with patch("app.main.supabase_get", new=AsyncMock(return_value=versions)), \
         patch("app.main.load_okx_candles", new=AsyncMock()) as fetch:
        asyncio.run(run_optimizer_job("test-other-coin", request))
    assert optimizer_jobs["test-other-coin"]["status"] == "failed"
    fetch.assert_not_called()


def test_job_passes_the_server_lock_snapshot_to_optimizer_and_persists_null_winner():
    pairs = ["NEAR/USDT", "SUI/USDT"]
    versions = [{"settings": {"_universe": {"pairs": pairs, "lock": {
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}}}}]
    request = OptimizerRequest(settings=StrategySettings(timeframe="3m"), pairs=["NEAR/USDT"])
    optimizer_jobs["test-locked"] = {"created_at": "test"}

    async def fetch(pair, timeframe, count, *args):
        return [{}] * count

    result = {"winner": None, "qualified": False, "job_verdict": "ŽIADNY PLATNÝ VARIANT"}
    with patch("app.main.supabase_get", new=AsyncMock(return_value=versions)), \
         patch("app.main.supabase_upsert", new=AsyncMock()) as save, \
         patch("app.main.load_pair_cost_model", new=AsyncMock(return_value={"fee_rate": .001})), \
         patch("app.main.load_okx_candles", new=AsyncMock(side_effect=fetch)) as load, \
         patch("app.main.optimize", return_value=result) as optimize:
        asyncio.run(run_optimizer_job("test-locked", request))
    assert optimize.call_args.kwargs["locked_pairs"] == pairs
    assert [call.args[1] for call in load.call_args_list] == ["15m", "5m"]
    assert optimizer_jobs["test-locked"]["result"]["winner"] is None
    assert save.call_args.args[1]["result"]["winner"] is None
