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

    signals_in_fold = {
        ts: side for ts, side in signals.items()
        if evaluation_start_ms <= ts <= evaluation_end_ms
    }
    eval_signal_times = {
        ts: side for ts, side in signals_in_fold.items()
        if ts + 1 <= evaluation_end_ms
    }

    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    raw_signal_count = len(eval_signal_times)
    ignored_signals_while_open = 0
    unexecutable_end_signals = len(signals_in_fold) - len(eval_signal_times)
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

    if int(eval_rows[0]["open_time"]) != evaluation_start_ms:
        raise ValueError("evaluation_start_mismatch")
    if any(
        int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS
        for a, b in zip(eval_rows, eval_rows[1:])
    ):
        raise ValueError("evaluation_5m_gap")
    eval_open_times = {int(candle["open_time"]) for candle in eval_rows}
    for signal_close_time in eval_signal_times:
        required_entry_open = signal_close_time + 1
        if required_entry_open not in eval_open_times:
            raise ValueError("missing_required_entry_bar")

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

    # Locked BTC overlap diagnostic / PASS gate.
    # For each taken ZEC trade require complete BTC 1h data at T-1h, T and T+1h.
    btc_hour_times = {bar.close_time for bar in btc_hourly}
    btc_overlap = 0
    btc_overlap_denominator = 0
    btc_overlap_dropped = 0
    for trade in trades:
        ts = int(trade["signal_close_time"])
        required = (ts - ONE_HOUR_MS, ts, ts + ONE_HOUR_MS)
        if any(required_ts not in btc_hour_times for required_ts in required):
            btc_overlap_dropped += 1
            continue
        btc_overlap_denominator += 1
        if any(btc_signals.get(required_ts) == trade["side"] for required_ts in required):
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
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 8)
        profit_factor_infinite = False
        profit_factor_defined = True
    elif gross_profit > 0:
        profit_factor = None
        profit_factor_infinite = True
        profit_factor_defined = True
    else:
        profit_factor = None
        profit_factor_infinite = False
        profit_factor_defined = False

    metrics = {
        "signals_1h": raw_signal_count,
        "ignored_signals_while_open": ignored_signals_while_open,
        "unexecutable_end_signals": unexecutable_end_signals,
        "closed_trades": len(trades),
        "net_pnl_usdt": round(net_pnl, 8),
        "net_expectancy_usdt_per_trade": round(net_pnl / len(trades), 8) if trades else None,
        "profit_factor": profit_factor,
        "profit_factor_infinite": profit_factor_infinite,
        "profit_factor_defined": profit_factor_defined,
        "gross_profit_usdt": round(gross_profit, 8),
        "gross_loss_usdt": round(gross_loss, 8),
        "long": side_metrics(longs),
        "short": side_metrics(shorts),
        "max_drawdown_percent": round(max_drawdown, 6),
        "time_in_market_percent": round(100 * total_hold_minutes / eval_minutes, 6) if eval_minutes else None,
        "average_hold_minutes": round(sum(float(t["hold_minutes"]) for t in trades) / len(trades), 6) if trades else None,
        "btc_same_direction_overlap_count": btc_overlap,
        "btc_same_direction_overlap_denominator": btc_overlap_denominator,
        "btc_overlap_dropped_trades": btc_overlap_dropped,
        "btc_overlap_dropped_percent_of_taken": round(
            100 * btc_overlap_dropped / len(trades), 6
        ) if trades else 0.0,
        "btc_same_direction_overlap_percent": round(
            100 * btc_overlap / btc_overlap_denominator, 6
        ) if btc_overlap_denominator else None,
        "win_rate_percent": round(100 * len(winners) / len(trades), 4) if trades else None,
    }

    overlap_missing_too_high = (
        len(trades) > 0
        and metrics["btc_overlap_dropped_percent_of_taken"] > 10
    )
    if len(trades) < 20 or overlap_missing_too_high:
        verdict = "INSUFFICIENT_SAMPLE"
        conditions = None
    else:
        long_clause = True
        if longs and shorts:
            long_clause = metrics["long"]["net_pnl_usdt"] > 0 and metrics["short"]["net_pnl_usdt"] > 0
        conditions = {
            "expectancy_positive": metrics["net_expectancy_usdt_per_trade"] is not None and metrics["net_expectancy_usdt_per_trade"] > 0,
            "profit_factor_at_least_1_10": (
                metrics["profit_factor_infinite"]
                or (
                    metrics["profit_factor"] is not None
                    and metrics["profit_factor"] >= 1.10
                )
            ),
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
    """Synthetic-only checks. Never touches the official evaluation fold."""
    def candle(ts: int, o: float, h: float, l: float, c: float) -> dict[str, Any]:
        return {
            "open_time": ts,
            "close_time": ts + FIVE_MIN_MS - 1,
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1.0, "quote_volume": 1.0,
        }

    # UTC 1h aggregation.
    rows = []
    for i in range(24):
        ts = i * FIVE_MIN_MS
        price = 100 + i * .01
        rows.append(candle(ts, price, price + .1, price - .1, price))
    hourly = aggregate_1h_ohlc(rows)
    utc_hour = len(hourly) == 2 and hourly[0].open_time == 0 and hourly[1].open_time == ONE_HOUR_MS

    # Signal excludes current bar.
    hbars = [
        HourBar(i * ONE_HOUR_MS, (i + 1) * ONE_HOUR_MS - 1, 100, 101, 99, 100)
        for i in range(24)
    ]
    hbars.append(HourBar(24 * ONE_HOUR_MS, 25 * ONE_HOUR_MS - 1, 100, 102, 100, 101.5))
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

    # ATR at signal close includes the fully closed signal bar TR.
    atr_signal_bars = [
        HourBar(i * ONE_HOUR_MS, (i + 1) * ONE_HOUR_MS - 1, 100, 101, 99, 100)
        for i in range(25)
    ]
    atr_signal_bars.append(
        HourBar(25 * ONE_HOUR_MS, 26 * ONE_HOUR_MS - 1, 100, 110, 100, 109)
    )
    atr_signal = wilder_atr(atr_signal_bars)
    expected_signal_atr = ((23 * 2.0) + 10.0) / 24.0
    atr_includes_signal_bar = abs(
        atr_signal[26 * ONE_HOUR_MS - 1] - expected_signal_atr
    ) < 1e-12

    profile = {"entry_cost_rate": .0011, "exit_cost_rate": .0011}
    costs_both_sides = long_net_return(100, 100, profile) < 0 and short_net_return(100, 100, profile) < 0

    # Full synthetic engine path: 24h warmup, one breakout signal in the
    # first evaluation hour, entry exactly at the next 5m open.
    synthetic: list[dict[str, Any]] = []
    for hour in range(26):
        for j in range(12):
            ts = hour * ONE_HOUR_MS + j * FIVE_MIN_MS
            if hour < 24:
                synthetic.append(candle(ts, 100, 101, 99, 100))
            elif hour == 24:
                close = 102.0 if j == 11 else 100.0
                high = 102.2 if j == 11 else 101.0
                synthetic.append(candle(ts, 100, high, 99.5, close))
            else:
                if j == 0:
                    synthetic.append(candle(ts, 102.5, 103.0, 102.0, 102.4))
                else:
                    synthetic.append(candle(ts, 102.4, 102.8, 101.5, 101.8))

    eval_start = 24 * ONE_HOUR_MS
    eval_end = 26 * ONE_HOUR_MS - 1
    engine = simulate_b_v1(
        synthetic, synthetic,
        evaluation_start_ms=eval_start,
        evaluation_end_ms=eval_end,
        stake_amount=50,
        initial_capital=100,
        cost_profile=profile,
    )
    next_5m_entry = (
        len(engine["trades"]) == 1
        and engine["trades"][0]["entry_time"] == 25 * ONE_HOUR_MS
        and abs(engine["trades"][0]["entry_price"] - 102.5) < 1e-12
    )
    end_of_test_counted = (
        len(engine["trades"]) == 1
        and engine["trades"][0]["exit_reason"] == "end_of_test"
        and engine["metrics"]["closed_trades"] == 1
        and engine["metrics"]["net_expectancy_usdt_per_trade"] is not None
    )

    # Intrabar ordering: current candle makes a large new high, but its low is
    # above the old stop and below the newly implied stop. It must survive this
    # candle and may only gap through the tightened stop on the next 5m open.
    retro = [dict(row) for row in synthetic]
    first_entry_idx = next(i for i, row in enumerate(retro) if int(row["open_time"]) == 25 * ONE_HOUR_MS)
    retro[first_entry_idx].update({"open": 102.5, "high": 110.0, "low": 100.0, "close": 105.0})
    retro[first_entry_idx + 1].update({"open": 105.0, "high": 105.5, "low": 104.5, "close": 105.0})
    retro_engine = simulate_b_v1(
        retro, retro,
        evaluation_start_ms=eval_start,
        evaluation_end_ms=eval_end,
        stake_amount=50,
        initial_capital=100,
        cost_profile=profile,
    )
    intrabar_no_retro = (
        len(retro_engine["trades"]) >= 1
        and retro_engine["trades"][0]["exit_reason"] == "chandelier_stop_gap"
        and retro_engine["trades"][0]["exit_time"] == 25 * ONE_HOUR_MS + FIVE_MIN_MS
    )

    invalid_final_rejected = False
    bad = [dict(row) for row in synthetic]
    bad[-1]["close"] = float("nan")
    try:
        simulate_b_v1(
            bad, bad,
            evaluation_start_ms=eval_start,
            evaluation_end_ms=eval_end,
            stake_amount=50,
            initial_capital=100,
            cost_profile=profile,
        )
    except ValueError as exc:
        invalid_final_rejected = str(exc) == "invalid_final_5m_close"

    missing_entry_rejected = False
    missing_entry = [
        dict(row) for row in synthetic
        if int(row["open_time"]) != 25 * ONE_HOUR_MS
    ]
    try:
        simulate_b_v1(
            missing_entry, synthetic,
            evaluation_start_ms=eval_start,
            evaluation_end_ms=eval_end,
            stake_amount=50,
            initial_capital=100,
            cost_profile=profile,
        )
    except ValueError as exc:
        missing_entry_rejected = str(exc) in {
            "evaluation_5m_gap",
            "missing_required_entry_bar",
        }

    # Missing BTC 1h data in T-1/T/T+1 drops the taken trade from both
    # overlap numerator and denominator and is surfaced in metrics.
    btc_missing = [
        dict(row) for row in synthetic
        if not (24 * ONE_HOUR_MS <= int(row["open_time"]) < 25 * ONE_HOUR_MS)
    ]
    btc_missing_outcome = simulate_b_v1(
        synthetic, btc_missing,
        evaluation_start_ms=eval_start,
        evaluation_end_ms=eval_end,
        stake_amount=50,
        initial_capital=100,
        cost_profile=profile,
    )
    btc_missing_accounted = (
        btc_missing_outcome["metrics"]["btc_overlap_dropped_trades"] == 1
        and btc_missing_outcome["metrics"]["btc_same_direction_overlap_denominator"] == 0
        and btc_missing_outcome["verdict"] == "INSUFFICIENT_SAMPLE"
    )

    return {
        "utc_1h_aggregation": utc_hour,
        "breakout_excludes_current_bar": breakout_excludes_current,
        "wilder_atr_24": wilder_ready,
        "atr_includes_signal_bar": atr_includes_signal_bar,
        "long_short_costs": costs_both_sides,
        "entry_on_next_5m_open": next_5m_entry,
        "end_of_test_accounted": end_of_test_counted,
        "intrabar_no_retroactive_stop": intrabar_no_retro,
        "invalid_final_close_rejected": invalid_final_rejected,
        "missing_required_entry_bar_rejected": missing_entry_rejected,
        "btc_missing_overlap_accounted": btc_missing_accounted,
    }
