"""Preregistered paired rebound-confirmation experiment.

This module is research-only. It does not alter the production strategy
configuration, optimizer search space, or qualification gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import median, pstdev
from typing import Any

FOUR_HOURS_MS = 4 * 60 * 60 * 1000
FIVE_MINUTES_MS = 5 * 60 * 1000


@dataclass(frozen=True)
class FourHourBar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float


def _bucket_start(open_time: int) -> int:
    return (open_time // FOUR_HOURS_MS) * FOUR_HOURS_MS


def _find_signal_index(candles: list[dict[str, Any]], signal_close_time: int) -> int:
    for index, candle in enumerate(candles):
        if int(candle["close_time"]) == signal_close_time:
            return index
    raise ValueError("signal_timestamp_not_found")


def _aggregate_4h(candles: list[dict[str, Any]], bucket_start: int) -> FourHourBar:
    rows = [
        candle for candle in candles
        if bucket_start <= int(candle["open_time"]) < bucket_start + FOUR_HOURS_MS
    ]
    rows.sort(key=lambda candle: int(candle["open_time"]))
    if len(rows) != 48:
        raise ValueError("incomplete_4h_candle")
    if int(rows[0]["open_time"]) != bucket_start:
        raise ValueError("misaligned_4h_open")
    if int(rows[-1]["close_time"]) != bucket_start + FOUR_HOURS_MS - 1:
        raise ValueError("misaligned_4h_close")
    for left, right in zip(rows, rows[1:]):
        if int(right["open_time"]) - int(left["open_time"]) != FIVE_MINUTES_MS:
            raise ValueError("gap_in_4h_candle")
    return FourHourBar(
        open_time=bucket_start,
        close_time=bucket_start + FOUR_HOURS_MS - 1,
        open=float(rows[0]["open"]),
        high=max(float(row["high"]) for row in rows),
        low=min(float(row["low"]) for row in rows),
        close=float(rows[-1]["close"]),
    )


def rebound_confirmation_context(
    candles: list[dict[str, Any]],
    signal_close_time: int,
    entry_price: float | None = None,
) -> dict[str, Any]:
    """Evaluate Filter B with no data from the signal's in-progress 4h candle."""
    signal_index = _find_signal_index(candles, signal_close_time)
    signal_candle = candles[signal_index]
    current_bucket = _bucket_start(int(signal_candle["open_time"]))
    bucket_starts = [current_bucket - FOUR_HOURS_MS * offset for offset in range(6, 0, -1)]
    bars = [_aggregate_4h(candles, start) for start in bucket_starts]

    low_24h = min(bar.low for bar in bars)
    # Explicit preregistration rule: latest equal-low 4h candle wins.
    low_origin = max((bar for bar in bars if bar.low == low_24h), key=lambda bar: bar.open_time)

    confirmation = next(
        (
            bar for bar in bars
            if bar.open_time > low_origin.open_time
            and bar.close_time < current_bucket
            and bar.low > low_24h
            and bar.close > bar.open
        ),
        None,
    )

    price = float(entry_price if entry_price is not None else signal_candle["close"])
    high_24h = max(bar.high for bar in bars)
    range_pos = ((price - low_24h) / (high_24h - low_24h) * 100) if high_24h > low_24h else None
    pct_above_low = ((price / low_24h) - 1) * 100 if low_24h else None

    low_origin_rows = [
        (index, candle) for index, candle in enumerate(candles)
        if low_origin.open_time <= int(candle["open_time"]) < low_origin.open_time + FOUR_HOURS_MS
        and float(candle["low"]) == low_24h
    ]
    if not low_origin_rows:
        raise ValueError("low_origin_5m_not_found")
    low_index = max(index for index, _ in low_origin_rows)
    bars_since_low = signal_index - low_index

    return {
        "confirmed": confirmation is not None,
        "low_24h": low_24h,
        "low_origin_4h_open_time": low_origin.open_time,
        "confirm_4h_ts": confirmation.close_time if confirmation else None,
        "range_pos_24h": range_pos,
        "pct_above_24h_low": pct_above_low,
        "bars_since_24h_low": bars_since_low,
        "current_4h_open_time": current_bucket,
    }


def filter_b(candles: list[dict[str, Any]], index: int) -> bool:
    candle = candles[index]
    return bool(
        rebound_confirmation_context(
            candles,
            int(candle["close_time"]),
            float(candle["close"]),
        )["confirmed"]
    )


def _iso_to_ms(value: str) -> int:
    from datetime import datetime
    return int(round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000))


