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
MIN_VALIDATION_TRADES = 20
MIN_HOLDOUT_TRADES = 10
MAX_DRAWDOWN_PERCENT = 15.0
SELECTION_POLICY_VERSION = 3


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
    metrics = row["walk_forward_metrics"]
    if not row["validation_passed"] or int(metrics["closed_trades"]) < MIN_VALIDATION_TRADES:
        return (float("-inf"), float("-inf"), float("-inf"))
    expectancy = metrics.get("expectancy")
    if expectancy is None:
        expectancy = float(metrics["realized_profit"]) / int(metrics["closed_trades"])
    payoff = metrics.get("payoff")
    return (float(expectancy), float(payoff) if payoff is not None else float("-inf"), -float(metrics["max_drawdown_percent"]))


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


def assess_validation(metrics: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Eligibility uses validation only; score ranks eligible variants."""
    profitable = sum(float(window["realized_profit"]) > 0 for window in windows)
    reasons = []
    if int(metrics["closed_trades"]) < MIN_VALIDATION_TRADES:
        reasons.append("malo_obchodov")
    if not float(metrics["realized_profit"]) > 0:
        reasons.append("zaporny_pnl")
    if not 0 <= float(metrics["max_drawdown_percent"]) <= MAX_DRAWDOWN_PERCENT:
        reasons.append("drawdown")
    if len(windows) != 3 or profitable < 2:
        reasons.append("nestabilita")
    return {"validation_passed": not reasons, "validation_rejection_reasons": reasons,
            "profitable_validation_windows": profitable}


def assess_candidate(row: dict[str, Any], locked_pairs: list[str]) -> dict[str, Any]:
    """All gates are mandatory. Training metrics never enter this verdict."""
    validation = row.get("walk_forward_metrics") or {}
    holdout = row.get("holdout_metrics") or {}
    validation_trades = int(validation.get("closed_trades", 0))
    if not row.get("validation_passed"):
        codes = row.get("validation_rejection_reasons") or []
        reasons = ["walk_forward_failed"]
        if validation_trades < MIN_VALIDATION_TRADES:
            reasons.append("insufficient_validation_trades")
        if row.get("pair") not in locked_pairs:
            reasons.append("outside_locked_universe")
        verdict = ("NEDOSTATOK OBCHODOV" if validation_trades < MIN_VALIDATION_TRADES else
                   "NEPREŠIEL — drawdown validácie" if "drawdown" in codes else
                   "NEPREŠIEL — PnL validácie nie je kladné" if "zaporny_pnl" in codes else
                   "NEPREŠIEL — nestabilné WF okná" if "nestabilita" in codes else "NEPREŠIEL — validácia")
        return {"qualified": False, "result_status": "insufficient_trades" if validation_trades < MIN_VALIDATION_TRADES else "validation_failed",
                "verdict": verdict, "rejection_reasons": reasons, "holdout_profit_percent": None}
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


def variant_diagnostic(row: dict[str, Any]) -> dict[str, Any]:
    """Serialise evidence without evaluating any additional holdouts."""
    evaluated = bool(row["validation_passed"] and row.get("holdout_evaluated"))
    reason_map = {"insufficient_holdout_trades": "holdout_malo_obchodov",
                  "non_positive_holdout_pnl": "zaporny_pnl", "underperformed_buy_and_hold": "horsie_ako_hold",
                  "drawdown_limit_exceeded": "drawdown"}
    reasons = list(row["validation_rejection_reasons"])
    if evaluated:
        reasons.extend(reason_map[reason] for reason in row.get("rejection_reasons", []) if reason in reason_map)
    return {**{key: row.get(key) for key in ("pair", "timeframe", "variant", "variant_id", "score", "settings",
            "validation_windows", "walk_forward_metrics", "validation_passed", "profitable_validation_windows", "cost_per_side",
            "trades", "trade_tape_version", "validation_boundaries", "snapshot_id", "settings_sha256", "cost_components")},
            "is_finalist": bool(row.get("is_finalist")), "holdout_evaluated": evaluated,
            "holdout_metrics": row.get("holdout_metrics") if evaluated else None,
            "holdout_profit_percent": row.get("holdout_profit_percent") if evaluated else None,
            "buy_hold_percent": row.get("buy_hold_percent") if evaluated else None,
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
            train_metrics, validation_window_metrics, trades = [], [], []
            for number, (train, validation) in enumerate(windows, 1):
                train_metrics.append(simulate({pair: train}, settings, fee=cost_per_side, force_close_at_end=True, **cost_options)["metrics"])
                validation_start = int(candles[len(train)]["open_time"])
                validation_run = simulate(
                    {pair: validation}, settings, fee=cost_per_side, force_close_at_end=True,
                    trading_start_time=validation_start, **cost_options,
                )
                validation_window_metrics.append(validation_run["metrics"])
                trades.extend(trade_tape(validation_run, pair, timeframe, variant_id, f"wf{number}", cost_per_side))
            validation_metrics = aggregate_metrics(validation_window_metrics, capital)
            window_profit_pcts = [float(item["realized_profit"]) / capital * 100 for item in validation_window_metrics]
            stability = pstdev(window_profit_pcts) if len(window_profit_pcts) > 1 else 0.0
            candidate_score = score(validation_metrics, capital, stability)
            validation = assess_validation(validation_metrics, validation_window_metrics)
            results.append({
                "pair": pair, "timeframe": timeframe, "variant": index + 1, "settings": settings,
                "train_metrics": train_metrics, "walk_forward_metrics": validation_metrics,
                "validation_metrics": validation_metrics, "validation_windows": validation_window_metrics,
                "cost_per_side": cost_per_side, "score": candidate_score, **validation,
                "cost_components": cost_models[pair] if cost_models is not None else None,
                "variant_id": variant_id, "trades": trades, "trade_tape_version": 1,
                "validation_boundaries": boundaries, "snapshot_id": snapshot_id, "settings_sha256": digest(settings),
                "holdout_evaluated": False, "holdout_metrics": None, "buy_hold_percent": None,
                "holdout_profit_percent": None, "qualified": False, "strategy_code": None,
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
        row["trades"].extend(trade_tape(holdout_run, row["pair"], row["timeframe"], row["variant_id"], "holdout", row["cost_per_side"]))
        # Benchmark and strategy cover exactly the same dates and initial capital.
        row["buy_hold_percent"] = (round(net_return(float(candles[-1]["close"]) / float(candles[boundary]["close"]), row["cost_components"]) * 100, 4)
                                   if row["cost_components"] else buy_and_hold_percent(candles[boundary:], row["cost_per_side"]))
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
                    "buy_hold_percent", "holdout_profit_percent", "qualified", "result_status",
                    "verdict", "rejection_reasons", "validation_passed", "holdout_evaluated")
    return {
        **selected,
        "winner": {**selected} if qualified else None,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "selection_method": "validation_only_per_coin_then_holdout_veto",
        "ranking_method": "validation_expectancy_then_payoff_then_lower_drawdown",
        "locked_pairs": locked_pairs,
        "tested_combinations": total,
        "max_validation_trades": max(int(row["walk_forward_metrics"]["closed_trades"]) for row in results),
        "minimum_validation_trades": MIN_VALIDATION_TRADES,
        "minimum_holdout_trades": MIN_HOLDOUT_TRADES,
        "max_drawdown_limit_percent": MAX_DRAWDOWN_PERCENT,
        "job_verdict": "KANDIDÁT" if qualified else "ŽIADNY PLATNÝ VARIANT",
        "method": "3× walk-forward + 20 % nedotknutý holdout",
        "fee_schedule": FEE_SCHEDULE if cost_models is not None else "legacy_combined_cost",
        "cost_profiles": cost_models,
        "cost_model": "Taker 0,10 % + half-spread + impact podľa knihy každého páru. Kniha je aktuálny odhad, nie historické L2." if cost_models is not None else f"Historický model: {selected['cost_per_side'] * 100:.2f} % na každej strane (fee + spread/sklz)",
        "per_coin_results": [{key: row[key] for key in summary_keys} for row in finalists.values()],
        "variant_results": [variant_diagnostic(row) for row in sorted(results, key=lambda item: (item["pair"], item["timeframe"], item["variant"]))],
        "replay_snapshots": snapshots,
        "all_variants_insufficient_trades": all("malo_obchodov" in row["validation_rejection_reasons"] for row in results),
        "top_results": [{key: row[key] for key in ("pair", "timeframe", "variant", "score", "validation_metrics")} for row in results[:5]],
    }
