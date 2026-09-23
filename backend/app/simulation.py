"""Standalone spot simulation engine used by Revoltis Bot Strategy.

It consumes public OHLCV candles and simulates trades locally.  It never
connects an account and never creates an exchange order.
"""
from __future__ import annotations

from .costs import net_return

from collections import defaultdict
from datetime import UTC, datetime
from math import sqrt
from typing import Any


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    average = _mean(values)
    return sqrt(_mean([(value - average) ** 2 for value in values]))


def _rsi(closes: list[float], period: int) -> float | None:
    if len(closes) <= period:
        return None
    gains = [max(0.0, closes[index] - closes[index - 1]) for index in range(-period, 0)]
    losses = [max(0.0, closes[index - 1] - closes[index]) for index in range(-period, 0)]
    average_gain, average_loss = _mean(gains), _mean(losses)
    if average_loss == 0:
        return 100.0
    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def _atr(candles: list[dict[str, float]], period: int) -> float | None:
    if len(candles) <= period:
        return None
    values = []
    for index in range(-period, 0):
        candle, previous = candles[index], candles[index - 1]
        values.append(max(candle["high"] - candle["low"], abs(candle["high"] - previous["close"]), abs(candle["low"] - previous["close"])))
    return _mean(values)


def _iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()


def payoff_statistics(wins: int, losses: int, breakeven: int, winning_pnl: float, losing_pnl: float) -> dict[str, Any]:
    """Amounts are already net of both sides' fee/spread/slippage model."""
    count = wins + losses + breakeven
    avg_win = winning_pnl / wins if wins else 0.0
    avg_loss = losing_pnl / losses if losses else 0.0
    return {"winning_trades": wins, "losing_trades": losses, "breakeven_trades": breakeven,
            "winning_pnl": round(winning_pnl, 8), "losing_pnl": round(losing_pnl, 8),
            "avg_win": round(avg_win, 8), "avg_loss": round(avg_loss, 8),
            "payoff": round(avg_win / avg_loss, 8) if avg_loss > 0 else None,
            # Zero-PnL trades count in N, but must not be counted as losses.
            "expectancy": round((winning_pnl - losing_pnl) / count, 8) if count else None}


def trade_statistics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    profits = [float(trade["profit_usdt"]) for trade in trades if trade.get("status") == "closed"]
    wins = [profit for profit in profits if profit > 0]
    losses = [-profit for profit in profits if profit < 0]
    return payoff_statistics(len(wins), len(losses), len(profits) - len(wins) - len(losses), sum(wins), sum(losses))


