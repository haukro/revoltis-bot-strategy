"""TEST-SPEC-002 — ZEC TSMOM B v1.

Standalone research engine. It does not reuse Strategy A entry/exit logic.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any

FIVE_MIN_MS = 300_000
ONE_HOUR_MS = 3_600_000
ATR_PERIOD = 24
BREAKOUT_N = 24
ATR_MULTIPLE = 2.0


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=False).encode()).hexdigest()


def pack_ohlc_snapshot(candles: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        [
            int(c["open_time"]), int(c["close_time"]),
            float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]),
        ]
        for c in candles
    ]
    raw = json.dumps(rows, separators=(",", ":")).encode()
    return {
        "encoding": "gzip+base64",
        "count": len(rows),
        "sha256": _digest(rows),
        "data": base64.b64encode(gzip.compress(raw, mtime=0)).decode(),
    }


def unpack_ohlc_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if snapshot.get("encoding") != "gzip+base64":
        raise ValueError("invalid_snapshot_encoding")
    rows = json.loads(gzip.decompress(base64.b64decode(snapshot["data"])))
    if len(rows) != int(snapshot["count"]) or _digest(rows) != snapshot["sha256"]:
        raise ValueError("snapshot_integrity_error")
    candles = [
        {
            "open_time": int(row[0]), "close_time": int(row[1]),
            "open": float(row[2]), "high": float(row[3]),
            "low": float(row[4]), "close": float(row[5]),
        }
        for row in rows
    ]
    if not candles or any(int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS for a, b in zip(candles, candles[1:])):
        raise ValueError("non_contiguous_5m_snapshot")
    return candles


@dataclass(frozen=True)
class HourBar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float


def aggregate_1h_ohlc(candles: list[dict[str, Any]]) -> list[HourBar]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for candle in candles:
        bucket = (int(candle["open_time"]) // ONE_HOUR_MS) * ONE_HOUR_MS
        buckets.setdefault(bucket, []).append(candle)
    out: list[HourBar] = []
    for bucket in sorted(buckets):
        rows = sorted(buckets[bucket], key=lambda row: int(row["open_time"]))
        if len(rows) != 12:
            continue
        if int(rows[0]["open_time"]) != bucket:
            continue
        if int(rows[-1]["close_time"]) != bucket + ONE_HOUR_MS - 1:
            continue
        if any(int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS for a, b in zip(rows, rows[1:])):
            continue
        values = [
            float(rows[0]["open"]),
            max(float(row["high"]) for row in rows),
            min(float(row["low"]) for row in rows),
            float(rows[-1]["close"]),
        ]
        if any(not isfinite(value) or value <= 0 for value in values):
            raise ValueError("invalid_1h_ohlc")
        out.append(HourBar(
            open_time=bucket,
            close_time=bucket + ONE_HOUR_MS - 1,
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
        ))
    return out


def wilder_atr(hourly: list[HourBar], period: int = ATR_PERIOD) -> dict[int, float]:
    """Map hourly close_time -> ATR known at that close."""
    trs: list[tuple[int, float]] = []
    for i in range(1, len(hourly)):
        cur = hourly[i]
        prev = hourly[i - 1]
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        trs.append((cur.close_time, tr))
    if len(trs) < period:
        return {}
    result: dict[int, float] = {}
    atr = sum(value for _, value in trs[:period]) / period
    result[trs[period - 1][0]] = atr
    for close_time, tr in trs[period:]:
        atr = ((period - 1) * atr + tr) / period
        result[close_time] = atr
    return result


def hourly_signals(hourly: list[HourBar], lookback: int = BREAKOUT_N) -> dict[int, str]:
    """Map signal-hour close_time -> long/short. Signal candle excluded from range."""
    signals: dict[int, str] = {}
    for i in range(lookback, len(hourly)):
        cur = hourly[i]
        prior = hourly[i - lookback:i]
        range_high = max(bar.high for bar in prior)
        range_low = min(bar.low for bar in prior)
        long_signal = cur.close > range_high
        short_signal = cur.close < range_low
        if long_signal and short_signal:
            continue
        if long_signal:
            signals[cur.close_time] = "long"
        elif short_signal:
            signals[cur.close_time] = "short"
    return signals


def long_net_return(entry: float, exit: float, profile: dict[str, Any]) -> float:
    ratio = exit / entry
    return ratio * (1 - float(profile["exit_cost_rate"])) / (1 + float(profile["entry_cost_rate"])) - 1


def short_net_return(entry: float, exit: float, profile: dict[str, Any]) -> float:
    ratio = exit / entry
    return 1 - ratio * (1 + float(profile["entry_cost_rate"])) / (1 - float(profile["exit_cost_rate"]))


def _position_return(side: str, entry: float, exit: float, profile: dict[str, Any]) -> float:
    return long_net_return(entry, exit, profile) if side == "long" else short_net_return(entry, exit, profile)


def _hour_close_before(open_time: int) -> int:
    """Most recent fully closed UTC hour available before this 5m open."""
    current_hour = (open_time // ONE_HOUR_MS) * ONE_HOUR_MS
    return current_hour - 1


def _valid_price(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number > 0


def simulate_b_v1(
    zec_5m: list[dict[str, Any]],
    btc_5m: list[dict[str, Any]],
    *,
    evaluation_start_ms: int,
    evaluation_end_ms: int,
    stake_amount: float,
    initial_capital: float,
    cost_profile: dict[str, Any],
) -> dict[str, Any]:
    """Run locked TEST-SPEC-002 on one official fold."""
    if not zec_5m:
        raise ValueError("empty_zec_feed")
    if int(zec_5m[-1]["close_time"]) != evaluation_end_ms:
        raise ValueError("evaluation_end_mismatch")
    if not _valid_price(zec_5m[-1].get("close")):
        raise ValueError("invalid_final_5m_close")

    hourly = aggregate_1h_ohlc(zec_5m)
    btc_hourly = aggregate_1h_ohlc(btc_5m)
    atr_by_close = wilder_atr(hourly)
    signals = hourly_signals(hourly)
    btc_signals = hourly_signals(btc_hourly)

    eval_signal_times = {
        ts: side for ts, side in signals.items()
        if evaluation_start_ms <= ts <= evaluation_end_ms
    }

    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    raw_signal_count = len(eval_signal_times)
    ignored_signals_while_open = 0
    unexecutable_end_signals = 0
    equity_peak = cash
    max_drawdown = 0.0
    in_market_ms = 0
    last_open_time: int | None = None

    def current_equity(mark_price: float) -> float:
        if position is None:
            return cash
        pnl = position["stake"] * _position_return(
            position["side"], position["entry_price"], mark_price, cost_profile
        )
        return cash + position["stake"] + pnl

    def close_position(exit_price: float, exit_time: int, reason: str) -> None:
        nonlocal cash, position
        if position is None:
            return
        ret = _position_return(position["side"], position["entry_price"], exit_price, cost_profile)
        pnl = position["stake"] * ret
        cash += position["stake"] + pnl
        hold_minutes = (exit_time - position["entry_time"]) / 60_000
        trades.append({
            "side": position["side"],
            "signal_close_time": position["signal_close_time"],
            "entry_time": position["entry_time"],
            "entry_price": position["entry_price"],
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason": reason,
            "stake_amount": position["stake"],
            "pnl_net": pnl,
            "net_return_percent": ret * 100,
            "hold_minutes": hold_minutes,
            "entry_atr": position["entry_atr"],
        })
        position = None

    eval_rows = [
        candle for candle in zec_5m
        if int(candle["open_time"]) >= evaluation_start_ms
        and int(candle["close_time"]) <= evaluation_end_ms
    ]
    if not eval_rows:
        raise ValueError("empty_evaluation_rows")

    for idx, candle in enumerate(eval_rows):
        open_time = int(candle["open_time"])
        close_time = int(candle["close_time"])
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])
        if any(not isfinite(v) or v <= 0 for v in (o, h, l, close)):
            raise ValueError("invalid_5m_ohlc")

        if position is not None and last_open_time is not None:
            in_market_ms += open_time - last_open_time

        # Signal from the immediately preceding fully closed 1h candle.
        available_hour_close = _hour_close_before(open_time)
        signal_side = (
            eval_signal_times.get(available_hour_close)
            if open_time == available_hour_close + 1
            else None
        )

        # Whether the position existed when the signal became actionable is fixed
        # before any stop/gap processing on this 5m candle.
        had_position_at_bar_start = position is not None
        if signal_side is not None and had_position_at_bar_start:
            ignored_signals_while_open += 1

        # Existing-position stop is frozen before this candle's high/low.
        if position is not None:
            latest_atr = atr_by_close.get(available_hour_close)
            if latest_atr is not None:
                if position["side"] == "long":
                    candidate = position["peak_high"] - ATR_MULTIPLE * latest_atr
                    position["stop"] = max(position["stop"], candidate)
                else:
                    candidate = position["trough_low"] + ATR_MULTIPLE * latest_atr
                    position["stop"] = min(position["stop"], candidate)

            active_stop = float(position["stop"])
            if position["side"] == "long":
                if o < active_stop:
                    close_position(o, open_time, "chandelier_stop_gap")
                elif l <= active_stop:
                    close_position(active_stop, close_time, "chandelier_stop")
            else:
                if o > active_stop:
                    close_position(o, open_time, "chandelier_stop_gap")
                elif h >= active_stop:
                    close_position(active_stop, close_time, "chandelier_stop")

        # Entry may occur only if the signal was not blocked by an already-open
        # position at the start of this 5m candle.
        entered_this_bar = False
        if signal_side is not None and not had_position_at_bar_start and position is None:
            atr = atr_by_close.get(available_hour_close)
            if atr is None or not isfinite(atr) or atr <= 0:
                raise ValueError("signal_without_valid_atr")
            stake = min(float(stake_amount), cash)
            if stake <= 0:
                raise ValueError("insufficient_capital")
            cash -= stake
            if signal_side == "long":
                stop = o - ATR_MULTIPLE * atr
            else:
                stop = o + ATR_MULTIPLE * atr
            position = {
                "side": signal_side,
                "signal_close_time": available_hour_close,
                "entry_time": open_time,
                "entry_price": o,
                "entry_atr": atr,
                "stake": stake,
                "stop": stop,
                "peak_high": o,
                "trough_low": o,
            }
            entered_this_bar = True

            # Initial stop is active immediately from the entry 5m open.
            if signal_side == "long" and l <= stop:
                close_position(stop, close_time, "initial_stop")
            elif signal_side == "short" and h >= stop:
                close_position(stop, close_time, "initial_stop")

        # Only surviving positions observe this candle's extrema for NEXT bar.
        if position is not None:
            position["peak_high"] = max(float(position["peak_high"]), h)
            position["trough_low"] = min(float(position["trough_low"]), l)

        equity = current_equity(close)
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            max_drawdown = max(max_drawdown, (equity_peak - equity) / equity_peak * 100)

        last_open_time = open_time

    # Count position exposure through the last candle duration.
    if position is not None and last_open_time is not None:
        in_market_ms += FIVE_MIN_MS

    # A signal closing exactly at fold end cannot enter because the next 5m open
    # lies outside the evaluation fold.
    if evaluation_end_ms in eval_signal_times:
        unexecutable_end_signals += 1

    # Locked end-of-test bookkeeping. No further stop/extrema update here.
    if position is not None:
        final_close = float(eval_rows[-1]["close"])
        if not isfinite(final_close) or final_close <= 0:
            raise ValueError("invalid_final_5m_close")
        close_position(final_close, evaluation_end_ms, "end_of_test")

    profits = [float(t["pnl_net"]) for t in trades]
    winners = [p for p in profits if p > 0]
    losers = [-p for p in profits if p < 0]
    gross_profit = sum(winners)
    gross_loss = sum(losers)
    net_pnl = sum(profits)

    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]

    # BTC overlap is diagnostic only and may include +/- one 1h signal bar.
    btc_index = {bar.close_time: i for i, bar in enumerate(btc_hourly)}
    btc_signal_by_index = {
        btc_index[ts]: side
        for ts, side in btc_signals.items()
        if ts in btc_index
    }
    btc_overlap = 0
    for trade in trades:
        ts = int(trade["signal_close_time"])
        if ts not in btc_index:
            continue
        i = btc_index[ts]
        if any(btc_signal_by_index.get(j) == trade["side"] for j in (i - 1, i, i + 1)):
            btc_overlap += 1

    def side_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        pnl = sum(float(t["pnl_net"]) for t in items)
        return {
            "trades": len(items),
            "net_pnl_usdt": round(pnl, 8),
            "expectancy_usdt": round(pnl / len(items), 8) if items else None,
            "win_rate_percent": round(100 * sum(float(t["pnl_net"]) > 0 for t in items) / len(items), 4) if items else None,
        }

    eval_minutes = (evaluation_end_ms - evaluation_start_ms + 1) / 60_000
    total_hold_minutes = sum(float(t["hold_minutes"]) for t in trades)
    metrics = {
        "signals_1h": raw_signal_count,
        "ignored_signals_while_open": ignored_signals_while_open,
        "unexecutable_end_signals": unexecutable_end_signals,
        "closed_trades": len(trades),
        "net_pnl_usdt": round(net_pnl, 8),
        "net_expectancy_usdt_per_trade": round(net_pnl / len(trades), 8) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 8) if gross_loss > 0 else None,
        "gross_profit_usdt": round(gross_profit, 8),
        "gross_loss_usdt": round(gross_loss, 8),
        "long": side_metrics(longs),
        "short": side_metrics(shorts),
        "max_drawdown_percent": round(max_drawdown, 6),
        "time_in_market_percent": round(100 * total_hold_minutes / eval_minutes, 6) if eval_minutes else None,
        "average_hold_minutes": round(sum(float(t["hold_minutes"]) for t in trades) / len(trades), 6) if trades else None,
        "btc_same_direction_overlap_count": btc_overlap,
        "btc_same_direction_overlap_percent": round(100 * btc_overlap / len(trades), 6) if trades else None,
        "win_rate_percent": round(100 * len(winners) / len(trades), 4) if trades else None,
    }

    if len(trades) < 20:
        verdict = "INSUFFICIENT_SAMPLE"
        conditions = None
    else:
        long_clause = True
        if longs and shorts:
            long_clause = metrics["long"]["net_pnl_usdt"] > 0 and metrics["short"]["net_pnl_usdt"] > 0
        conditions = {
            "expectancy_positive": metrics["net_expectancy_usdt_per_trade"] is not None and metrics["net_expectancy_usdt_per_trade"] > 0,
            "profit_factor_at_least_1_10": metrics["profit_factor"] is not None and metrics["profit_factor"] >= 1.10,
            "both_active_sides_profitable": long_clause,
            "btc_overlap_below_60_percent": metrics["btc_same_direction_overlap_percent"] is not None and metrics["btc_same_direction_overlap_percent"] < 60,
        }
        verdict = "PASS" if all(conditions.values()) else "FAIL"

    return {
        "metrics": metrics,
        "trades": trades,
        "conditions": conditions,
        "verdict": verdict,
    }


def smoke_cases() -> dict[str, bool]:
    """Synthetic-only checks. No official evaluation data."""
    def candle(ts: int, o: float, h: float, l: float, c: float) -> dict[str, Any]:
        return {
            "open_time": ts,
            "close_time": ts + FIVE_MIN_MS - 1,
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1.0, "quote_volume": 1.0,
        }

    # UTC 1h aggregation and next-5m execution geometry.
    rows = []
    for i in range(24):
        ts = i * FIVE_MIN_MS
        price = 100 + i * .01
        rows.append(candle(ts, price, price + .1, price - .1, price))
    hourly = aggregate_1h_ohlc(rows)
    utc_hour = len(hourly) == 2 and hourly[0].open_time == 0 and hourly[1].open_time == ONE_HOUR_MS

    # Signal excludes current bar.
    hbars = [
        HourBar(i * ONE_HOUR_MS, (i + 1) * ONE_HOUR_MS - 1, 100, 100, 99, 100)
        for i in range(24)
    ]
    hbars.append(HourBar(24 * ONE_HOUR_MS, 25 * ONE_HOUR_MS - 1, 100, 102, 100, 101))
    sig = hourly_signals(hbars)
    breakout_excludes_current = sig.get(25 * ONE_HOUR_MS - 1) == "long"

    # Wilder ATR known only at close after 24 TR observations.
    atr_bars = [
        HourBar(i * ONE_HOUR_MS, (i + 1) * ONE_HOUR_MS - 1, 100, 101, 99, 100)
        for i in range(25)
    ]
    atr = wilder_atr(atr_bars)
    first_atr_time = 25 * ONE_HOUR_MS - 1
    wilder_ready = len(atr) == 1 and abs(atr[first_atr_time] - 2.0) < 1e-12

    # Cost accounting round-trip must be negative at unchanged price.
    profile = {"entry_cost_rate": .0011, "exit_cost_rate": .0011}
    costs_both_sides = long_net_return(100, 100, profile) < 0 and short_net_return(100, 100, profile) < 0

    # Intrabar rule: a high can only tighten stop for the next candle.
    # We test the ordering primitive directly: old stop is not replaced until after hit check.
    old_stop = 95.0
    high = 110.0
    low = 96.0
    atr_value = 5.0
    newly_computed = high - 2 * atr_value  # 100
    no_same_bar_retro_stop = low > old_stop and low <= newly_computed

    return {
        "utc_1h_aggregation": utc_hour,
        "breakout_excludes_current_bar": breakout_excludes_current,
        "wilder_atr_24": wilder_ready,
        "long_short_costs": costs_both_sides,
        "intrabar_no_retroactive_stop": no_same_bar_retro_stop,
    }
