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


def test_validation_requires_three_of_five_positive_windows_forty_trades_and_positive_total_pnl():
    windows = [metrics([1] * 8), metrics([.5] * 8), metrics([.5] * 8), metrics([-.2] * 8), metrics([-.2] * 8)]
    check = assess_validation(aggregate_metrics(windows, 100), windows)
    assert check["validation_passed"]
    assert check["profitable_validation_windows"] == 3
    windows = [metrics([1] * 8), metrics([1] * 8), metrics([-.2] * 8), metrics([-.2] * 8), metrics([-.2] * 8)]
    check = assess_validation(aggregate_metrics(windows, 100), windows)
    assert check["validation_rejection_reasons"] == ["nestabilita"]
    windows = [metrics([1] * 8)] * 3
    check = assess_validation(aggregate_metrics(windows, 100), windows)
    assert check["validation_rejection_reasons"] == ["malo_obchodov", "nestabilita"]
    windows = [metrics([.1] * 8)] * 3 + [metrics([-1] * 8), metrics([-1] * 8, 16)]
    assert assess_validation(aggregate_metrics(windows, 100), windows)["validation_rejection_reasons"] == ["zaporny_pnl", "drawdown", "window_drawdown"]


@pytest.mark.parametrize("drawdown", ["missing", None, float("nan"), float("inf"), float("-inf")])
def test_validation_fails_closed_for_missing_or_nonfinite_window_drawdown(drawdown):
    windows = [metrics([1] * 8) for _ in range(5)]
    aggregate = aggregate_metrics(windows, 100)
    if drawdown == "missing":
        windows[4].pop("max_drawdown_percent")
    else:
        windows[4]["max_drawdown_percent"] = drawdown
    result = assess_validation(aggregate, windows)
    assert result["validation_passed"] is False
    assert "window_drawdown" in result["validation_rejection_reasons"]
    assert "invalid_metrics" in result["validation_rejection_reasons"]
    assert validation_rank({"walk_forward_metrics": aggregate, **result})[0] == float("-inf")


@pytest.mark.parametrize("scope", ["aggregate", "window"])
@pytest.mark.parametrize("key", ["closed_trades", "realized_profit", "max_drawdown_percent"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_validation_rejects_nonfinite_wf_metrics(scope, key, value):
    windows = [metrics([1] * 8) for _ in range(5)]
    aggregate = aggregate_metrics(windows, 100)
    (aggregate if scope == "aggregate" else windows[0])[key] = value
    result = assess_validation(aggregate, windows)
    assert result["validation_passed"] is False
    assert "invalid_metrics" in result["validation_rejection_reasons"]


def test_validation_accepts_exact_drawdown_budget_and_rejects_an_excess():
    windows = [metrics([1] * 8, dd=15) for _ in range(5)]
    aggregate = aggregate_metrics(windows, 100)
    assert assess_validation(aggregate, windows)["validation_passed"]
    windows[0]["max_drawdown_percent"] = 15.001
    assert "window_drawdown" in assess_validation(aggregate, windows)["validation_rejection_reasons"]


def test_all_twenty_rows_preserve_windows_and_never_touch_holdout_if_validation_fails():
    data = candles([100] * 600)
    holdout_start = data[480]["open_time"]
    pairs = ["UNI/USDT", "ZEC/USDT", "SUI/USDT", "PEPE/USDT", "NEAR/USDT"]

    def simulation(markets, config, **kwargs):
        start = kwargs.get("trading_start_time")
        assert start != holdout_start, "Failed validation must not read holdout"
        if start is None:
            return {"metrics": metrics([1] * 100)}
        # 40 trades but four losing windows: still ineligible, no hidden winner.
        positive = start == data[192]["open_time"]
        return {"metrics": metrics([1 if positive else -.1] * 8)}

    with patch("app.optimizer.simulate", side_effect=simulation):
        result = optimize({(pair, bar): data for pair in pairs for bar in ("15m", "5m")}, settings(), 2, locked_pairs=pairs)
    assert len(result["variant_results"]) == result["tested_combinations"] == 20
    assert len({row["variant_id"] for row in result["variant_results"]}) == 20
    assert result["winner"] is None
    for row in result["variant_results"]:
        assert [window["closed_trades"] for window in row["validation_windows"]] == [8, 8, 8, 8, 8]
        assert row["walk_forward_metrics"]["closed_trades"] == 40
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


def test_risk_adjusted_score_ranks_first_and_insufficient_sample_cannot_rank():
    def row(profits, score, dd=1):
        return {"validation_passed": True, "walk_forward_metrics": metrics(profits, dd), "risk_adjusted_oos_score": score}
    higher_score = row([.7] * 32 + [-1] * 8, 2.0)
    more_trades = row([1] * 60, 1.0)
    assert validation_rank(higher_score) > validation_rank(more_trades)
    assert validation_rank(row([1] * 60, 1.0)) > validation_rank(row([1] * 40, 1.0))
    assert validation_rank(row([1] * 40, 1.0, dd=1)) > validation_rank(row([1] * 40, 1.0, dd=5))
    short = row([100] * 8, 99.0)
    assert validation_rank(short)[0] == float("-inf")
    assert validation_rank({**higher_score, "validation_passed": False})[0] == float("-inf")
    assert payoff_note(higher_score["walk_forward_metrics"], 10, beats_hold=True) == "slaby_pomer"
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
        assert [window["window"] for window in windows] == ["wf1", "wf2", "wf3", "wf4", "wf5"]
        assert all(window["skip_rsi"] == 20 for window in windows)
        assert all(window["skip_rebound"] == 40 for window in windows)
        assert all(window["passed_entry"] == 8 for window in windows)


def test_diagnostic_rank_uses_policy_v4_gates():
    from app.optimizer import diagnostic_rank
    two_positive = {"walk_forward_metrics": {"closed_trades": 40, "realized_profit": 1, "max_drawdown_percent": 1},
                    "validation_windows": [{"realized_profit": 1}] * 2 + [{"realized_profit": -.1}] * 3}
    three_positive = {**two_positive, "validation_windows": [{"realized_profit": 1}] * 3 + [{"realized_profit": -.1}] * 2}
    assert diagnostic_rank(two_positive)[0] == 1
    assert diagnostic_rank(three_positive)[0] == 0
    short = {**three_positive, "walk_forward_metrics": {**three_positive["walk_forward_metrics"], "closed_trades": 25}}
    assert diagnostic_rank(short)[1] == 15