def enrich_trade_path(candles: list[dict[str, Any]], trade: dict[str, Any], trail_start_percent: float) -> dict[str, Any]:
    by_close = {int(candle["close_time"]): i for i, candle in enumerate(candles)}
    entry_ms = _iso_to_ms(trade["entry_ts"])
    exit_ms = _iso_to_ms(trade["exit_ts"])
    entry_index = by_close[entry_ms]
    exit_index = by_close[exit_ms]
    entry_price = float(trade["entry_px"])
    context = rebound_confirmation_context(candles, entry_ms, entry_price)

    def prior_return(bars: int) -> float | None:
        if entry_index < bars:
            return None
        prior = float(candles[entry_index - bars]["close"])
        return (entry_price / prior - 1) * 100 if prior else None

    values = []
    if entry_index >= 24:
        for j in range(entry_index - 23, entry_index + 1):
            before = float(candles[j - 1]["close"])
            after = float(candles[j]["close"])
            values.append(log(after / before) * 100)
    rv24 = pstdev(values) if values else None

    trail_level = entry_price * (1 + trail_start_percent / 100)
    minus1 = entry_price * .99
    time_to_trail = None
    time_to_minus1 = None
    mae_before_trail = 0.0
    trail_seen = False
    for candle in candles[entry_index + 1:exit_index + 1]:
        minutes = (int(candle["close_time"]) - entry_ms) / 60_000
        low = float(candle["low"])
        high = float(candle["high"])
        if not trail_seen:
            mae_before_trail = min(mae_before_trail, (low / entry_price - 1) * 100)
        if time_to_minus1 is None and low <= minus1:
            time_to_minus1 = minutes
        if time_to_trail is None and high >= trail_level:
            time_to_trail = minutes
            trail_seen = True

    return {
        "pre_entry_1h_ret": prior_return(12),
        "pre_entry_4h_ret": prior_return(48),
        "rv_24bars": rv24,
        "range_pos_24h": context["range_pos_24h"],
        "pct_above_24h_low": context["pct_above_24h_low"],
        "bars_since_24h_low": context["bars_since_24h_low"],
        "confirm_4h_ts": context["confirm_4h_ts"],
        "mae_before_trail_pct": mae_before_trail,
        "hit_minus_1pct": time_to_minus1 is not None,
        "time_to_trail_min": time_to_trail,
        "time_to_minus_1pct_min": time_to_minus1,
    }


def paired_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0, "wr_overall": None, "trail_reached_share": None,
            "wr_trail_reached": None, "wr_trail_not_reached": None,
            "trail_not_reached_n": 0,
        }
    wins = [trade for trade in trades if float(trade["pnl_net"]) > 0]
    trail = [trade for trade in trades if bool(trade.get("trail_hit"))]
    no_trail = [trade for trade in trades if not bool(trade.get("trail_hit"))]
    return {
        "trades": len(trades),
        "wr_overall": 100 * len(wins) / len(trades),
        "trail_reached_share": 100 * len(trail) / len(trades),
        "wr_trail_reached": 100 * sum(float(t["pnl_net"]) > 0 for t in trail) / len(trail) if trail else None,
        "wr_trail_not_reached": 100 * sum(float(t["pnl_net"]) > 0 for t in no_trail) / len(no_trail) if no_trail else None,
        "trail_not_reached_n": len(no_trail),
        "avg_mae": sum(float(t["mae_pct"]) for t in trades) / len(trades),
        "avg_mae_losses": (
            sum(float(t["mae_pct"]) for t in trades if float(t["pnl_net"]) < 0)
            / sum(float(t["pnl_net"]) < 0 for t in trades)
        ) if any(float(t["pnl_net"]) < 0 for t in trades) else None,
        "median_time_to_trail": median([float(t["time_to_trail_min"]) for t in trades if t["time_to_trail_min"] is not None])
            if any(t["time_to_trail_min"] is not None for t in trades) else None,
        "median_time_to_minus_1pct": median([float(t["time_to_minus_1pct_min"]) for t in trades if t["time_to_minus_1pct_min"] is not None])
            if any(t["time_to_minus_1pct_min"] is not None for t in trades) else None,
        "minus_1_before_trail_share": 100 * sum(
            t["time_to_minus_1pct_min"] is not None
            and (t["time_to_trail_min"] is None or float(t["time_to_minus_1pct_min"]) <= float(t["time_to_trail_min"]))
            for t in trades
        ) / len(trades),
    }


def smoke_cases() -> dict[str, bool]:
    """Synthetic-only checks; never touches the preregistered evaluation period."""
    def make_bucket(start: int, o: float, h: float, l: float, c: float) -> list[dict[str, Any]]:
        rows = []
        for i in range(48):
            ts = start + i * FIVE_MINUTES_MS
            rows.append({
                "open_time": ts,
                "close_time": ts + FIVE_MINUTES_MS - 1,
                "open": o if i == 0 else c,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1.0,
                "quote_volume": 1000.0,
            })
        return rows

    base = 1_800_000_000_000
    base = _bucket_start(base)
    candles: list[dict[str, Any]] = []
    specs = [
        (100, 104, 98, 101),
        (101, 103, 97, 99),
        (99, 102, 95, 96),   # first equal low
        (96, 100, 95, 97),   # latest equal low must win
        (97, 101, 96, 100),  # bullish higher-low confirmation
        (100, 102, 99, 101),
        (101, 103, 100, 102),  # current 4h bucket, ignored
    ]
    for offset, (o, h, l, c) in enumerate(specs):
        candles.extend(make_bucket(base + offset * FOUR_HOURS_MS, o, h, l, c))

    signal = candles[-1]
    context = rebound_confirmation_context(candles, int(signal["close_time"]), float(signal["close"]))
    latest_equal_low = context["low_origin_4h_open_time"] == base + 3 * FOUR_HOURS_MS
    utc_current_excluded = context["current_4h_open_time"] == base + 6 * FOUR_HOURS_MS
    confirmed = context["confirmed"] and context["confirm_4h_ts"] == base + 5 * FOUR_HOURS_MS - 1

    future_mutated = [dict(row) for row in candles]
    for row in future_mutated:
        if int(row["open_time"]) >= base + 6 * FOUR_HOURS_MS:
            row["low"] = 1.0
            row["close"] = 1.0
    context_future = rebound_confirmation_context(
        future_mutated, int(signal["close_time"]), float(signal["close"])
    )
    no_lookahead = (
        context_future["confirmed"] == context["confirmed"]
        and context_future["low_24h"] == context["low_24h"]
        and context_future["low_origin_4h_open_time"] == context["low_origin_4h_open_time"]
    )

    return {
        "utc_4h_boundaries": utc_current_excluded,
        "latest_equal_low": latest_equal_low,
        "confirmation_after_low": confirmed,
        "no_lookahead": no_lookahead,
    }
