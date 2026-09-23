from unittest.mock import patch

import pytest

from app.optimizer import assess_candidate, optimize, walk_forward_windows, present_optimizer_record
from test_simulation import candles, settings


def metrics(trades=20, profit=8.0, dd=1.0):
    return {"initial_capital": 100, "closed_trades": trades, "realized_profit": profit,
            "max_drawdown_percent": dd, "win_rate": 100}


def candidate(**overrides):
    return {"pair": "NEAR/USDT", "settings": {"initial_capital": 100},
            "walk_forward_metrics": metrics(20), "holdout_metrics": metrics(10, 8, 15),
            "buy_hold_percent": 8, "validation_passed": True, **overrides}


def test_exact_thresholds_pass_and_equal_hold_is_allowed():
    assert assess_candidate(candidate(), ["NEAR/USDT"])["qualified"]


@pytest.mark.parametrize("change,reason", [
    ({"walk_forward_metrics": metrics(19)}, "insufficient_validation_trades"),
    ({"holdout_metrics": metrics(9)}, "insufficient_holdout_trades"),
    ({"holdout_metrics": metrics(10, 0), "buy_hold_percent": -10}, "non_positive_holdout_pnl"),
    ({"holdout_metrics": metrics(10, -1), "buy_hold_percent": -10}, "non_positive_holdout_pnl"),
    ({"buy_hold_percent": 8.0001}, "underperformed_buy_and_hold"),
    ({"holdout_metrics": metrics(10, 8, 15.001)}, "drawdown_limit_exceeded"),
    ({"buy_hold_percent": float("nan")}, "invalid_metrics"),
    ({"validation_passed": False}, "walk_forward_failed"),
])
def test_each_gate_is_mandatory(change, reason):
    result = assess_candidate(candidate(**change), ["NEAR/USDT"])
    assert not result["qualified"]
    assert reason in result["rejection_reasons"]


def test_missing_or_other_lock_rejects():
    for lock in ([], ["BONK/USDT"]):
        assert "outside_locked_universe" in assess_candidate(candidate(), lock)["rejection_reasons"]


def test_training_trade_count_does_not_count_as_validation():
    result = assess_candidate(candidate(walk_forward_metrics=metrics(0), train_metrics=[metrics(1000)]), ["NEAR/USDT"])
    assert result["result_status"] == "insufficient_trades"


def fake_search(holdout_by_pair, validation_trades=8):
    data = candles([100] * 600)
    holdout_start = data[480]["open_time"]
    holdout_calls = []

    def simulation(markets, config, **kwargs):
        pair = next(iter(markets))
        start = kwargs.get("trading_start_time")
        if start == holdout_start:
            holdout_calls.append((pair, config["bb_period"]))
            return {"metrics": holdout_by_pair[pair]}
        if start is None:
            return {"metrics": metrics(1000)}
        # Variant 1 wins validation. Variant 2 must not become a holdout fallback.
        return {"metrics": metrics(validation_trades, 9 if config["bb_period"] == 5 else 4)}

    return data, simulation, holdout_calls


def test_rejected_finalist_never_retries_other_parameters_or_timeframe():
    data, simulation, calls = fake_search({"NEAR/USDT": metrics(4, 2.3419, 1.68)})
    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({("NEAR/USDT", bar): data for bar in ("15m", "5m")}, settings(), 5, locked_pairs=["NEAR/USDT"])
    assert len(calls) == 1
    assert calls == [("NEAR/USDT", 5)]
    assert result["winner"] is None
    assert result["strategy_code"] is None
    assert result["job_verdict"] == "ŽIADNY PLATNÝ VARIANT"
    assert result["result_status"] == "insufficient_trades"
    assert result["tested_combinations"] == 10
    assert result["max_validation_trades"] == 24
    assert result["walk_forward_metrics"]["closed_trades"] == 24
    assert result["holdout_metrics"]["closed_trades"] == 4


def test_winner_only_from_accepted_coins_and_uses_same_period_hold():
    data, simulation, calls = fake_search({"NEAR/USDT": metrics(4), "SUI/USDT": metrics(10)})
    # Warmup contains extreme returns: benchmark must exclude it.
    data[440]["close"] = 1
    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({(p, "15m"): data for p in ("NEAR/USDT", "SUI/USDT")}, settings(), 2,
                          locked_pairs=["NEAR/USDT", "SUI/USDT"])
    assert len(calls) == 2
    assert result["winner"]["pair"] == "SUI/USDT"
    assert result["qualified"]
    assert result["buy_hold_percent"] == -0.26
    assert len(result["per_coin_results"]) == 2


def test_no_relaxation_if_all_validation_samples_too_small():
    data, simulation, calls = fake_search({"NEAR/USDT": metrics(20)}, validation_trades=6)
    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({("NEAR/USDT", "15m"): data}, settings(), 5, locked_pairs=["NEAR/USDT"])
    assert result["max_validation_trades"] == 18
    assert result["winner"] is None
    assert result["tested_combinations"] == 5
    assert len(calls) == 1


def test_validation_windows_only_overlap_for_indicator_warmup():
    for size in (200, 600, 2880):
        data = candles([100] * size)
        windows, _ = walk_forward_windows(data)
        used = []
        for train, validation in windows:
            start = data[len(train)]["open_time"]
            used.extend(row["open_time"] for row in validation if row["open_time"] >= start)
        assert len(used) == len(set(used))
        assert max(used) < data[int(size * .8)]["open_time"]


def test_legacy_near_is_downgraded_on_read_without_mutating_saved_record():
    old = {"result": {**candidate(), "holdout_metrics": metrics(4, 2.3419, 1.68),
                      "buy_hold_percent": 70.8068, "qualified": True, "strategy_code": "legacy code",
                      "validation_windows": [metrics(8), metrics(7), metrics(5)]}}
    view = present_optimizer_record(old)["result"]
    assert view["verdict"] == "NEDOSTATOK OBCHODOV"
    assert view["winner"] is None
    assert view["strategy_code"] is None
    assert not view["qualified"]
    assert view["walk_forward_metrics"]["closed_trades"] == 20
    assert old["result"]["strategy_code"] == "legacy code"


def test_legacy_success_needs_retest_and_is_not_grandfathered_in():
    old = {"result": {**candidate(), "qualified": True,
                      "validation_windows": [metrics(8), metrics(7), metrics(5)]}}
    assert not present_optimizer_record(old)["result"]["qualified"]
