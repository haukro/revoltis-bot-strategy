from unittest.mock import patch

import pytest

from app.optimizer import aggregate_metrics, assess_validation, optimize, payoff_note, validation_rank
from app.simulation import trade_statistics
from test_simulation import candles, settings


def metrics(profits, dd=1):
    stats = trade_statistics([{"status": "closed", "profit_usdt": profit} for profit in profits])
    return {**stats, "initial_capital": 100, "closed_trades": len(profits),
            "realized_profit": sum(profits), "max_drawdown_percent": dd,
            "win_rate": stats["winning_trades"] / len(profits) * 100 if profits else 0}


def test_net_statistics_include_losses_and_breakeven_without_counting_open_trades():
    trades = [{"status": "closed", "profit_usdt": value} for value in [3, 1, -2, 0]]
    stats = trade_statistics(trades + [{"status": "open", "profit_usdt": 999}])
    assert stats["avg_win"] == 2
    assert stats["avg_loss"] == 2
    assert stats["payoff"] == 1
    assert stats["expectancy"] == .5
    assert stats["breakeven_trades"] == 1
    assert trade_statistics([])["expectancy"] is None
    assert trade_statistics(trades[:2])["payoff"] is None


def test_window_aggregation_weights_individual_trades_not_average_of_averages():
    combined = aggregate_metrics([metrics([10, -2]), metrics([1] * 8 + [-1] * 2)], 100)
    assert combined["avg_win"] == 2
    assert combined["avg_loss"] == pytest.approx(4 / 3)
    assert combined["payoff"] == 1.5
    assert combined["expectancy"] == pytest.approx(14 / 12)


def test_validation_requires_two_positive_windows_and_positive_total_pnl():
    windows = [metrics([1] * 8), metrics([.5] * 8), metrics([-.2] * 8)]
    check = assess_validation(aggregate_metrics(windows, 100), windows)
    assert check["validation_passed"]
    assert check["profitable_validation_windows"] == 2
    windows = [metrics([1] * 8), metrics([-.2] * 8), metrics([-.2] * 8)]
    check = assess_validation(aggregate_metrics(windows, 100), windows)
    assert check["validation_rejection_reasons"] == ["nestabilita"]
    windows = [metrics([.1] * 8), metrics([.1] * 8), metrics([-1] * 8, 16)]
    assert assess_validation(aggregate_metrics(windows, 100), windows)["validation_rejection_reasons"] == ["zaporny_pnl", "drawdown"]


def test_all_twenty_rows_preserve_windows_and_never_touch_holdout_if_validation_fails():
    data = candles([100] * 600)
    holdout_start = data[480]["open_time"]
    pairs = ["UNI/USDT", "ZEC/USDT", "SUI/USDT", "PEPE/USDT", "NEAR/USDT"]

    def simulation(markets, config, **kwargs):
        start = kwargs.get("trading_start_time")
        assert start != holdout_start, "Failed validation must not read holdout"
        if start is None:
            return {"metrics": metrics([1] * 100)}
        # 24 trades but two losing windows: still ineligible, no hidden winner.
        positive = start == data[240]["open_time"]
        return {"metrics": metrics([1 if positive else -.1] * 8)}

    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({(pair, bar): data for pair in pairs for bar in ("15m", "5m")}, settings(), 2, locked_pairs=pairs)
    assert len(result["variant_results"]) == result["tested_combinations"] == 20
    assert len({row["variant_id"] for row in result["variant_results"]}) == 20
    assert result["winner"] is None
    for row in result["variant_results"]:
        assert [window["closed_trades"] for window in row["validation_windows"]] == [8, 8, 8]
        assert row["walk_forward_metrics"]["closed_trades"] == 24
        assert row["rejection_reasons"] == ["nestabilita"]
        assert row["holdout_metrics"] is None
        assert row["buy_hold_percent"] is None
        assert not row["holdout_evaluated"]


def test_eligible_nonfinalists_do_not_get_holdout_and_failed_finalist_has_no_replacement():
    data = candles([100] * 600)
    calls = []

    def simulation(markets, config, **kwargs):
        if kwargs.get("trading_start_time") == data[480]["open_time"]:
            calls.append(config["bb_period"])
            return {"metrics": metrics([1] * 3)}
        return {"metrics": metrics([2 if config["bb_period"] == 5 else 1] * 8)}

    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({("UNI/USDT", bar): data for bar in ("15m", "5m")}, settings(), 2, locked_pairs=["UNI/USDT"])
    assert calls == [5]
    evaluated = [row for row in result["variant_results"] if row["holdout_evaluated"]]
    assert len(evaluated) == 1
    assert "holdout_malo_obchodov" in evaluated[0]["rejection_reasons"]
    assert all(row["holdout_metrics"] is None for row in result["variant_results"] if not row["is_finalist"])
    assert result["winner"] is None


def test_expectancy_precedes_payoff_and_insufficient_sample_cannot_rank():
    low_payoff = {"validation_passed": True, "walk_forward_metrics": metrics([.7] * 16 + [-1] * 4)}
    high_payoff = {"validation_passed": True, "walk_forward_metrics": metrics([2] * 7 + [-1] * 13)}
    assert validation_rank(low_payoff) > validation_rank(high_payoff)
    short = {"validation_passed": True, "walk_forward_metrics": metrics([100] * 8)}
    assert validation_rank(short)[0] == float("-inf")
    assert payoff_note(low_payoff["walk_forward_metrics"], 10, beats_hold=True) == "slaby_pomer"
    assert payoff_note(short["walk_forward_metrics"], 20) is None


def test_optimizer_persists_entry_diagnostics_for_each_wf_window():
    data = candles([100] * 600)
    pairs = ["UNI/USDT"]

    call_number = 0

    def simulation(markets, config, **kwargs):
        nonlocal call_number
        call_number += 1
        start = kwargs.get("trading_start_time")
        result = {"metrics": metrics([1] * 8)}
        if start is not None:
            result["entry_diagnostics"] = {
                "n_bars": 100 + call_number,
                "skip_bollinger": 10,
                "skip_rsi": 20,
                "skip_reversal": 30,
                "skip_rebound": 40,
                "skip_atr": 50,
                "skip_volume": 60,
                "skip_other": 0,
                "passed_entry": 8,
            }
        return result

    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize(
            {("UNI/USDT", bar): data for bar in ("15m", "5m")},
            settings(),
            2,
            locked_pairs=pairs,
        )

    for row in result["variant_results"]:
        windows = row["entry_diagnostics_windows"]
        assert [window["window"] for window in windows] == ["wf1", "wf2", "wf3"]
        assert all(window["skip_rsi"] == 20 for window in windows)
        assert all(window["skip_rebound"] == 40 for window in windows)
        assert all(window["passed_entry"] == 8 for window in windows)
