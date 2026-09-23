"""Bounded, reproducible strategy search for the simulation application."""
from __future__ import annotations

from random import Random
from math import isfinite
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
MIN_HOLDOUT_TRADES = 10
MAX_DRAWDOWN_PERCENT = 15.0
SELECTION_POLICY_VERSION = 2


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


def walk_forward_windows(candles: list[dict[str, Any]], warmup: int = 40) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Keep the final 20% untouched and build three chronological expanding windows."""
    holdout_start = max(120, int(len(candles) * 0.8))
    development, holdout = candles[:holdout_start], candles[max(0, holdout_start - warmup):]
    boundaries = ((0.50, 2 / 3), (2 / 3, 5 / 6), (5 / 6, 1.0))
    windows = []
    for train_ratio, validation_ratio in boundaries:
        train_end = max(80, int(len(development) * train_ratio))
        validation_end = max(train_end + 1, int(len(development) * validation_ratio))
        train = development[:train_end]
        validation = development[max(0, train_end - warmup):min(len(development), validation_end)]
        windows.append((train, validation))
    return windows, holdout


def buy_and_hold_percent(candles: list[dict[str, Any]], cost_per_side: float) -> float:
    if len(candles) < 2:
        return 0.0
    first, last = float(candles[0]["close"]), float(candles[-1]["close"])
    return round(((last / first - 1) - 2 * cost_per_side) * 100, 4) if first else 0.0


def assess_candidate(row: dict[str, Any], locked_pairs: list[str]) -> dict[str, Any]:
    """All gates are mandatory. Training metrics never enter this verdict."""
    validation = row.get("walk_forward_metrics") or {}
    holdout = row.get("holdout_metrics") or {}
    validation_trades = int(validation.get("closed_trades", 0))
    holdout_trades = int(holdout.get("closed_trades", 0))
    capital = float(row.get("settings", {}).get("initial_capital", 0))
    profit = float(holdout.get("realized_profit", float("nan")))
    drawdown = float(holdout.get("max_drawdown_percent", float("nan")))
    benchmark = float(row.get("buy_hold_percent", float("nan")))
    profit_pct = profit / capital * 100 if capital > 0 else float("nan")
    reasons = []
    if validation_trades < MIN_VALIDATION_TRADES:
        reasons.append("insufficient_validation_trades")
    if holdout_trades < MIN_HOLDOUT_TRADES:
        reasons.append("insufficient_holdout_trades")
    if not all(isfinite(value) for value in (profit, drawdown, benchmark, profit_pct)):
        reasons.append("invalid_metrics")
    if not profit > 0:
        reasons.append("non_positive_holdout_pnl")
    if not profit_pct >= benchmark:
        reasons.append("underperformed_buy_and_hold")
    if not 0 <= drawdown <= MAX_DRAWDOWN_PERCENT:
        reasons.append("drawdown_limit_exceeded")
    if row.get("pair") not in locked_pairs:
        reasons.append("outside_locked_universe")
    if not row.get("validation_passed"):
        reasons.append("walk_forward_failed")
    if not reasons:
        status, verdict = "candidate", "KANDIDÁT — pokračovať iba do paper režimu"
    elif validation_trades < MIN_VALIDATION_TRADES or holdout_trades < MIN_HOLDOUT_TRADES:
        status, verdict = "insufficient_trades", "NEDOSTATOK OBCHODOV"
    elif "drawdown_limit_exceeded" in reasons:
        status, verdict = "drawdown_failed", "NEPREŠIEL — drawdown"
    elif "underperformed_buy_and_hold" in reasons:
        status, verdict = "hold_failed", "NEPREŠIEL — horšie ako hold"
    elif "non_positive_holdout_pnl" in reasons:
        status, verdict = "pnl_failed", "NEPREŠIEL — čistý zisk nie je kladný"
    else:
        status, verdict = "no_valid_variant", "ŽIADNY PLATNÝ VARIANT"
    return {"qualified": not reasons, "result_status": status, "verdict": verdict,
            "rejection_reasons": reasons, "holdout_profit_percent": round(profit_pct, 4) if isfinite(profit_pct) else None}


def present_optimizer_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Downgrade legacy stored results on read, without rewriting history."""
    if not record or not record.get("result"):
        return record
    previous = record["result"]
    if previous.get("selection_policy_version") == SELECTION_POLICY_VERSION:
        return record
    result = {**previous}
    capital = float((result.get("settings") or {}).get("initial_capital", 100))
    result["walk_forward_metrics"] = aggregate_metrics(result.get("validation_windows") or [], capital)
    result["holdout_metrics"] = result.get("holdout_metrics") or result.get("validation_metrics") or {}
    result.update(assess_candidate(result, []))
    result.update({"qualified": False, "winner": None, "strategy_code": None,
                   "job_verdict": "ŽIADNY PLATNÝ VARIANT", "requires_retest": True})
    result["rejection_reasons"].append("legacy_methodology")
    return {**record, "result": result}


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
             progress: Callable[[int, int, str], None] | None = None,
             *, locked_pairs: list[str] | None = None) -> dict[str, Any]:
    variants = candidates(base, trials)
    total = len(candle_sets) * len(variants)
    completed, results = 0, []
    capital = float(base["initial_capital"])
    locked_pairs = list(locked_pairs or [])
    if capital <= 0 or not candle_sets:
        raise ValueError("Chýba kapitál alebo dáta pre optimalizáciu.")
    # An identical prefix warms every variant. Its candles cannot create trades.
    warmup = max(40, *(int(v[key]) + 2 for v in variants for key in ("bb_period", "rsi_period", "atr_period")))
    for (pair, timeframe), candles in candle_sets.items():
        if len(candles) < 200:
            raise ValueError("Nedostatok sviečok pre tri validačné okná a holdout.")
        windows, _ = walk_forward_windows(candles, warmup)
        cost_per_side = COST_PER_SIDE.get(timeframe, 0.0018)
        for index, variant in enumerate(variants):
            settings = base | variant | {"selected_pairs": [pair], "timeframe": timeframe, "max_open_trades": 1}
            train_metrics, validation_window_metrics = [], []
            for train, validation in windows:
                train_metrics.append(simulate({pair: train}, settings, fee=cost_per_side, force_close_at_end=True)["metrics"])
                validation_start = int(candles[len(train)]["open_time"])
                validation_window_metrics.append(simulate(
                    {pair: validation}, settings, fee=cost_per_side, force_close_at_end=True,
                    trading_start_time=validation_start,
                )["metrics"])
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
                "train_metrics": train_metrics, "walk_forward_metrics": validation_metrics,
                "validation_metrics": validation_metrics, "validation_windows": validation_window_metrics,
                "cost_per_side": cost_per_side, "score": candidate_score, "validation_passed": validation_passed,
            })
            completed += 1
            if progress:
                progress(completed, total, f"{pair} · {timeframe}")

    # Freeze ONE finalist per coin using validation only, across both timeframes.
    # Holdout is a veto, never a second parameter search or a fallback loop.
    results.sort(key=lambda row: (row["validation_passed"], row["score"]), reverse=True)
    finalists: dict[str, dict[str, Any]] = {}
    for row in results:
        finalists.setdefault(row["pair"], row)
    for row in finalists.values():
        candles = candle_sets[(row["pair"], row["timeframe"])]
        _, holdout = walk_forward_windows(candles, warmup)
        boundary = max(120, int(len(candles) * 0.8))
        holdout_start = int(candles[boundary]["open_time"])
        row["holdout_metrics"] = simulate(
            {row["pair"]: holdout}, row["settings"], fee=row["cost_per_side"], force_close_at_end=True,
            trading_start_time=holdout_start,
        )["metrics"]
        # Benchmark and strategy cover exactly the same dates and initial capital.
        row["buy_hold_percent"] = buy_and_hold_percent(candles[boundary:], row["cost_per_side"])
        row["holdout_from_ms"] = holdout_start
        row["holdout_to_ms"] = int(candles[-1]["close_time"])
        row.update(assess_candidate(row, locked_pairs))
        row["strategy_code"] = generate_freqtrade_strategy(row["pair"], row["timeframe"], row["settings"]) if row["qualified"] else None

    qualified = [row for row in finalists.values() if row["qualified"]]
    selected = qualified[0] if qualified else next(iter(finalists.values()))
    summary_keys = ("pair", "timeframe", "variant", "score", "walk_forward_metrics", "holdout_metrics",
                    "buy_hold_percent", "holdout_profit_percent", "qualified", "result_status",
                    "verdict", "rejection_reasons")
    return {
        **selected,
        "winner": {**selected} if qualified else None,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "selection_method": "validation_only_per_coin_then_holdout_veto",
        "locked_pairs": locked_pairs,
        "tested_combinations": total,
        "max_validation_trades": max(int(row["walk_forward_metrics"]["closed_trades"]) for row in results),
        "minimum_validation_trades": MIN_VALIDATION_TRADES,
        "minimum_holdout_trades": MIN_HOLDOUT_TRADES,
        "max_drawdown_limit_percent": MAX_DRAWDOWN_PERCENT,
        "job_verdict": "KANDIDÁT" if qualified else "ŽIADNY PLATNÝ VARIANT",
        "method": "3× walk-forward + 20 % nedotknutý holdout",
        "cost_model": f"{selected['cost_per_side'] * 100:.2f} % na každej strane podľa intervalu (fee + spread/sklz)",
        "per_coin_results": [{key: row[key] for key in summary_keys} for row in finalists.values()],
        "top_results": [{key: row[key] for key in ("pair", "timeframe", "variant", "score", "validation_metrics")} for row in results[:5]],
    }
