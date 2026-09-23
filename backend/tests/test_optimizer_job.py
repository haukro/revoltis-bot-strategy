import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.main import OptimizerRequest, StrategySettings, combine_optimizer_lock_results, optimizer_jobs, run_optimizer_job


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


def test_combine_optimizer_lock_results_keeps_current_lock_and_all_coin_rows():
    results = [
        {
            "pair": "UNI/USDT",
            "timeframe": "5m",
            "qualified": False,
            "validation_passed": False,
            "walk_forward_metrics": {"closed_trades": 12, "realized_profit": -1.0, "max_drawdown_percent": 2.0},
            "tested_combinations": 6,
            "max_validation_trades": 12,
            "variant_results": [{"variant_id": "uni-v1", "rejection_reasons": ["malo_obchodov"]}],
            "per_coin_results": [{"pair": "UNI/USDT", "verdict": "NEDOSTATOK OBCHODOV"}],
            "pairs_ready": ["UNI-USDT"],
            "pairs_dropped": [],
            "replay_snapshots": {"uni": {"count": 1}},
            "cost_profiles": {"UNI/USDT": {"fee_rate": .001}},
            "source_job_id": "job-uni",
        },
        {
            "pair": "XRP/USDT",
            "timeframe": "15m",
            "qualified": False,
            "validation_passed": True,
            "walk_forward_metrics": {"closed_trades": 25, "realized_profit": 1.5, "expectancy": .06, "max_drawdown_percent": 1.0},
            "tested_combinations": 6,
            "max_validation_trades": 25,
            "variant_results": [{"variant_id": "xrp-v1", "rejection_reasons": ["non_positive_holdout_pnl"]}],
            "per_coin_results": [{"pair": "XRP/USDT", "verdict": "NEPREŠIEL"}],
            "pairs_ready": ["XRP-USDT"],
            "pairs_dropped": [],
            "replay_snapshots": {"xrp": {"count": 1}},
            "cost_profiles": {"XRP/USDT": {"fee_rate": .001}},
            "source_job_id": "job-xrp",
        },
    ]

    combined = combine_optimizer_lock_results(
        results,
        ["UNI/USDT", "XRP/USDT"],
        "version-123",
    )

    assert combined["version_id"] == "version-123"
    assert combined["locked_pairs"] == ["UNI/USDT", "XRP/USDT"]
    assert combined["tested_combinations"] == 12
    assert combined["max_validation_trades"] == 25
    assert combined["source_job_ids"] == ["job-xrp", "job-uni"]
    assert {row["pair"] for row in combined["per_coin_results"]} == {"UNI/USDT", "XRP/USDT"}
    assert {row["variant_id"] for row in combined["variant_results"]} == {"uni-v1", "xrp-v1"}
    assert combined["aggregate_lock_result"] is True
    assert combined["qualified"] is False
    assert combined["winner"] is None
    assert combined["strategy_code"] is None
    assert combined["job_verdict"] == "ŽIADNY PLATNÝ VARIANT"
