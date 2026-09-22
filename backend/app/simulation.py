"""Standalone spot simulation engine used by Revoltis Bot Strategy.

It consumes public OHLCV candles and simulates trades locally.  It never
connects an account and never creates an exchange order.
"""
from __future__ import annotations

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


def simulate(
    candles_by_pair: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
    fee: float = 0.001,
    force_close_at_end: bool = False,
) -> dict[str, Any]:
    """Run the mean-reversion strategy on public historical candles."""
    bb_period = int(settings["bb_period"])
    rsi_period = int(settings["rsi_period"])
    atr_period = int(settings["atr_period"])
    warmup = max(bb_period, rsi_period, atr_period, 20) + 2
    events: list[tuple[int, str, int]] = []
    for pair, candles in candles_by_pair.items():
        for index in range(warmup, len(candles)):
            events.append((int(candles[index]["close_time"]), pair, index))
    events.sort()

    cash = float(settings["initial_capital"])
    initial_capital = cash
    positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily_entries: dict[str, int] = defaultdict(int)
    equity_curve = [{"time": "start", "value": round(cash, 4)}]
    rejection_counts: dict[str, int] = defaultdict(int)
    latest_prices: dict[str, float] = {}
    peak, drawdown = initial_capital, 0.0
    pair_peaks = {pair: initial_capital for pair in candles_by_pair}
    pair_drawdowns = {pair: 0.0 for pair in candles_by_pair}
    sample_every = max(1, len(events) // 1000)

    def marked_equity() -> float:
        value = cash
        for position in positions:
            price = latest_prices.get(position["pair"], position["entry_rate"])
            value += position["stake"] * (price / position["entry_rate"] - fee)
        return value

    for event_number, (close_time, pair, index) in enumerate(events):
        candles = candles_by_pair[pair]
        candle = candles[index]
        close = float(candle["close"])
        latest_prices[pair] = close
        current_positions = [position for position in positions if position["pair"] == pair]
        for position in current_positions:
            position["high_watermark"] = max(position["high_watermark"], float(candle["high"]))
            exit_reason = None
            if close <= position["entry_rate"] * (1 - float(settings["stop_loss_percent"]) / 100):
                exit_reason = "stop_loss"
            elif position["high_watermark"] >= position["entry_rate"] * (1 + float(settings["trailing_start_percent"]) / 100) and close <= position["high_watermark"] * (1 - float(settings["trailing_distance_percent"]) / 100):
                exit_reason = "trailing_profit"
            if exit_reason:
                gross_return = close / position["entry_rate"] - 1
                profit = position["stake"] * (gross_return - 2 * fee)
                cash += position["stake"] + profit
                positions.remove(position)
                trades.append({"id": f"simulation-{pair}-{close_time}-{len(trades)}", "pair": pair, "status": "closed", "opened_at": position["opened_at"], "closed_at": _iso(close_time), "entry_rate": position["entry_rate"], "exit_rate": close, "stake_amount": position["stake"], "profit_usdt": round(profit, 6), "exit_reason": exit_reason, "raw": {"source": "okx_public_candles", "entry_fee_usdt": round(position["stake"] * fee, 6), "exit_fee_usdt": round(position["stake"] * fee, 6), "gross_return_percent": round(gross_return * 100, 6), "net_return_percent": round((gross_return - 2 * fee) * 100, 6), "holding_candles": index - position["entry_index"]}})

        equity = marked_equity()
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100 if peak else 0)
        pair_value = initial_capital + sum(float(trade["profit_usdt"]) for trade in trades if trade["pair"] == pair)
        pair_value += sum(position["stake"] * (close / position["entry_rate"] - 1 - fee) for position in positions if position["pair"] == pair)
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
        conditions = {
            "pokles pod Bollinger pásmo": float(previous["close"]) < lower_band,
            "RSI nie je prepredané": bool(previous_rsi is not None and previous_rsi < float(settings["rsi_oversold"])),
            "potvrdenie otočenia": close > float(previous["close"]) and close > float(candle["open"]) and float(settings["rebound_min_percent"]) <= rebound_percent <= float(settings["rebound_max_percent"]),
            "ATR volatilita": float(settings["atr_min_percent"]) / 100 <= atr_ratio <= float(settings["atr_max_percent"]) / 100,
            "objem": float(candle["volume"]) >= float(settings["min_volume_ratio"]) * volume_mean and float(candle["volume"]) * close >= float(settings["min_quote_volume_usdt"]),
        }
        if not all(conditions.values()):
            for reason, passed in conditions.items():
                if not passed:
                    rejection_counts[reason] += 1
            continue
        if len(positions) >= int(settings["max_open_trades"]):
            rejection_counts["limit otvorených pozícií"] += 1
            continue
        if daily_entries[day] >= int(settings["daily_trade_limit"]):
            rejection_counts["denný limit obchodov"] += 1
            continue
        stake = min(float(settings["stake_amount"]), cash)
        if stake < 5:
            rejection_counts["nedostatok simulovaného kapitálu"] += 1
            continue
        cash -= stake
        daily_entries[day] += 1
        positions.append({"pair": pair, "entry_rate": close, "stake": stake, "opened_at": _iso(close_time), "high_watermark": close, "entry_index": index})

    if force_close_at_end:
        for position in list(positions):
            pair = position["pair"]
            close = last_close = float(candles_by_pair[pair][-1]["close"])
            close_time = int(candles_by_pair[pair][-1]["close_time"])
            gross_return = close / position["entry_rate"] - 1
            profit = position["stake"] * (gross_return - 2 * fee)
            cash += position["stake"] + profit
            positions.remove(position)
            trades.append({"id": f"simulation-{pair}-{close_time}-{len(trades)}", "pair": pair, "status": "closed", "opened_at": position["opened_at"], "closed_at": _iso(close_time), "entry_rate": position["entry_rate"], "exit_rate": last_close, "stake_amount": position["stake"], "profit_usdt": round(profit, 6), "exit_reason": "end_of_test", "raw": {"source": "okx_public_candles", "entry_fee_usdt": round(position["stake"] * fee, 6), "exit_fee_usdt": round(position["stake"] * fee, 6), "gross_return_percent": round(gross_return * 100, 6), "net_return_percent": round((gross_return - 2 * fee) * 100, 6), "holding_candles": len(candles_by_pair[pair]) - 1 - position["entry_index"]}})
        final_time = max((int(candles[-1]["close_time"]) for candles in candles_by_pair.values() if candles), default=0)
        if final_time:
            equity_curve.append({"time": _iso(final_time), "value": round(cash, 4)})
            peak = max(peak, cash)
            drawdown = max(drawdown, (peak - cash) / peak * 100 if peak else 0)

    last_prices = {pair: float(candles[-1]["close"]) for pair, candles in candles_by_pair.items() if candles}
    unrealized = sum(position["stake"] * (last_prices[position["pair"]] / position["entry_rate"] - 1 - fee) for position in positions)
    portfolio_value = cash + sum(position["stake"] for position in positions) + unrealized
    wins = sum(1 for trade in trades if trade["profit_usdt"] > 0)
    per_pair_metrics: dict[str, dict[str, float | int]] = {}
    per_pair_equity_curves: dict[str, list[dict[str, Any]]] = {}
    for pair in candles_by_pair:
        pair_trades = sorted((trade for trade in trades if trade["pair"] == pair), key=lambda trade: trade.get("closed_at") or "")
        pair_realized = sum(float(trade["profit_usdt"]) for trade in pair_trades)
        pair_unrealized = sum(position["stake"] * (last_prices[pair] / position["entry_rate"] - 1 - fee) for position in positions if position["pair"] == pair and pair in last_prices)
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
    return {"trades": trades, "open_positions": len(positions), "metrics": {"initial_capital": initial_capital, "portfolio_value": round(portfolio_value, 4), "realized_profit": round(sum(item["profit_usdt"] for item in trades), 4), "unrealized_profit": round(unrealized, 4), "closed_trades": len(trades), "win_rate": round(wins / len(trades) * 100, 1) if trades else 0, "max_drawdown_percent": round(drawdown, 2)}, "per_pair_metrics": per_pair_metrics, "per_pair_equity_curves": per_pair_equity_curves, "equity_curve": equity_curve, "rejections": dict(sorted(rejection_counts.items(), key=lambda item: item[1], reverse=True)[:8])}
