from unittest.mock import patch

import pytest

from app.optimizer import assess_candidate, optimize, walk_forward_windows, present_optimizer_record
from test_simulation import candles, settings


def metrics(trades=40, profit=8.0, dd=1.0):
    return {
        "initial_capital": 100, "closed_trades": trades, "realized_profit": profit,
        "max_drawdown_percent": dd, "win_rate": 60,
        "expectancy": profit / trades if trades else None, "payoff": 1.1,
    }


def candidate(**overrides):
    return {
        "pair": "NEAR/USDT", "settings": {"initial_capital": 100},
        "walk_forward_metrics": metrics(40, 8, 4),
        "holdout_metrics": metrics(10, 2, 5),
        "buy_hold_percent": 117.9581,
        "holdout_exposure_matched_bh_percent": 2,
        "validation_passed": True, **overrides,
    }


def test_raw_buy_and_hold_is_diagnostic_not_veto():
    result = assess_candidate(candidate(), ["NEAR/USDT"])
    assert result["qualified"]
    assert result["verdict"].startswith("QUALIFIED")


@pytest.mark.parametrize("change,reason", [
    ({"walk_forward_metrics": metrics(39, 8)}, "insufficient_validation_trades"),
    ({"holdout_metrics": metrics(9, 2)}, "insufficient_holdout_trades"),
    ({"holdout_metrics": metrics(10, 0)}, "non_positive_holdout_pnl"),
    ({"holdout_exposure_matched_bh_percent": 2.0001}, "underperformed_exposure_matched_benchmark"),
    ({"holdout_metrics": metrics(10, 2, 15.001)}, "drawdown_limit_exceeded"),
])
def test_policy_v4_gates(change, reason):
    result = assess_candidate(candidate(**change), ["NEAR/USDT"])
    assert not result["qualified"]
    assert reason in result["rejection_reasons"]


def test_expectancy_decay_gate():
    result = assess_candidate(candidate(
        walk_forward_metrics={**metrics(40, 8), "expectancy": .20},
        holdout_metrics={**metrics(10, 1), "expectancy": .09},
        holdout_exposure_matched_bh_percent=.5,
    ), ["NEAR/USDT"])
    assert not result["qualified"]
    assert "expectancy_decay" in result["rejection_reasons"]


def test_underpowered_only_is_unproven():
    result = assess_candidate(candidate(holdout_metrics=metrics(9, 2)), ["NEAR/USDT"])
    assert result["result_status"] == "unproven"
    assert result["verdict"].startswith("UNPROVEN")


def test_missing_or_other_lock_rejects():
    for lock in ([], ["BONK/USDT"]):
        assert "outside_locked_universe" in assess_candidate(candidate(), lock)["rejection_reasons"]


def fake_search(holdout_by_pair, validation_trades=8):
    data = candles([100] * 600)
    holdout_start = data[480]["open_time"]
    holdout_calls = []

    def simulation(markets, config, **kwargs):
        pair = next(iter(markets))
        start = kwargs.get("trading_start_time")
        if start == holdout_start:
            holdout_calls.append((pair, config["bb_period"]))
            return {"metrics": holdout_by_pair[pair], "trades": []}
        if start is None:
            return {"metrics": metrics(1000), "trades": []}
        return {"metrics": metrics(validation_trades, 9 if config["bb_period"] == 5 else 4), "trades": []}

    return data, simulation, holdout_calls


def test_failed_finalist_never_retries():
    data, simulation, calls = fake_search({"NEAR/USDT": metrics(4, 2.3419, 1.68)})
    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({("NEAR/USDT", bar): data for bar in ("15m", "5m")},
                          settings(), 5, locked_pairs=["NEAR/USDT"])
    assert len(calls) == 1
    assert result["winner"] is None
    assert result["strategy_code"] is None
    assert result["job_verdict"] == "UNPROVEN"
    assert result["max_validation_trades"] == 40
    assert result["holdout_metrics"]["closed_trades"] == 4


def test_no_relaxation_if_validation_samples_too_small():
    data, simulation, calls = fake_search({"NEAR/USDT": metrics(20)}, validation_trades=6)
    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({("NEAR/USDT", "15m"): data}, settings(), 5, locked_pairs=["NEAR/USDT"])
    assert result["max_validation_trades"] == 30
    assert result["winner"] is None
    assert len(calls) == 0
    assert result["holdout_metrics"] is None
    assert result["all_variants_insufficient_trades"]


def test_validation_windows_are_five_nonoverlapping_oos_slices():
    for size in (200, 600, 2880):
        data = candles([100] * size)
        windows, _ = walk_forward_windows(data)
        assert len(windows) == 5
        used = []
        for train, validation in windows:
            start = data[len(train)]["open_time"]
            used.extend(row["open_time"] for row in validation if row["open_time"] >= start)
        assert len(used) == len(set(used))
        assert max(used) < data[int(size * .8)]["open_time"]


def test_legacy_policy_is_not_grandfathered():
    old = {"result": {
        **candidate(), "qualified": True, "strategy_code": "legacy",
        "selection_policy_version": 3,
        "validation_windows": [metrics(8), metrics(7), metrics(5)],
    }}
    view = present_optimizer_record(old)["result"]
    assert not view["qualified"]
    assert view["winner"] is None
    assert view["strategy_code"] is None
    assert view["requires_retest"]
