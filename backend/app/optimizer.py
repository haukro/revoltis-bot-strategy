"""Bounded, reproducible strategy search for the simulation application."""
from __future__ import annotations

from random import Random
from math import isfinite
from statistics import pstdev
from typing import Any, Callable

from .simulation import simulate, payoff_statistics
from .trade_audit import digest, pack_snapshot, trade_tape
from .costs import FEE_SCHEDULE, net_return


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
MIN_VALIDATION_TRADES = 40
MIN_HOLDOUT_TRADES = 10
MIN_PROFITABLE_WF_WINDOWS = 3
WF_WINDOW_COUNT = 5
MAX_DRAWDOWN_PERCENT = 15.0
EXPECTANCY_DECAY_FLOOR = 0.50
SELECTION_POLICY_VERSION = 4


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
    statistics = {}
    if items and all("winning_pnl" in item for item in items):
        statistics = payoff_statistics(
            sum(item["winning_trades"] for item in items), sum(item["losing_trades"] for item in items),
            sum(item["breakeven_trades"] for item in items), sum(item["winning_pnl"] for item in items),
            sum(item["losing_pnl"] for item in items))
    return {
        **statistics,
        "initial_capital": capital,
        "portfolio_value": round(capital + profit, 4),
        "realized_profit": round(profit, 4),
        "unrealized_profit": 0,
        "closed_trades": trades,
        "win_rate": round(wins / trades * 100, 1) if trades else 0,
        "max_drawdown_percent": round(max((float(item["max_drawdown_percent"]) for item in items), default=0), 2),
    }


def validation_rank(row: dict[str, Any]) -> tuple[float, float, float]:
    """Policy v4: one predeclared risk-adjusted OOS score, then sample size, then lower DD."""
    metrics = row["walk_forward_metrics"]
    if not row["validation_passed"] or int(metrics["closed_trades"]) < MIN_VALIDATION_TRADES:
        return (float("-inf"), float("-inf"), float("-inf"))
    return (
        float(row.get("risk_adjusted_oos_score", float("-inf"))),
        float(metrics.get("closed_trades", 0)),
        -float(metrics["max_drawdown_percent"]),
    )


def diagnostic_rank(row: dict) -> tuple:
    """Distance to validation gates only; never a candidate or holdout ranking."""
    m = row.get("walk_forward_metrics") or {}
    windows = row.get("validation_windows") or []
    other_failures = int(not float(m.get("realized_profit", 0)) > 0) + int(float(m.get("max_drawdown_percent", 100)) > 15) + int(sum(float(w.get("realized_profit", 0)) > 0 for w in windows) < 2)
    return (other_failures, max(0, 20 - int(m.get("closed_trades", 0))),
            -float(m.get("realized_profit", 0)), float(m.get("max_drawdown_percent", 100)), row.get("variant_id", ""))


def payoff_note(metrics: dict[str, Any], minimum: int, *, beats_hold: bool = False) -> str | None:
    payoff = metrics.get("payoff")
    if int(metrics.get("closed_trades", 0)) < minimum or payoff is None:
        return None
    if payoff < .8:
        return "slaby_pomer"
    if payoff < 1:
        return "podmienecne_ok" if float(metrics.get("win_rate", 0)) >= 55 and beats_hold else "upozornenie_pomer"
    return "slusny_pomer"


