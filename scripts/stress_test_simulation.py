"""Deterministic stress matrix for the standalone simulation engine."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from random import Random


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("revoltis_simulation", ROOT / "backend/app/simulation.py")
simulation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(simulation)

PAIRS = ["PEPE", "DOGE", "WIF", "BONK", "SUI", "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "LINK", "XEC", "ZEC"]
TIMEFRAMES = ["1m", "3m", "5m", "15m"]
REGIMES = ["up", "down", "sideways", "shock"]
PROFILES = {
    "conservative": {"bb_period": 20, "bb_deviation": 2.2, "rsi_period": 14, "rsi_oversold": 30, "atr_period": 14, "atr_min_percent": .2, "atr_max_percent": 2, "min_volume_ratio": 1.2, "rebound_min_percent": .5, "rebound_max_percent": .8, "stop_loss_percent": 2.5, "trailing_start_percent": 1.5, "trailing_distance_percent": .4},
    "growing": {"bb_period": 20, "bb_deviation": 2, "rsi_period": 14, "rsi_oversold": 35, "atr_period": 14, "atr_min_percent": .15, "atr_max_percent": 4, "min_volume_ratio": .9, "rebound_min_percent": .3, "rebound_max_percent": .6, "stop_loss_percent": 4, "trailing_start_percent": 1.2, "trailing_distance_percent": .5},
    "risky": {"bb_period": 16, "bb_deviation": 1.7, "rsi_period": 10, "rsi_oversold": 40, "atr_period": 10, "atr_min_percent": .1, "atr_max_percent": 8, "min_volume_ratio": .6, "rebound_min_percent": .15, "rebound_max_percent": .4, "stop_loss_percent": 6, "trailing_start_percent": .8, "trailing_distance_percent": .35},
}


def make_candles(seed: int, regime: str, count: int = 600) -> list[dict]:
    rng, price, rows = Random(seed), 100.0, []
    for index in range(count):
        drift = {"up": .0005, "down": -.0005, "sideways": 0, "shock": 0}[regime]
        shock = (-.12 if index in (180, 420) else .08 if index in (200, 440) else 0) if regime == "shock" else 0
        move = drift + rng.gauss(0, .004) + shock
        opened = price
        price = max(.000001, price * (1 + move))
        high = max(opened, price) * (1 + abs(rng.gauss(0, .0015)))
        low = min(opened, price) * (1 - abs(rng.gauss(0, .0015)))
        rows.append({"open_time": 1_700_000_000_000 + index * 60_000, "close_time": 1_700_000_059_999 + index * 60_000, "open": opened, "high": high, "low": low, "close": price, "volume": 1_000_000 * (1 + abs(move) * 20)})
    return rows


def main() -> None:
    runs = trades = failures = 0
    max_drawdown = 0.0
    for pair_index, pair in enumerate(PAIRS):
        for timeframe_index, timeframe in enumerate(TIMEFRAMES):
            for regime_index, regime in enumerate(REGIMES):
                market = {f"{pair}/USDT": make_candles(pair_index * 100 + timeframe_index * 10 + regime_index, regime)}
                for profile in PROFILES.values():
                    config = {"initial_capital": 100.0, "stake_amount": 30.0, "max_open_trades": 2, "daily_trade_limit": 15, "min_quote_volume_usdt": 0} | profile
                    result = simulation.simulate(market, config, fee=.0015, force_close_at_end=True)
                    runs += 1
                    trades += result["metrics"]["closed_trades"]
                    max_drawdown = max(max_drawdown, result["metrics"]["max_drawdown_percent"])
                    reconciled = abs(result["metrics"]["portfolio_value"] - (100 + result["metrics"]["realized_profit"])) < .0011
                    finite = all(math.isfinite(float(value)) for value in result["metrics"].values())
                    audited = all({"entry_fee_usdt", "exit_fee_usdt", "holding_candles"} <= trade["raw"].keys() for trade in result["trades"])
                    if result["open_positions"] != 0 or not reconciled or not finite or not audited:
                        failures += 1
    print(json.dumps({"runs": runs, "pairs": len(PAIRS), "timeframes": len(TIMEFRAMES), "regimes": len(REGIMES), "profiles": len(PROFILES), "closed_trades": trades, "max_drawdown_seen": round(max_drawdown, 4), "invariant_failures": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
