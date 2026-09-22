"""Bounded, reproducible strategy search for the simulation application."""
from __future__ import annotations

from random import Random
from statistics import pstdev
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


PRIMARY_TIMEFRAMES = {"5m", "15m"}
COST_PER_SIDE = {"1m": 0.0022, "3m": 0.0018, "5m": 0.0015, "15m": 0.0013}
MIN_VALIDATION_TRADES = 20
MAX_DRAWDOWN_PERCENT = 10.0


def score(metrics: dict[str, Any], capital: float, stability_penalty: float) -> float:
    profit_pct = float(metrics["realized_profit"]) / capital * 100
    drawdown = float(metrics["max_drawdown_percent"])
    trades = int(metrics["closed_trades"])
    low_trade_penalty = max(0, MIN_VALIDATION_TRADES - trades) * 0.15
    return round(profit_pct - 1.5 * drawdown - stability_penalty - low_trade_penalty + min(trades, 40) * 0.02, 5)


def aggregate_metrics(items: list[dict[str, Any]], capital: float) -> dict[str, Any]:
    trades = sum(int(item["closed_trades"]) for item in items)
    wins = sum(int(round(int(item["closed_trades"]) * float(item["win_rate"]) / 100)) for item in items)
    profit = sum(float(item["realized_profit"]) for item in items)
    return {
        "initial_capital": capital,
        "portfolio_value": round(capital + profit, 4),
        "realized_profit": round(profit, 4),
        "unrealized_profit": 0,
        "closed_trades": trades,
        "win_rate": round(wins / trades * 100, 1) if trades else 0,
        "max_drawdown_percent": round(max((float(item["max_drawdown_percent"]) for item in items), default=0), 2),
    }


def walk_forward_windows(candles: list[dict[str, Any]]) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Keep the final 20% untouched and build three chronological expanding windows."""
    holdout_start = max(120, int(len(candles) * 0.8))
    development, holdout = candles[:holdout_start], candles[max(0, holdout_start - 40):]
    boundaries = ((0.50, 2 / 3), (2 / 3, 5 / 6), (5 / 6, 1.0))
    windows = []
    for train_ratio, validation_ratio in boundaries:
        train_end = max(80, int(len(development) * train_ratio))
        validation_end = max(train_end + 40, int(len(development) * validation_ratio))
        train = development[:train_end]
        validation = development[max(0, train_end - 40):min(len(development), validation_end)]
        windows.append((train, validation))
    return windows, holdout


def buy_and_hold_percent(candles: list[dict[str, Any]], cost_per_side: float) -> float:
    if len(candles) < 2:
        return 0.0
    first, last = float(candles[0]["close"]), float(candles[-1]["close"])
    return round(((last / first - 1) - 2 * cost_per_side) * 100, 4) if first else 0.0


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
        windows, holdout = walk_forward_windows(candles)
        cost_per_side = COST_PER_SIDE.get(timeframe, 0.0018)
        for index, variant in enumerate(variants):
            settings = base | variant | {"selected_pairs": [pair], "timeframe": timeframe, "max_open_trades": 1}
            train_metrics, validation_window_metrics = [], []
            for train, validation in windows:
                train_metrics.append(simulate({pair: train}, settings, fee=cost_per_side, force_close_at_end=True)["metrics"])
                validation_window_metrics.append(simulate({pair: validation}, settings, fee=cost_per_side, force_close_at_end=True)["metrics"])
            validation_metrics = aggregate_metrics(validation_window_metrics, capital)
            window_profit_pcts = [float(item["realized_profit"]) / capital * 100 for item in validation_window_metrics]
            stability = pstdev(window_profit_pcts) if len(window_profit_pcts) > 1 else 0.0
            candidate_score = score(validation_metrics, capital, stability)
            validation_passed = (
                timeframe in PRIMARY_TIMEFRAMES
                and int(validation_metrics["closed_trades"]) >= MIN_VALIDATION_TRADES
                and all(float(item["realized_profit"]) > 0 for item in validation_window_metrics)
                and float(validation_metrics["max_drawdown_percent"]) <= MAX_DRAWDOWN_PERCENT
                and candidate_score > 0
            )
            results.append({
                "pair": pair, "timeframe": timeframe, "variant": index + 1, "settings": settings,
                "train_metrics": train_metrics, "validation_metrics": validation_metrics,
                "validation_windows": validation_window_metrics, "holdout_candles": holdout,
                "cost_per_side": cost_per_side, "score": candidate_score, "validation_passed": validation_passed,
            })
            completed += 1
            if progress:
                progress(completed, total, f"{pair} · {timeframe}")
    results.sort(key=lambda row: (row["validation_passed"], row["score"]), reverse=True)
    best = results[0]
    holdout_result = simulate({best["pair"]: best.pop("holdout_candles")}, best["settings"], fee=best["cost_per_side"], force_close_at_end=True)
    holdout_metrics = holdout_result["metrics"]
    holdout_profit_pct = float(holdout_metrics["realized_profit"]) / capital * 100
    holdout_market = walk_forward_windows(candle_sets[(best["pair"], best["timeframe"])])[1]
    hold_return = buy_and_hold_percent(holdout_market, best["cost_per_side"])
    best["holdout_metrics"] = holdout_metrics
    best["validation_metrics"] = holdout_metrics
    best["buy_hold_percent"] = hold_return
    best["qualified"] = bool(
        best["validation_passed"]
        and int(holdout_metrics["closed_trades"]) >= 3
        and holdout_profit_pct >= hold_return
        and float(holdout_metrics["max_drawdown_percent"]) <= MAX_DRAWDOWN_PERCENT
    )
    best["strategy_code"] = generate_freqtrade_strategy(best["pair"], best["timeframe"], best["settings"])
    best["tested_combinations"] = total
    best["method"] = "3× walk-forward + 20 % nedotknutý holdout"
    best["cost_model"] = f"{best['cost_per_side'] * 100:.2f} % na každej strane podľa intervalu (fee + spread/sklz)"
    best["verdict"] = "prešiel holdoutom – pokračovať iba do paper režimu" if best["qualified"] else "holdout nepotvrdil výhodu – nepoužiť na live obchodovanie"
    best["top_results"] = [{k: row[k] for k in ("pair", "timeframe", "variant", "score", "validation_metrics")} for row in results[:5]]
    best.pop("holdout_candles", None)
    return best