def walk_forward_windows(candles: list[dict[str, Any]], warmup: int = 40) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Keep the final 20% untouched and build five chronological expanding OOS windows."""
    holdout_start = max(120, int(len(candles) * 0.8))
    development, holdout = candles[:holdout_start], candles[max(0, holdout_start - warmup):]
    boundaries = ((0.40, 0.52), (0.52, 0.64), (0.64, 0.76), (0.76, 0.88), (0.88, 1.0))
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


def exposure_percent(trades: list[dict[str, Any]], period_minutes: float) -> float:
    """Average capital exposure for max_open_trades=1, based on recorded holding time."""
    if period_minutes <= 0:
        return 0.0
    held = sum(max(0.0, float(trade.get("hold_minutes") or trade.get("duration_min") or 0.0)) for trade in trades)
    return round(min(100.0, held / period_minutes * 100.0), 4)


def risk_adjusted_score(strategy_return_percent: float, benchmark_return_percent: float, drawdown_percent: float) -> float:
    excess = strategy_return_percent - benchmark_return_percent
    return round(excess / max(drawdown_percent, 1.0), 6)


def assess_validation(metrics: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Policy v4 eligibility uses stitched OOS validation only."""
    profitable = sum(float(window["realized_profit"]) > 0 for window in windows)
    reasons = []
    if int(metrics["closed_trades"]) < MIN_VALIDATION_TRADES:
        reasons.append("malo_obchodov")
    if not float(metrics["realized_profit"]) > 0:
        reasons.append("zaporny_pnl")
    if not 0 <= float(metrics["max_drawdown_percent"]) <= MAX_DRAWDOWN_PERCENT:
        reasons.append("drawdown")
    if len(windows) != WF_WINDOW_COUNT or profitable < MIN_PROFITABLE_WF_WINDOWS:
        reasons.append("nestabilita")
    if any(float(window.get("max_drawdown_percent", 0)) > MAX_DRAWDOWN_PERCENT for window in windows):
        reasons.append("window_drawdown")
    return {"validation_passed": not reasons, "validation_rejection_reasons": list(dict.fromkeys(reasons)),
            "profitable_validation_windows": profitable}


def assess_candidate(row: dict[str, Any], locked_pairs: list[str]) -> dict[str, Any]:
    """Policy v4 verdict. Raw buy-and-hold is diagnostic, not a veto."""
    validation = row.get("walk_forward_metrics") or {}
    holdout = row.get("holdout_metrics") or {}
    validation_trades = int(validation.get("closed_trades", 0))
    if not row.get("validation_passed"):
        codes = row.get("validation_rejection_reasons") or []
        only_underpowered = codes and set(codes).issubset({"malo_obchodov"})
        status = "unproven" if only_underpowered else "rejected"
        verdict = "NEPOTVRDENÉ — nedostatok dát" if only_underpowered else (
            "ZAMIETNUTÉ — drawdown validácie" if "drawdown" in codes or "window_drawdown" in codes else
            "ZAMIETNUTÉ — PnL validácie nie je kladné" if "zaporny_pnl" in codes else
            "ZAMIETNUTÉ — nestabilné WF okná" if "nestabilita" in codes else "ZAMIETNUTÉ — validácia"
        )
        reasons = ["walk_forward_failed"]
        if validation_trades < MIN_VALIDATION_TRADES:
            reasons.append("insufficient_validation_trades")
        if row.get("pair") not in locked_pairs:
            reasons.append("outside_locked_universe")
        return {"qualified": False, "result_status": status, "verdict": verdict,
                "rejection_reasons": reasons, "holdout_profit_percent": None}

    holdout_trades = int(holdout.get("closed_trades", 0))
    capital = float(row.get("settings", {}).get("initial_capital", 0))
    profit = float(holdout.get("realized_profit", float("nan")))
    drawdown = float(holdout.get("max_drawdown_percent", float("nan")))
    profit_pct = profit / capital * 100 if capital > 0 else float("nan")
    validation_expectancy = validation.get("expectancy")
    holdout_expectancy = holdout.get("expectancy")
    exposure_benchmark = row.get("holdout_exposure_matched_bh_percent")
    reasons = []

    if validation_trades < MIN_VALIDATION_TRADES:
        reasons.append("insufficient_validation_trades")
    if holdout_trades < MIN_HOLDOUT_TRADES:
        reasons.append("insufficient_holdout_trades")
    finite_values = [profit, drawdown, profit_pct]
    if validation_expectancy is not None:
        finite_values.append(float(validation_expectancy))
    if holdout_expectancy is not None:
        finite_values.append(float(holdout_expectancy))
    if exposure_benchmark is not None:
        finite_values.append(float(exposure_benchmark))
    if not all(isfinite(value) for value in finite_values):
        reasons.append("invalid_metrics")
    if not profit > 0:
        reasons.append("non_positive_holdout_pnl")
    if not 0 <= drawdown <= MAX_DRAWDOWN_PERCENT:
        reasons.append("drawdown_limit_exceeded")
    if holdout_expectancy is None or float(holdout_expectancy) <= 0:
        reasons.append("non_positive_holdout_expectancy")
    if validation_expectancy is not None and holdout_expectancy is not None and float(validation_expectancy) > 0:
        if float(holdout_expectancy) < EXPECTANCY_DECAY_FLOOR * float(validation_expectancy):
            reasons.append("expectancy_decay")
    if exposure_benchmark is None or not profit_pct >= float(exposure_benchmark):
        reasons.append("underperformed_exposure_matched_benchmark")
    if row.get("pair") not in locked_pairs:
        reasons.append("outside_locked_universe")

    underpowered = any(reason in reasons for reason in ("insufficient_validation_trades", "insufficient_holdout_trades"))
    hard_failures = [reason for reason in reasons if reason not in ("insufficient_validation_trades", "insufficient_holdout_trades")]
    if not reasons:
        status, verdict = "qualified", "QUALIFIED — metodika v4 potvrdená pre paper režim"
    elif underpowered and not hard_failures:
        status, verdict = "unproven", "UNPROVEN — nedostatok dát"
    elif "drawdown_limit_exceeded" in reasons:
        status, verdict = "rejected", "REJECTED — drawdown"
    elif "non_positive_holdout_pnl" in reasons or "non_positive_holdout_expectancy" in reasons:
        status, verdict = "rejected", "REJECTED — negatívny holdout edge"
    elif "expectancy_decay" in reasons:
        status, verdict = "rejected", "REJECTED — edge sa v holdoute rozpadol"
    elif "underperformed_exposure_matched_benchmark" in reasons:
        status, verdict = "rejected", "REJECTED — timing nepridal hodnotu voči exposure-matched hold"
    else:
        status, verdict = "rejected", "REJECTED — qualification v4"
    return {"qualified": not reasons, "result_status": status, "verdict": verdict,
            "rejection_reasons": reasons, "holdout_profit_percent": round(profit_pct, 4) if isfinite(profit_pct) else None}


