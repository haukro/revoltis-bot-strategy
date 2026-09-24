"""TEST-SPEC-003 — BTC TSMOM C v1.

Standalone Strategy C replication engine. It must not modify TEST-SPEC-002 behavior.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from typing import Any

FIVE_MIN_MS = 300_000
ONE_HOUR_MS = 3_600_000
BREAKOUT_N = 24
ATR_PERIOD = 24
ATR_MULTIPLE = 2.0


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=False).encode()
    ).hexdigest()


def pack_ohlc_snapshot(candles: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        [
            int(c["open_time"]),
            int(c["close_time"]),
            float(c["open"]),
            float(c["high"]),
            float(c["low"]),
            float(c["close"]),
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
    return [
        {
            "open_time": int(row[0]),
            "close_time": int(row[1]),
            "open": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "close": float(row[5]),
        }
        for row in rows
    ]


@dataclass(frozen=True)
class HourBar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float


def _is_contiguous_5m(candles: list[dict[str, Any]]) -> bool:
    return bool(candles) and all(
        int(b["open_time"]) - int(a["open_time"]) == FIVE_MIN_MS
        for a, b in zip(candles, candles[1:])
    )


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
        if any(
            int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS
            for a, b in zip(rows, rows[1:])
        ):
            continue
        values = [
            float(rows[0]["open"]),
            max(float(row["high"]) for row in rows),
            min(float(row["low"]) for row in rows),
            float(rows[-1]["close"]),
        ]
        if any(not isfinite(value) or value <= 0 for value in values):
            raise ValueError("invalid_1h_ohlc")
        out.append(
            HourBar(
                open_time=bucket,
                close_time=bucket + ONE_HOUR_MS - 1,
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
            )
        )
    return out


def wilder_atr(hourly: list[HourBar], period: int = ATR_PERIOD) -> dict[int, float]:
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

    atr = sum(value for _, value in trs[:period]) / period
    result = {trs[period - 1][0]: atr}
    for close_time, tr in trs[period:]:
        atr = ((period - 1) * atr + tr) / period
        result[close_time] = atr
    return result


def hourly_signals(hourly: list[HourBar], lookback: int = BREAKOUT_N) -> dict[int, str]:
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
    return (
        ratio
        * (1 - float(profile["exit_cost_rate"]))
        / (1 + float(profile["entry_cost_rate"]))
        - 1
    )


def short_net_return(entry: float, exit: float, profile: dict[str, Any]) -> float:
    ratio = exit / entry
    return 1 - (
        ratio
        * (1 + float(profile["entry_cost_rate"]))
        / (1 - float(profile["exit_cost_rate"]))
    )


def _position_return(
    side: str,
    entry: float,
    exit: float,
    profile: dict[str, Any],
) -> float:
    if side == "long":
        return long_net_return(entry, exit, profile)
    return short_net_return(entry, exit, profile)


def _hour_close_before(open_time: int) -> int:
    current_hour = (open_time // ONE_HOUR_MS) * ONE_HOUR_MS
    return current_hour - 1


def _valid_price(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number > 0


def continuity_certificate(
    btc_5m: list[dict[str, Any]],
    eth_5m: list[dict[str, Any]],
    *,
    evaluation_start_ms: int,
    evaluation_end_ms: int,
    btc_warmup_start_ms: int,
    eth_warmup_start_ms: int,
) -> dict[str, Any]:
    """Data/instrumentation certificate only. No trade PnL is computed."""
    expected_btc_5m = (evaluation_end_ms - btc_warmup_start_ms + 1) // FIVE_MIN_MS
    expected_eth_5m = (evaluation_end_ms - eth_warmup_start_ms + 1) // FIVE_MIN_MS

    btc_hourly = aggregate_1h_ohlc(btc_5m)
    eth_hourly = aggregate_1h_ohlc(eth_5m)

    expected_btc_1h = (evaluation_end_ms - btc_warmup_start_ms + 1) // ONE_HOUR_MS
    expected_eth_1h = (evaluation_end_ms - eth_warmup_start_ms + 1) // ONE_HOUR_MS

    btc_signals = hourly_signals(btc_hourly)
    eligible_signals = {
        ts: side
        for ts, side in btc_signals.items()
        if ts >= evaluation_start_ms and ts + 1 <= evaluation_end_ms
    }
    btc_open_times = {int(row["open_time"]) for row in btc_5m}
    missing_entry_bars = [
        ts + 1
        for ts in eligible_signals
        if ts + 1 not in btc_open_times
    ]

    eth_hour_times = {bar.close_time for bar in eth_hourly}
    eth_missing_windows = 0
    for ts in eligible_signals:
        required = (ts - ONE_HOUR_MS, ts, ts + ONE_HOUR_MS)
        if any(required_ts not in eth_hour_times for required_ts in required):
            eth_missing_windows += 1

    btc_first = int(btc_5m[0]["open_time"]) if btc_5m else None
    btc_last = int(btc_5m[-1]["close_time"]) if btc_5m else None
    eth_first = int(eth_5m[0]["open_time"]) if eth_5m else None
    eth_last = int(eth_5m[-1]["close_time"]) if eth_5m else None

    btc_final_close_valid = (
        bool(btc_5m)
        and btc_last == evaluation_end_ms
        and _valid_price(btc_5m[-1].get("close"))
    )

    btc_execution_data_pass = all(
        [
            len(btc_5m) == expected_btc_5m,
            btc_first == btc_warmup_start_ms,
            btc_last == evaluation_end_ms,
            _is_contiguous_5m(btc_5m),
            len(btc_hourly) == expected_btc_1h,
            not missing_entry_bars,
            btc_final_close_valid,
        ]
    )

    return {
        "certificate_kind": "tsmom_c_v1_continuity_only",
        "strategy_metrics_computed": False,
        "btc": {
            "warmup_start_ms": btc_warmup_start_ms,
            "evaluation_start_ms": evaluation_start_ms,
            "evaluation_end_ms": evaluation_end_ms,
            "actual_5m_bars": len(btc_5m),
            "expected_5m_bars": expected_btc_5m,
            "actual_1h_bars": len(btc_hourly),
            "expected_1h_bars": expected_btc_1h,
            "first_open_time": btc_first,
            "last_close_time": btc_last,
            "contiguous_5m": _is_contiguous_5m(btc_5m),
            "eligible_signal_count_for_entry_availability_only": len(eligible_signals),
            "missing_required_entry_bars": len(missing_entry_bars),
            "missing_required_entry_open_times": missing_entry_bars[:20],
            "final_5m_close_valid": btc_final_close_valid,
            "execution_data_pass": btc_execution_data_pass,
        },
        "eth_diagnostic": {
            "warmup_start_ms": eth_warmup_start_ms,
            "actual_5m_bars": len(eth_5m),
            "expected_5m_bars": expected_eth_5m,
            "actual_1h_bars": len(eth_hourly),
            "expected_1h_bars": expected_eth_1h,
            "first_open_time": eth_first,
            "last_close_time": eth_last,
            "contiguous_5m": _is_contiguous_5m(eth_5m),
            "eligible_btc_signals_missing_complete_eth_pm1h_window": eth_missing_windows,
            "diagnostic_complete": (
                len(eth_5m) == expected_eth_5m
                and eth_first == eth_warmup_start_ms
                and eth_last == evaluation_end_ms
                and _is_contiguous_5m(eth_5m)
                and len(eth_hourly) == expected_eth_1h
                and eth_missing_windows == 0
            ),
        },
        "passed": btc_execution_data_pass,
    }


def simulate_c_v1(
    btc_5m: list[dict[str, Any]],
    eth_5m: list[dict[str, Any]],
    *,
    evaluation_start_ms: int,
    evaluation_end_ms: int,
    stake_amount: float,
    initial_capital: float,
    cost_profile: dict[str, Any],
) -> dict[str, Any]:
    """Run locked TEST-SPEC-003 exactly once after a passed continuity certificate."""
    if not btc_5m:
        raise ValueError("empty_btc_feed")
    if not _is_contiguous_5m(btc_5m):
        raise ValueError("btc_5m_gap")
    if int(btc_5m[-1]["close_time"]) != evaluation_end_ms:
        raise ValueError("evaluation_end_mismatch")
    if not _valid_price(btc_5m[-1].get("close")):
        raise ValueError("invalid_final_5m_close")

    hourly = aggregate_1h_ohlc(btc_5m)
    eth_hourly = aggregate_1h_ohlc(eth_5m)
    atr_by_close = wilder_atr(hourly)
    signals = hourly_signals(hourly)
    eth_signals = hourly_signals(eth_hourly)

    signals_in_fold = {
        ts: side
        for ts, side in signals.items()
        if ts >= evaluation_start_ms
    }
    eligible_signals = {
        ts: side
        for ts, side in signals_in_fold.items()
        if ts + 1 <= evaluation_end_ms
    }

    eval_rows = [
        row
        for row in btc_5m
        if int(row["open_time"]) >= evaluation_start_ms
        and int(row["close_time"]) <= evaluation_end_ms
    ]
    if not eval_rows:
        raise ValueError("empty_evaluation_rows")
    if int(eval_rows[0]["open_time"]) != evaluation_start_ms:
        raise ValueError("evaluation_start_mismatch")
    if not _is_contiguous_5m(eval_rows):
        raise ValueError("evaluation_5m_gap")

    eval_open_times = {int(row["open_time"]) for row in eval_rows}
    for signal_close_time in eligible_signals:
        if signal_close_time + 1 not in eval_open_times:
            raise ValueError("missing_required_entry_bar")

    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    ignored_signals_while_open = 0
    unexecutable_end_signals = len(signals_in_fold) - len(eligible_signals)
    equity_peak = cash
    max_drawdown = 0.0

    def current_equity(mark_price: float) -> float:
        if position is None:
            return cash
        pnl = position["stake"] * _position_return(
            position["side"],
            position["entry_price"],
            mark_price,
            cost_profile,
        )
        return cash + position["stake"] + pnl

    def close_position(exit_price: float, exit_time: int, reason: str) -> None:
        nonlocal cash, position
        if position is None:
            return
        ret = _position_return(
            position["side"],
            position["entry_price"],
            exit_price,
            cost_profile,
        )
        pnl = position["stake"] * ret
        cash += position["stake"] + pnl
        trades.append(
            {
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
                "hold_minutes": (exit_time - position["entry_time"]) / 60_000,
                "entry_atr": position["entry_atr"],
            }
        )
        position = None

    for candle in eval_rows:
        open_time = int(candle["open_time"])
        close_time = int(candle["close_time"])
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])
        if any(not isfinite(v) or v <= 0 for v in (o, h, l, close)):
            raise ValueError("invalid_5m_ohlc")

        available_hour_close = _hour_close_before(open_time)
        signal_side = (
            eligible_signals.get(available_hour_close)
            if open_time == available_hour_close + 1
            else None
        )

        had_position_at_bar_start = position is not None
        if signal_side is not None and had_position_at_bar_start:
            ignored_signals_while_open += 1

        if position is not None:
            latest_atr = atr_by_close.get(available_hour_close)
            if latest_atr is not None:
                if position["side"] == "long":
                    candidate = (
                        position["peak_high"]
                        - ATR_MULTIPLE * latest_atr
                    )
                    position["stop"] = max(position["stop"], candidate)
                else:
                    candidate = (
                        position["trough_low"]
                        + ATR_MULTIPLE * latest_atr
                    )
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

        if signal_side is not None and not had_position_at_bar_start and position is None:
            atr = atr_by_close.get(available_hour_close)
            if atr is None or not isfinite(atr) or atr <= 0:
                raise ValueError("signal_without_valid_atr")
            stake = min(float(stake_amount), cash)
            if stake <= 0:
                raise ValueError("insufficient_capital")
            cash -= stake
            stop = (
                o - ATR_MULTIPLE * atr
                if signal_side == "long"
                else o + ATR_MULTIPLE * atr
            )
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

            if signal_side == "long" and l <= stop:
                close_position(stop, close_time, "initial_stop")
            elif signal_side == "short" and h >= stop:
                close_position(stop, close_time, "initial_stop")

        if position is not None:
            position["peak_high"] = max(float(position["peak_high"]), h)
            position["trough_low"] = min(float(position["trough_low"]), l)

        equity = current_equity(close)
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            max_drawdown = max(
                max_drawdown,
                (equity_peak - equity) / equity_peak * 100,
            )

    if position is not None:
        final_close = float(eval_rows[-1]["close"])
        if not isfinite(final_close) or final_close <= 0:
            raise ValueError("invalid_final_5m_close")
        close_position(final_close, evaluation_end_ms, "end_of_test")

    profits = [float(t["pnl_net"]) for t in trades]
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = sum(-p for p in profits if p < 0)
    net_pnl = sum(profits)

    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]

    eth_hour_times = {bar.close_time for bar in eth_hourly}
    eth_overlap = 0
    eth_overlap_denominator = 0
    eth_overlap_dropped = 0
    for trade in trades:
        ts = int(trade["signal_close_time"])
        required = (ts - ONE_HOUR_MS, ts, ts + ONE_HOUR_MS)
        if any(required_ts not in eth_hour_times for required_ts in required):
            eth_overlap_dropped += 1
            continue
        eth_overlap_denominator += 1
        if any(
            eth_signals.get(required_ts) == trade["side"]
            for required_ts in required
        ):
            eth_overlap += 1

    def side_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        pnl = sum(float(t["pnl_net"]) for t in items)
        return {
            "trades": len(items),
            "net_pnl_usdt": round(pnl, 8),
            "expectancy_usdt": (
                round(pnl / len(items), 8) if items else None
            ),
            "win_rate_percent": (
                round(
                    100
                    * sum(float(t["pnl_net"]) > 0 for t in items)
                    / len(items),
                    4,
                )
                if items
                else None
            ),
        }

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

    eval_minutes = (evaluation_end_ms - evaluation_start_ms + 1) / 60_000
    total_hold_minutes = sum(float(t["hold_minutes"]) for t in trades)
    metrics = {
        "signals_1h": len(eligible_signals),
        "ignored_signals_while_open": ignored_signals_while_open,
        "unexecutable_end_signals": unexecutable_end_signals,
        "closed_trades": len(trades),
        "net_pnl_usdt": round(net_pnl, 8),
        "net_expectancy_usdt_per_trade": (
            round(net_pnl / len(trades), 8) if trades else None
        ),
        "profit_factor": profit_factor,
        "profit_factor_infinite": profit_factor_infinite,
        "profit_factor_defined": profit_factor_defined,
        "gross_profit_usdt": round(gross_profit, 8),
        "gross_loss_usdt": round(gross_loss, 8),
        "long": side_metrics(longs),
        "short": side_metrics(shorts),
        "max_drawdown_percent": round(max_drawdown, 6),
        "time_in_market_percent": (
            round(100 * total_hold_minutes / eval_minutes, 6)
            if eval_minutes
            else None
        ),
        "average_hold_minutes": (
            round(total_hold_minutes / len(trades), 6)
            if trades
            else None
        ),
        "eth_same_direction_overlap_count": eth_overlap,
        "eth_same_direction_overlap_denominator": eth_overlap_denominator,
        "eth_overlap_dropped_trades": eth_overlap_dropped,
        "eth_overlap_completeness_percent": (
            round(100 * eth_overlap_denominator / len(trades), 6)
            if trades
            else None
        ),
        "eth_same_direction_overlap_percent": (
            round(100 * eth_overlap / eth_overlap_denominator, 6)
            if eth_overlap_denominator
            else None
        ),
        "win_rate_percent": (
            round(100 * sum(p > 0 for p in profits) / len(profits), 4)
            if profits
            else None
        ),
    }

    if len(trades) < 20:
        verdict = "INSUFFICIENT_SAMPLE"
        conditions = None
    else:
        both_sides_clause = True
        if longs and shorts:
            both_sides_clause = (
                metrics["long"]["net_pnl_usdt"] > 0
                and metrics["short"]["net_pnl_usdt"] > 0
            )
        conditions = {
            "expectancy_positive": (
                metrics["net_expectancy_usdt_per_trade"] is not None
                and metrics["net_expectancy_usdt_per_trade"] > 0
            ),
            "profit_factor_at_least_1_10": (
                metrics["profit_factor_infinite"]
                or (
                    metrics["profit_factor"] is not None
                    and metrics["profit_factor"] >= 1.10
                )
            ),
            "both_active_sides_profitable": both_sides_clause,
        }
        verdict = "PASS" if all(conditions.values()) else "FAIL"

    return {
        "metrics": metrics,
        "trades": trades,
        "conditions": conditions,
        "verdict": verdict,
    }


def smoke_cases() -> dict[str, bool]:
    """Synthetic-only Strategy C smoke checks."""
    def candle(ts: int, o: float, h: float, l: float, c: float) -> dict[str, Any]:
        return {
            "open_time": ts,
            "close_time": ts + FIVE_MIN_MS - 1,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        }

    hourly = [
        HourBar(
            i * ONE_HOUR_MS,
            (i + 1) * ONE_HOUR_MS - 1,
            100,
            101,
            99,
            100,
        )
        for i in range(25)
    ]
    hourly.append(
        HourBar(
            25 * ONE_HOUR_MS,
            26 * ONE_HOUR_MS - 1,
            100,
            110,
            100,
            109,
        )
    )
    atr = wilder_atr(hourly)
    expected = ((23 * 2.0) + 10.0) / 24.0
    atr_signal_included = abs(
        atr[26 * ONE_HOUR_MS - 1] - expected
    ) < 1e-12

    base = []
    for hour in range(27):
        for j in range(12):
            ts = hour * ONE_HOUR_MS + j * FIVE_MIN_MS
            if hour < 24:
                base.append(candle(ts, 100, 101, 99, 100))
            elif hour == 24:
                close = 102 if j == 11 else 100
                high = 102.2 if j == 11 else 101
                base.append(candle(ts, 100, high, 99.5, close))
            else:
                base.append(candle(ts, 102.5, 103, 101.5, 102.0))

    eval_start = 24 * ONE_HOUR_MS
    eval_end = 27 * ONE_HOUR_MS - 1
    profile = {
        "entry_cost_rate": 0.001,
        "exit_cost_rate": 0.001,
    }
    result = simulate_c_v1(
        base,
        base,
        evaluation_start_ms=eval_start,
        evaluation_end_ms=eval_end,
        stake_amount=50,
        initial_capital=100,
        cost_profile=profile,
    )
    exact_next_hour_entry = (
        bool(result["trades"])
        and result["trades"][0]["entry_time"] == 25 * ONE_HOUR_MS
    )

    cert = continuity_certificate(
        base,
        base,
        evaluation_start_ms=eval_start,
        evaluation_end_ms=eval_end,
        btc_warmup_start_ms=0,
        eth_warmup_start_ms=0,
    )
    continuity_no_metrics = (
        cert["strategy_metrics_computed"] is False
        and "net_pnl_usdt" not in cert
    )

    missing_entry_rejected = False
    bad = [
        dict(row)
        for row in base
        if int(row["open_time"]) != 25 * ONE_HOUR_MS
    ]
    try:
        simulate_c_v1(
            bad,
            base,
            evaluation_start_ms=eval_start,
            evaluation_end_ms=eval_end,
            stake_amount=50,
            initial_capital=100,
            cost_profile=profile,
        )
    except ValueError as exc:
        missing_entry_rejected = str(exc) in {
            "btc_5m_gap",
            "missing_required_entry_bar",
            "evaluation_5m_gap",
        }

    return {
        "atr_includes_signal_bar": atr_signal_included,
        "exact_next_hour_entry": exact_next_hour_entry,
        "missing_required_entry_rejected": missing_entry_rejected,
        "continuity_certificate_has_no_strategy_metrics": continuity_no_metrics,
        "long_short_costs": (
            long_net_return(100, 100, profile) < 0
            and short_net_return(100, 100, profile) < 0
        ),
    }