def simulate(
    candles_by_pair: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
    fee: float = 0.001,
    force_close_at_end: bool = False,
    trading_start_time: int | None = None,
    cost_models: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Run the mean-reversion strategy on public historical candles."""
    bb_period = int(settings["bb_period"])
    rsi_period = int(settings["rsi_period"])
    atr_period = int(settings["atr_period"])
    max_no_trail_hours = float(settings.get("max_no_trail_hours") or 0)
    max_no_trail_ms = max_no_trail_hours * 3_600_000 if max_no_trail_hours > 0 else None
    warmup = max(bb_period, rsi_period, atr_period, 20) + 2
    events: list[tuple[int, str, int]] = []
    for pair, candles in candles_by_pair.items():
        for index in range(warmup, len(candles)):
            # Earlier candles warm indicators only; never count their trades
            # in a validation window or in the untouched holdout.
            if trading_start_time is not None and int(candles[index]["open_time"]) < trading_start_time:
                continue
            events.append((int(candles[index]["close_time"]), pair, index))
    events.sort()

    cash = float(settings["initial_capital"])
    initial_capital = cash
    positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily_entries: dict[str, int] = defaultdict(int)
    equity_curve = [{"time": "start", "value": round(cash, 4)}]
    rejection_counts: dict[str, int] = defaultdict(int)
    entry_diagnostics = {
        "n_bars": 0,
        "skip_bollinger": 0,
        "skip_rsi": 0,
        "skip_reversal": 0,
        "skip_rebound": 0,
        "skip_atr": 0,
        "skip_volume": 0,
        "skip_other": 0,
        "passed_entry": 0,
    }
    latest_prices: dict[str, float] = {}
    peak, drawdown = initial_capital, 0.0
    pair_peaks = {pair: initial_capital for pair in candles_by_pair}
    pair_drawdowns = {pair: 0.0 for pair in candles_by_pair}
    sample_every = max(1, len(events) // 1000)

    if cost_models is not None and any(pair not in cost_models for pair in candles_by_pair):
        raise ValueError("missing_pair_cost_model")

    def position_return(position: dict, price: float, *, legacy_sides: int = 2) -> float:
        ratio = price / position["entry_rate"]
        if cost_models is not None:
            return net_return(ratio, cost_models[position["pair"]])
        return ratio - 1 - legacy_sides * fee

    def cost_log(position: dict, price: float) -> dict:
        if cost_models is None:
            return {}
        profile = cost_models[position["pair"]]
        entry_value = position["stake"] / (1 + profile["entry_cost_rate"])
        exit_value = entry_value * price / position["entry_rate"]
        return {"cost_components": profile,
                "entry_fee_usdt": round(entry_value * profile["fee_rate"], 6),
                "exit_fee_usdt": round(exit_value * profile["fee_rate"], 6),
                "spread_impact_usdt": round(entry_value * (profile["half_spread"] + profile["buy_impact"]) + exit_value * (profile["half_spread"] + profile["sell_impact"]), 6),
                "net_return_percent": round(position_return(position, price) * 100, 6)}

    def marked_equity() -> float:
        value = cash
        for position in positions:
            price = latest_prices.get(position["pair"], position["entry_rate"])
            value += position["stake"] * (1 + position_return(position, price, legacy_sides=1))
        return value

    def excursion_log(position: dict[str, Any], close_time: int, *, sl_before_trail: bool = False,
                      prior_high: float | None = None) -> dict[str, Any]:
        # Entries/exits execute at candle close. Entry-candle wicks precede the
        # position and must not be counted; subsequent full candles are observed.
        entry = position["entry_rate"]
        mfe = max(0.0, (position["high_watermark"] / entry - 1) * 100)
        return {
            "mae": round(min(0.0, (position["low_watermark"] / entry - 1) * 100), 8),
            "mfe": round(mfe, 8),
            "excursion_unit": "gross_price_percent",
            "duration_min": (close_time - position["entry_ts_ms"]) / 60_000,
            "trailing_start_percent": float(settings["trailing_start_percent"]),
            "sl_before_trail": sl_before_trail,
            "trailing_start_reached": position["high_watermark"] >= entry * (1 + float(settings["trailing_start_percent"]) / 100),
            "trail_active_before_exit_candle": (prior_high if prior_high is not None else entry) >= entry * (1 + float(settings["trailing_start_percent"]) / 100),
            "max_no_trail_hours": max_no_trail_hours or None,
        }

    for event_number, (close_time, pair, index) in enumerate(events):
        candles = candles_by_pair[pair]
        candle = candles[index]
        close = float(candle["close"])
        latest_prices[pair] = close
        current_positions = [position for position in positions if position["pair"] == pair]
        for position in current_positions:
            prior_high = position["high_watermark"]
            position["high_watermark"] = max(position["high_watermark"], float(candle["high"]))
            position["low_watermark"] = min(position["low_watermark"], float(candle["low"]))
            position["prior_high"] = prior_high
            trail_activated = position["high_watermark"] >= position["entry_rate"] * (1 + float(settings["trailing_start_percent"]) / 100)
            trailing_triggered = trail_activated and close <= position["high_watermark"] * (1 - float(settings["trailing_distance_percent"]) / 100)
            exit_reason = None
            if close <= position["entry_rate"] * (1 - float(settings["stop_loss_percent"]) / 100):
                exit_reason = "stop_loss"
            elif trailing_triggered:
                exit_reason = "trailing_profit"
            elif max_no_trail_ms is not None and close_time - position["entry_ts_ms"] >= max_no_trail_ms and not trail_activated:
                exit_reason = "max_no_trail_hours"
            if exit_reason:
                gross_return = close / position["entry_rate"] - 1
                profit = position["stake"] * position_return(position, close)
                cash += position["stake"] + profit
                positions.remove(position)
                trades.append({"id": f"simulation-{pair}-{close_time}-{len(trades)}", "pair": pair, "status": "closed", "opened_at": position["opened_at"], "closed_at": _iso(close_time), "entry_rate": position["entry_rate"], "exit_rate": close, "stake_amount": position["stake"], "profit_usdt": round(profit, 6), "exit_reason": exit_reason, "raw": {"source": "okx_public_candles", "entry_fee_usdt": round(position["stake"] * fee, 6), "exit_fee_usdt": round(position["stake"] * fee, 6), "gross_return_percent": round(gross_return * 100, 6), "net_return_percent": round((gross_return - 2 * fee) * 100, 6), "holding_candles": index - position["entry_index"]}})
                trades[-1]["raw"].update(excursion_log(position, close_time,
                    sl_before_trail=exit_reason == "stop_loss" and trailing_triggered, prior_high=prior_high))
                trades[-1]["raw"].update(cost_log(position, close))

        equity = marked_equity()
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100 if peak else 0)
        pair_value = initial_capital + sum(float(trade["profit_usdt"]) for trade in trades if trade["pair"] == pair)
        pair_value += sum(position["stake"] * position_return(position, close, legacy_sides=1) for position in positions if position["pair"] == pair)
        pair_peaks[pair] = max(pair_peaks[pair], pair_value)
        pair_drawdowns[pair] = max(pair_drawdowns[pair], (pair_peaks[pair] - pair_value) / pair_peaks[pair] * 100 if pair_peaks[pair] else 0)
        if event_number % sample_every == 0:
            equity_curve.append({"time": _iso(close_time), "value": round(equity, 4)})

        window = [float(item["close"]) for item in candles[index - bb_period + 1:index + 1]]
        average, deviation = _mean(window), _std(window)
        lower_band = average - float(settings["bb_deviation"]) * deviation
        recent_closes = [float(item["close"]) for item in candles[max(0, index - rsi_period - 1):index + 1]]
        current_rsi = _rsi(recent_closes, rsi_period)
        previous_rsi = _rsi(recent_closes[:-1], rsi_period)
        current_atr = _atr(candles[max(0, index - atr_period):index + 1], atr_period)
        volume_mean = _mean([float(item["volume"]) for item in candles[max(0, index - 20):index]])
        previous = candles[index - 1]
        previous_low = float(previous["low"])
        rebound_percent = ((close / previous_low) - 1) * 100 if previous_low else 0
        day = _iso(close_time)[:10]
        atr_ratio = current_atr / close if current_atr and close else 0

        bollinger_ok = float(previous["close"]) < lower_band
        rsi_ok = bool(previous_rsi is not None and previous_rsi < float(settings["rsi_oversold"]))
        reversal_ok = close > float(previous["close"]) and close > float(candle["open"])
        rebound_ok = float(settings["rebound_min_percent"]) <= rebound_percent <= float(settings["rebound_max_percent"])
        atr_ok = float(settings["atr_min_percent"]) / 100 <= atr_ratio <= float(settings["atr_max_percent"]) / 100
        volume_ok = (
            float(candle["volume"]) >= float(settings["min_volume_ratio"]) * volume_mean
            and float(candle["volume"]) * close >= float(settings["min_quote_volume_usdt"])
        )

        # Diagnostics are observational only. The legacy condition groups below
        # remain logically identical, so entries/exits and all qualification
        # thresholds are unchanged.
        entry_diagnostics["n_bars"] += 1
        if not bollinger_ok:
            entry_diagnostics["skip_bollinger"] += 1
        if not rsi_ok:
            entry_diagnostics["skip_rsi"] += 1
        if not reversal_ok:
            entry_diagnostics["skip_reversal"] += 1
        if not rebound_ok:
            entry_diagnostics["skip_rebound"] += 1
        if not atr_ok:
            entry_diagnostics["skip_atr"] += 1
        if not volume_ok:
            entry_diagnostics["skip_volume"] += 1

        conditions = {
            "pokles pod Bollinger pásmo": bollinger_ok,
            "RSI nie je prepredané": rsi_ok,
            "potvrdenie otočenia": reversal_ok and rebound_ok,
            "ATR volatilita": atr_ok,
            "objem": volume_ok,
        }
        if not all(conditions.values()):
            for reason, passed in conditions.items():
                if not passed:
                    rejection_counts[reason] += 1
            continue
        if len(positions) >= int(settings["max_open_trades"]):
            rejection_counts["limit otvorených pozícií"] += 1
            entry_diagnostics["skip_other"] += 1
            continue
        if daily_entries[day] >= int(settings["daily_trade_limit"]):
            rejection_counts["denný limit obchodov"] += 1
            entry_diagnostics["skip_other"] += 1
            continue
        stake = min(float(settings["stake_amount"]), cash)
        if stake < 5:
            rejection_counts["nedostatok simulovaného kapitálu"] += 1
            entry_diagnostics["skip_other"] += 1
            continue
        cash -= stake
        daily_entries[day] += 1
        entry_diagnostics["passed_entry"] += 1
        positions.append({"pair": pair, "entry_rate": close, "stake": stake, "opened_at": _iso(close_time), "high_watermark": close, "low_watermark": close, "entry_ts_ms": close_time, "entry_index": index})

    if force_close_at_end:
        for position in list(positions):
            pair = position["pair"]
            close = last_close = float(candles_by_pair[pair][-1]["close"])
            close_time = int(candles_by_pair[pair][-1]["close_time"])
            gross_return = close / position["entry_rate"] - 1
            profit = position["stake"] * position_return(position, close)
            cash += position["stake"] + profit
            positions.remove(position)
            trades.append({"id": f"simulation-{pair}-{close_time}-{len(trades)}", "pair": pair, "status": "closed", "opened_at": position["opened_at"], "closed_at": _iso(close_time), "entry_rate": position["entry_rate"], "exit_rate": last_close, "stake_amount": position["stake"], "profit_usdt": round(profit, 6), "exit_reason": "end_of_test", "raw": {"source": "okx_public_candles", "entry_fee_usdt": round(position["stake"] * fee, 6), "exit_fee_usdt": round(position["stake"] * fee, 6), "gross_return_percent": round(gross_return * 100, 6), "net_return_percent": round((gross_return - 2 * fee) * 100, 6), "holding_candles": len(candles_by_pair[pair]) - 1 - position["entry_index"]}})
            trades[-1]["raw"].update(excursion_log(position, close_time, prior_high=position.get("prior_high")))
            trades[-1]["raw"].update(cost_log(position, close))
        final_time = max((int(candles[-1]["close_time"]) for candles in candles_by_pair.values() if candles), default=0)
        if final_time:
            equity_curve.append({"time": _iso(final_time), "value": round(cash, 4)})
            peak = max(peak, cash)
            drawdown = max(drawdown, (peak - cash) / peak * 100 if peak else 0)

    last_prices = {pair: float(candles[-1]["close"]) for pair, candles in candles_by_pair.items() if candles}
    unrealized = sum(position["stake"] * position_return(position, last_prices[position["pair"]], legacy_sides=1) for position in positions)
    portfolio_value = cash + sum(position["stake"] for position in positions) + unrealized
    wins = sum(1 for trade in trades if trade["profit_usdt"] > 0)
    per_pair_metrics: dict[str, dict[str, float | int]] = {}
    per_pair_equity_curves: dict[str, list[dict[str, Any]]] = {}
    for pair in candles_by_pair:
        pair_trades = sorted((trade for trade in trades if trade["pair"] == pair), key=lambda trade: trade.get("closed_at") or "")
        pair_realized = sum(float(trade["profit_usdt"]) for trade in pair_trades)
        pair_unrealized = sum(position["stake"] * position_return(position, last_prices[pair], legacy_sides=1) for position in positions if position["pair"] == pair and pair in last_prices)
        pair_wins = sum(1 for trade in pair_trades if float(trade["profit_usdt"]) > 0)
        pair_equity = initial_capital
        pair_peak, pair_drawdown = initial_capital, pair_drawdowns[pair]
        pair_curve = [{"time": "start", "value": round(pair_equity, 4)}]
        for trade in pair_trades:
            pair_equity += float(trade["profit_usdt"])
            pair_peak = max(pair_peak, pair_equity)
            pair_drawdown = max(pair_drawdown, (pair_peak - pair_equity) / pair_peak * 100 if pair_peak else 0)
            pair_curve.append({"time": trade.get("closed_at") or trade.get("opened_at"), "value": round(pair_equity, 4)})
        per_pair_metrics[pair] = {
            **trade_statistics(pair_trades),
            "initial_capital": initial_capital,
            "portfolio_value": round(initial_capital + pair_realized + pair_unrealized, 4),
            "realized_profit": round(pair_realized, 4),
            "unrealized_profit": round(pair_unrealized, 4),
            "closed_trades": len(pair_trades),
            "win_rate": round(pair_wins / len(pair_trades) * 100, 1) if pair_trades else 0,
            "max_drawdown_percent": round(pair_drawdown, 2),
            "open_trades": sum(1 for position in positions if position["pair"] == pair),
        }
        per_pair_equity_curves[pair] = pair_curve
    return {"trades": trades, "open_positions": len(positions), "metrics": {**trade_statistics(trades), "initial_capital": initial_capital, "portfolio_value": round(portfolio_value, 4), "realized_profit": round(sum(item["profit_usdt"] for item in trades), 4), "unrealized_profit": round(unrealized, 4), "closed_trades": len(trades), "win_rate": round(wins / len(trades) * 100, 1) if trades else 0, "max_drawdown_percent": round(drawdown, 2)}, "per_pair_metrics": per_pair_metrics, "per_pair_equity_curves": per_pair_equity_curves, "equity_curve": equity_curve, "rejections": dict(sorted(rejection_counts.items(), key=lambda item: item[1], reverse=True)[:8]), "entry_diagnostics": entry_diagnostics}
