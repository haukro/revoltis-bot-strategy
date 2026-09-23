from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "app" / "simulation.py"
SPEC = importlib.util.spec_from_file_location("app.simulation", MODULE_PATH)
simulation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(simulation)


def candles(prices: list[float], start: int = 1_700_000_000_000) -> list[dict]:
    rows = []
    for index, close in enumerate(prices):
        rows.append({
            "open_time": start + index * 60_000,
            "close_time": start + (index + 1) * 60_000 - 1,
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 1_000_000,
        })
    return rows


def settings(**overrides):
    base = {
        "initial_capital": 100.0, "stake_amount": 50.0, "max_open_trades": 1,
        "daily_trade_limit": 100, "bb_period": 5, "bb_deviation": 0.5,
        "rsi_period": 2, "rsi_oversold": 60, "atr_period": 2,
        "atr_min_percent": 0.01, "atr_max_percent": 20,
        "min_volume_ratio": 0.1, "min_quote_volume_usdt": 0,
        "rebound_min_percent": 0.01, "rebound_max_percent": 10,
        "stop_loss_percent": 50, "trailing_start_percent": 20,
        "trailing_distance_percent": 10,
    }
    return base | overrides


class SimulationAuditTests(unittest.TestCase):
    def test_warmup_prefix_cannot_open_validation_or_holdout_trades(self):
        data = candles([100.0] * 25 + [90.0, 91.0, 91.0, 91.0] + [100.0] * 30)
        baseline = simulation.simulate({"TEST/USDT": data}, settings(), force_close_at_end=True)
        self.assertGreater(baseline["metrics"]["closed_trades"], 0)
        result = simulation.simulate({"TEST/USDT": data}, settings(), force_close_at_end=True,
                                     trading_start_time=data[40]["open_time"])
        self.assertEqual(result["metrics"]["closed_trades"], 0)

    def test_entry_diagnostics_are_observational_and_complete(self):
        data = candles([100.0] * 25 + [90.0, 91.0, 91.0, 91.0] + [100.0] * 30)
        result = simulation.simulate({"TEST/USDT": data}, settings(), force_close_at_end=True)
        diagnostics = result["entry_diagnostics"]
        self.assertEqual(
            set(diagnostics),
            {
                "n_bars", "skip_bollinger", "skip_rsi", "skip_reversal",
                "skip_rebound", "skip_atr", "skip_volume", "skip_other",
                "passed_entry",
            },
        )
        self.assertGreater(diagnostics["n_bars"], 0)
        self.assertGreaterEqual(diagnostics["passed_entry"], 0)
        self.assertLessEqual(diagnostics["passed_entry"], diagnostics["n_bars"])

    def test_force_close_leaves_no_hidden_open_position(self):
        prices = [100.0] * 25 + [90.0, 91.0, 91.0, 91.0]
        result = simulation.simulate({"TEST/USDT": candles(prices)}, settings(), force_close_at_end=True)
        self.assertEqual(result["open_positions"], 0)
        self.assertTrue(all(trade["status"] == "closed" for trade in result["trades"]))
        self.assertTrue(all("entry_fee_usdt" in trade["raw"] for trade in result["trades"]))

    def test_selected_pairs_are_not_added_by_engine(self):
        result = simulation.simulate({"ONLY/USDT": candles([100.0] * 40)}, settings(), force_close_at_end=True)
        self.assertEqual(set(result["per_pair_metrics"]), {"ONLY/USDT"})

    def test_mark_to_market_drawdown_detects_open_loss(self):
        prices = [100.0] * 25 + [90.0, 91.0, 80.0, 75.0]
        result = simulation.simulate({"TEST/USDT": candles(prices)}, settings(), force_close_at_end=False)
        if result["open_positions"]:
            self.assertGreater(result["metrics"]["max_drawdown_percent"], 0)

    def test_forced_close_reconciles_portfolio_and_realized_profit(self):
        prices = [100.0] * 25 + [90.0, 91.0, 92.0, 93.0]
        result = simulation.simulate({"TEST/USDT": candles(prices)}, settings(), fee=0.0015, force_close_at_end=True)
        expected = result["metrics"]["initial_capital"] + result["metrics"]["realized_profit"]
        self.assertAlmostEqual(result["metrics"]["portfolio_value"], expected, places=3)
        self.assertEqual(result["metrics"]["unrealized_profit"], 0)

    def test_max_no_trail_hours_closes_only_when_trailing_never_activated(self):
        prices = [100.0] * 25 + [90.0, 91.0] + [91.0] * 20
        result = simulation.simulate(
            {"TEST/USDT": candles(prices)},
            settings(max_no_trail_hours=0.05),
            force_close_at_end=True,
        )
        timed = [trade for trade in result["trades"] if trade["exit_reason"] == "max_no_trail_hours"]
        self.assertEqual(len(timed), 1)
        self.assertGreaterEqual(timed[0]["raw"]["duration_min"], 3.0)

    def test_max_no_trail_hours_does_not_fire_after_trailing_activation(self):
        prices = [100.0] * 25 + [90.0, 91.0, 110.0] + [109.9] * 10
        result = simulation.simulate(
            {"TEST/USDT": candles(prices)},
            settings(
                max_no_trail_hours=0.05,
                trailing_start_percent=10,
                trailing_distance_percent=50,
            ),
            force_close_at_end=True,
        )
        self.assertFalse(any(trade["exit_reason"] == "max_no_trail_hours" for trade in result["trades"]))


if __name__ == "__main__":
    unittest.main()