def variant_diagnostic(row: dict[str, Any]) -> dict[str, Any]:
    """Serialise evidence without evaluating any additional holdouts."""
    evaluated = bool(row["validation_passed"] and row.get("holdout_evaluated"))
    reason_map = {"insufficient_holdout_trades": "holdout_malo_obchodov",
                  "non_positive_holdout_pnl": "zaporny_pnl",
                  "non_positive_holdout_expectancy": "zaporny_expectancy",
                  "expectancy_decay": "expectancy_decay",
                  "underperformed_exposure_matched_benchmark": "horsie_ako_exposure_matched_hold",
                  "drawdown_limit_exceeded": "drawdown"}
    reasons = list(row["validation_rejection_reasons"])
    if evaluated:
        reasons.extend(reason_map[reason] for reason in row.get("rejection_reasons", []) if reason in reason_map)
    return {**{key: row.get(key) for key in ("pair", "timeframe", "variant", "variant_id", "score", "settings",
            "validation_windows", "entry_diagnostics_windows", "walk_forward_metrics", "validation_passed", "profitable_validation_windows", "cost_per_side",
            "trades", "trade_tape_version", "validation_boundaries", "snapshot_id", "settings_sha256", "cost_components")},
            "is_finalist": bool(row.get("is_finalist")), "holdout_evaluated": evaluated,
            "holdout_metrics": row.get("holdout_metrics") if evaluated else None,
            "holdout_profit_percent": row.get("holdout_profit_percent") if evaluated else None,
            "buy_hold_percent": row.get("buy_hold_percent") if evaluated else None,
            "holdout_exposure_percent": row.get("holdout_exposure_percent") if evaluated else None,
            "holdout_exposure_matched_bh_percent": row.get("holdout_exposure_matched_bh_percent") if evaluated else None,
            "holdout_excess_return_percent": row.get("holdout_excess_return_percent") if evaluated else None,
            "validation_exposure_percent": row.get("validation_exposure_percent"),
            "validation_exposure_matched_bh_percent": row.get("validation_exposure_matched_bh_percent"),
            "validation_excess_return_percent": row.get("validation_excess_return_percent"),
            "risk_adjusted_oos_score": row.get("risk_adjusted_oos_score"),
            "vs_hold_percentage_points": round(row["holdout_profit_percent"] - row["buy_hold_percent"], 4) if evaluated else None,
            "rejection_reasons": list(dict.fromkeys(reasons)),
            "rejection_phase": "holdout" if evaluated else "validation" if reasons else None,
            "payoff_warning": payoff_note(row.get("holdout_metrics") or {}, MIN_HOLDOUT_TRADES,
                beats_hold=bool(evaluated and row["holdout_profit_percent"] >= row["buy_hold_percent"])) if evaluated else None,
            "status": "rejected" if reasons else "accepted" if evaluated and row.get("qualified") else "not_selected"}


