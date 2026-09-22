"""Bounded, reproducible strategy search for the simulation application."""
from __future__ import annotations

from random import Random
from typing import Any, Callable

from .simulation import simulate


SEARCH_SPACE = {
    "bb_period": [14, 20, 26], "bb_deviation": [1.6, 2.0, 2.4],
    "rsi_period": [10, 14, 18], "rsi_oversold": [30, 35, 40],
    "atr_period": [10, 14, 20], "atr_min_percent": [0.08, 0.15, 0.25],
    "atr_max_percent": [2.0, 4.0, 7.0], "min_volume_ratio": [0.6, 0.9, 1.2],
    "min_quote_volume_usdt": [5000, 20000, 50000],
    "rebound_min_percent": [0.12, 0.25, 0.4], "rebound_max_percent": [0.45, 0.7, 1.0],
    "stop_loss_percent": [2.5, 4.0, 6.0], "trailing_start_percent": [0.7, 1.1, 1.6],
    "trailing_distance_percent": [0.3, 0.5, 0.8],
}


def candidates(base: dict[str, Any], count: int) -> list[dict[str, Any]]:
    rng = Random(42)
    result = [{key: base[key] for key in SEARCH_SPACE}]
    while len(result) < count:
        item = {key: rng.choice(values) for key, values in SEARCH_SPACE.items()}
        if item["rebound_max_percent"] <= item["rebound_min_percent"]:
            item["rebound_max_percent"] = min(1.0, item["rebound_min_percent"] + 0.35)
        if item not in result:
            result.append(item)
    return result


def score(metrics: dict[str, Any], capital: float, train_profit_pct: float) -> float:
    profit_pct = float(metrics["realized_profit"]) / capital * 100
    drawdown = float(metrics["max_drawdown_percent"])
    trades = int(metrics["closed_trades"])
    stability_penalty = abs(train_profit_pct - profit_pct) * 0.25
    low_trade_penalty = max(0, 3 - trades) * 1.5
    return round(profit_pct - 1.5 * drawdown - stability_penalty - low_trade_penalty + min(trades, 20) * 0.03, 5)


def generate_freqtrade_strategy(pair: str, timeframe: str, settings: dict[str, Any]) -> str:
    return f'''from freqtrade.strategy import IStrategy
import talib.abstract as ta
from technical import qtpylib

class RevoltisAIOptimized_v1(IStrategy):
    # Optimalizované pre {pair}, {timeframe}. Pred reálnym použitím vykonaj dry-run.
    timeframe = "{timeframe}"
    startup_candle_count = {max(int(settings['bb_period']), int(settings['rsi_period']), int(settings['atr_period'])) + 5}
    stoploss = -{float(settings['stop_loss_percent']) / 100:.4f}
    trailing_stop = True
    trailing_stop_positive = {float(settings['trailing_distance_percent']) / 100:.4f}
    trailing_stop_positive_offset = {float(settings['trailing_start_percent']) / 100:.4f}
    trailing_only_offset_is_reached = True
    minimal_roi = {{"0": 10.0}}

    def populate_indicators(self, dataframe, metadata):
        bands = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window={int(settings['bb_period'])}, stds={float(settings['bb_deviation'])})
        dataframe['bb_lower'] = bands['lower']
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod={int(settings['rsi_period'])})
        dataframe['atr_pct'] = ta.ATR(dataframe, timeperiod={int(settings['atr_period'])}) / dataframe['close'] * 100
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()
        dataframe['rebound_pct'] = (dataframe['close'] / dataframe['low'].shift(1) - 1) * 100
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[(dataframe['close'].shift(1) < dataframe['bb_lower']) &
          (dataframe['rsi'].shift(1) < {float(settings['rsi_oversold'])}) &
          (dataframe['close'] > dataframe['close'].shift(1)) & (dataframe['close'] > dataframe['open']) &
          (dataframe['rebound_pct'] >= {float(settings['rebound_min_percent'])}) &
          (dataframe['rebound_pct'] <= {float(settings['rebound_max_percent'])}) &
          (dataframe['atr_pct'] >= {float(settings['atr_min_percent'])}) &
          (dataframe['atr_pct'] <= {float(settings['atr_max_percent'])}) &
          (dataframe['volume'] >= dataframe['volume_mean'] * {float(settings['min_volume_ratio'])}) &
          (dataframe['volume'] * dataframe['close'] >= {float(settings['min_quote_volume_usdt'])}), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0
        return dataframe
'''


def optimize(candle_sets: dict[tuple[str, str], list[dict[str, Any]]], base: dict[str, Any], trials: int,
             progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    variants = candidates(base, trials)
    total = len(candle_sets) * len(variants)
    completed, results = 0, []
    capital = float(base["initial_capital"])
    for (pair, timeframe), candles in candle_sets.items():
        split = max(40, int(len(candles) * 0.7))
        train, validation = candles[:split], candles[max(0, split - 40):]
        for index, variant in enumerate(variants):
            settings = base | variant | {"selected_pairs": [pair], "timeframe": timeframe, "max_open_trades": 1}
            # 0.15% per side approximates exchange fee plus modest execution friction.
            train_result = simulate({pair: train}, settings, fee=0.0015)
            validation_result = simulate({pair: validation}, settings, fee=0.0015)
            train_metrics, validation_metrics = train_result["metrics"], validation_result["metrics"]
            train_profit_pct = float(train_metrics["realized_profit"]) / capital * 100
            results.append({
                "pair": pair, "timeframe": timeframe, "variant": index + 1, "settings": settings,
                "train_metrics": train_metrics, "validation_metrics": validation_metrics,
                "score": score(validation_metrics, capital, train_profit_pct),
            })
            completed += 1
            if progress:
                progress(completed, total, f"{pair} · {timeframe}")
    results.sort(key=lambda row: row["score"], reverse=True)
    best = results[0]
    best["strategy_code"] = generate_freqtrade_strategy(best["pair"], best["timeframe"], best["settings"])
    best["tested_combinations"] = total
    best["method"] = "70 % tréning / 30 % nezávislé overenie"
    best["cost_model"] = "0,15 % na každej strane obchodu (poplatok + rezerva na vykonanie)"
    best["top_results"] = [{k: row[k] for k in ("pair", "timeframe", "variant", "score", "validation_metrics")} for row in results[:5]]
    return best