def present_optimizer_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Downgrade legacy stored results on read, without rewriting history."""
    if not record or not record.get("result"):
        return record
    previous = record["result"]
    if previous.get("selection_policy_version") == SELECTION_POLICY_VERSION:
        variants = previous.get("variant_results") or []
        if variants and not previous.get("qualified") and not any(r.get("validation_passed") for r in variants):
            diagnostic = min(variants, key=diagnostic_rank)
            result = {**previous, **diagnostic, "validation_rejection_reasons": diagnostic.get("rejection_reasons", []),
                      "winner": None, "strategy_code": None, "diagnostic_only": True}
            result.update(assess_candidate(result, previous.get("locked_pairs", [])))
            return {**record, "result": result}
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
             *, locked_pairs: list[str] | None = None, cost_models: dict[str, dict] | None = None) -> dict[str, Any]:
    variants = candidates(base, trials)
    total = len(candle_sets) * len(variants)
    completed, results = 0, []
    snapshots = {}
    capital = float(base["initial_capital"])
    locked_pairs = list(locked_pairs or [])
    if capital <= 0 or not candle_sets:
        raise ValueError("Chýba kapitál alebo dáta pre optimalizáciu.")
    # An identical prefix warms every variant. Its candles cannot create trades.
    warmup = max(40, *(int(v[key]) + 2 for v in variants for key in ("bb_period", "rsi_period", "atr_period")))
    for (pair, timeframe), candles in candle_sets.items():
        if timeframe not in PRIMARY_TIMEFRAMES:
            raise ValueError("Optimalizácia podporuje iba intervaly 15m a 5m.")
        if len(candles) < 200:
            raise ValueError("Nedostatok sviečok pre tri validačné okná a holdout.")
        windows, _ = walk_forward_windows(candles, warmup)
        boundary = max(120, int(len(candles) * .8))
        snapshot_id = f"{pair}:{timeframe}"
        snapshots[snapshot_id] = pack_snapshot(candles[:boundary], int(candles[boundary]["open_time"]))
        boundaries = [{"window": f"wf{number}", "warmup_start_index": max(0, len(train) - warmup),
                       "trading_start_index": len(train), "trading_start_ms": int(candles[len(train)]["open_time"]),
                       "stop_index": max(0, len(train) - warmup) + len(validation)}
                      for number, (train, validation) in enumerate(windows, 1)]
        cost_per_side = cost_models[pair]["fee_rate"] if cost_models is not None else COST_PER_SIDE.get(timeframe, 0.0018)
        cost_options = {"cost_models": {pair: cost_models[pair]}} if cost_models is not None else {}
        for index, variant in enumerate(variants):
            settings = base | variant | {"selected_pairs": [pair], "timeframe": timeframe, "max_open_trades": 1}
            variant_id = f"{pair}:{timeframe}:v{index + 1}"
            train_metrics, validation_window_metrics, entry_diagnostics_windows, trades = [], [], [], []
            for number, (train, validation) in enumerate(windows, 1):
                train_metrics.append(simulate({pair: train}, settings, fee=cost_per_side, force_close_at_end=True, **cost_options)["metrics"])
                validation_start = int(candles[len(train)]["open_time"])
                validation_run = simulate(
                    {pair: validation}, settings, fee=cost_per_side, force_close_at_end=True,
                    trading_start_time=validation_start, **cost_options,
                )
                validation_window_metrics.append(validation_run["metrics"])
                entry_diagnostics_windows.append({
                    "window": f"wf{number}",
                    **validation_run.get("entry_diagnostics", {}),
                })
                trades.extend(trade_tape(validation_run, pair, timeframe, variant_id, f"wf{number}", cost_per_side))
            validation_metrics = aggregate_metrics(validation_window_metrics, capital)
            validation_period_minutes = sum(
                max(0.0, (int(validation[-1]["close_time"]) - int(candles[len(train)]["open_time"])) / 60000)
                for train, validation in windows if validation
            )
            validation_exposure = exposure_percent(trades, validation_period_minutes)
            validation_bh = sum(
                buy_and_hold_percent(validation[max(0, warmup):], cost_per_side) * exposure_percent(
                    [trade for trade in trades if trade["window"] == f"wf{number}"],
                    max(1.0, (int(validation[-1]["close_time"]) - int(candles[len(train)]["open_time"])) / 60000),
                ) / 100.0
                for number, (train, validation) in enumerate(windows, 1) if validation
            )
            validation_return = float(validation_metrics["realized_profit"]) / capital * 100
            validation_excess = validation_return - validation_bh
            validation_risk_score = risk_adjusted_score(
                validation_return, validation_bh, float(validation_metrics["max_drawdown_percent"])
            )
            window_profit_pcts = [float(item["realized_profit"]) / capital * 100 for item in validation_window_metrics]
            stability = pstdev(window_profit_pcts) if len(window_profit_pcts) > 1 else 0.0
            candidate_score = score(validation_metrics, capital, stability)
            validation = assess_validation(validation_metrics, validation_window_metrics)
            results.append({
                "pair": pair, "timeframe": timeframe, "variant": index + 1, "settings": settings,
                "train_metrics": train_metrics, "walk_forward_metrics": validation_metrics,
                "validation_metrics": validation_metrics, "validation_windows": validation_window_metrics,
                "entry_diagnostics_windows": entry_diagnostics_windows,
                "cost_per_side": cost_per_side, "score": candidate_score, **validation,
                "validation_exposure_percent": validation_exposure,
                "validation_exposure_matched_bh_percent": round(validation_bh, 4),
                "validation_excess_return_percent": round(validation_excess, 4),
                "risk_adjusted_oos_score": validation_risk_score,
                "cost_components": cost_models[pair] if cost_models is not None else None,
                "variant_id": variant_id, "trades": trades, "trade_tape_version": 1,
                "validation_boundaries": boundaries, "snapshot_id": snapshot_id, "settings_sha256": digest(settings),
                "holdout_evaluated": False, "holdout_metrics": None, "buy_hold_percent": None,
                "holdout_profit_percent": None, "holdout_exposure_percent": None,
                "holdout_exposure_matched_bh_percent": None, "holdout_excess_return_percent": None,
                "qualified": False, "strategy_code": None,
            })
            completed += 1
            if progress:
                progress(completed, total, f"{pair} · {timeframe}")

    # Freeze ONE eligible finalist per coin using validation only, across both timeframes.
    # Holdout is a veto, never a second parameter search or a fallback loop.
    results.sort(key=lambda row: (row["validation_passed"], *validation_rank(row)), reverse=True)
    finalists: dict[str, dict[str, Any]] = {}
    for row in results:
        finalists.setdefault(row["pair"], row)
    for row in finalists.values():
        if not row["validation_passed"]:
            row.update(assess_candidate(row, locked_pairs))
            continue
        row["is_finalist"] = True
        candles = candle_sets[(row["pair"], row["timeframe"])]
        _, holdout = walk_forward_windows(candles, warmup)
        boundary = max(120, int(len(candles) * 0.8))
        holdout_start = int(candles[boundary]["open_time"])
        holdout_run = simulate(
            {row["pair"]: holdout}, row["settings"], fee=row["cost_per_side"], force_close_at_end=True,
            trading_start_time=holdout_start,
            **({"cost_models": {row["pair"]: row["cost_components"]}} if row["cost_components"] else {}),
        )
        row["holdout_metrics"] = holdout_run["metrics"]
        holdout_tape = trade_tape(holdout_run, row["pair"], row["timeframe"], row["variant_id"], "holdout", row["cost_per_side"])
        row["trades"].extend(holdout_tape)
        # Raw buy-and-hold remains diagnostic. Qualification uses an exposure-matched benchmark.
        row["buy_hold_percent"] = (round(net_return(float(candles[-1]["close"]) / float(candles[boundary]["close"]), row["cost_components"]) * 100, 4)
                                   if row["cost_components"] else buy_and_hold_percent(candles[boundary:], row["cost_per_side"]))
        holdout_period_minutes = max(1.0, (int(candles[-1]["close_time"]) - holdout_start) / 60000)
        row["holdout_exposure_percent"] = exposure_percent(holdout_tape, holdout_period_minutes)
        row["holdout_exposure_matched_bh_percent"] = round(
            row["buy_hold_percent"] * row["holdout_exposure_percent"] / 100.0, 4
        )
        holdout_return = float(row["holdout_metrics"]["realized_profit"]) / capital * 100
        row["holdout_excess_return_percent"] = round(
            holdout_return - row["holdout_exposure_matched_bh_percent"], 4
        )
        row["holdout_from_ms"] = holdout_start
        row["holdout_to_ms"] = int(candles[-1]["close_time"])
        row["holdout_evaluated"] = True
        row.update(assess_candidate(row, locked_pairs))
        row["strategy_code"] = generate_freqtrade_strategy(row["pair"], row["timeframe"], row["settings"]) if row["qualified"] else None

    qualified = [row for row in finalists.values() if row["qualified"]]
    selected = qualified[0] if qualified else next(iter(finalists.values()))
    if not any(row["validation_passed"] for row in results):
        selected = min(results, key=diagnostic_rank)
        selected.update(assess_candidate(selected, locked_pairs))
    summary_keys = ("pair", "timeframe", "variant", "score", "walk_forward_metrics", "holdout_metrics",
                    "buy_hold_percent", "holdout_profit_percent", "holdout_exposure_percent",
                    "holdout_exposure_matched_bh_percent", "holdout_excess_return_percent",
                    "validation_exposure_percent", "validation_exposure_matched_bh_percent",
                    "validation_excess_return_percent", "risk_adjusted_oos_score",
                    "qualified", "result_status", "verdict", "rejection_reasons",
                    "validation_passed", "holdout_evaluated", "profitable_validation_windows")
    return {
        **selected,
        "winner": {**selected} if qualified else None,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "selection_method": "policy_v4_validation_only_per_coin_then_single_terminal_holdout",
        "ranking_method": "risk_adjusted_oos_excess_return_over_drawdown",
        "locked_pairs": locked_pairs,
        "tested_combinations": total,
        "max_validation_trades": max(int(row["walk_forward_metrics"]["closed_trades"]) for row in results),
        "minimum_validation_trades": MIN_VALIDATION_TRADES,
        "minimum_holdout_trades": MIN_HOLDOUT_TRADES,
        "max_drawdown_limit_percent": MAX_DRAWDOWN_PERCENT,
        "job_verdict": "QUALIFIED" if qualified else ("UNPROVEN" if any(row.get("result_status") == "unproven" for row in finalists.values()) else "REJECTED"),
        "method": "5× walk-forward OOS + 20 % nedotknutý holdout · qualification policy v4",
        "fee_schedule": FEE_SCHEDULE if cost_models is not None else "legacy_combined_cost",
        "cost_profiles": cost_models,
        "cost_model": "Taker 0,10 % + half-spread + impact podľa knihy každého páru. Kniha je aktuálny odhad, nie historické L2." if cost_models is not None else f"Historický model: {selected['cost_per_side'] * 100:.2f} % na každej strane (fee + spread/sklz)",
        "per_coin_results": [{key: row[key] for key in summary_keys} for row in finalists.values()],
        "variant_results": [variant_diagnostic(row) for row in sorted(results, key=lambda item: (item["pair"], item["timeframe"], item["variant"]))],
        "replay_snapshots": snapshots,
        "all_variants_insufficient_trades": all("malo_obchodov" in row["validation_rejection_reasons"] for row in results),
        "top_results": [{key: row[key] for key in ("pair", "timeframe", "variant", "score", "validation_metrics")} for row in results[:5]],
    }
