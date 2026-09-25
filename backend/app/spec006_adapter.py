"""Operational incremental reference helpers for locked IMPLEMENTATION-SPEC-006.

This module mirrors frozen TEST-SPEC-002 reference state only.
It does not compute official performance, PASS/FAIL, or paper execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from .tsmom_b_v1 import (
    ATR_MULTIPLE,
    FIVE_MIN_MS,
    ONE_HOUR_MS,
    aggregate_1h_ohlc,
    hourly_signals,
    wilder_atr,
)


@dataclass(frozen=True)
class ReferenceAction:
    action_type: str
    position_side: str
    reference_decision_time_ms: int
    required_execution_time_ms: int
    reference_price: float
    reason_code: str
    reference_atr: float | None
    active_stop: float | None


def reference_bar_source_hash(
    strategy_version_id: str,
    pair: str,
    candle: dict[str, Any],
) -> str:
    payload = [
        strategy_version_id,
        pair.upper(),
        int(candle["open_time"]),
        int(candle["close_time"]),
        str(candle["open"]),
        str(candle["high"]),
        str(candle["low"]),
        str(candle["close"]),
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def reference_bar_rpc_row(
    strategy_version_id: str,
    pair: str,
    candle: dict[str, Any],
) -> dict[str, Any]:
    return {
        "strategy_version_id": strategy_version_id,
        "pair": pair.upper(),
        "open_time": datetime.fromtimestamp(int(candle["open_time"]) / 1000, UTC).isoformat(),
        "close_time": datetime.fromtimestamp(int(candle["close_time"]) / 1000, UTC).isoformat(),
        "open": str(candle["open"]),
        "high": str(candle["high"]),
        "low": str(candle["low"]),
        "close": str(candle["close"]),
        "source_hash": reference_bar_source_hash(strategy_version_id, pair, candle),
    }


def _hour_close_before(open_time: int) -> int:
    return (open_time // ONE_HOUR_MS) * ONE_HOUR_MS - 1


def replay_frozen_reference(
    candles: list[dict[str, Any]],
    *,
    evaluation_start_ms: int,
    evaluation_end_ms: int,
) -> dict[str, Any]:
    """Replay frozen B reference mechanics without PnL or official scoring."""
    if not candles:
        raise ValueError("empty_reference_feed")
    ordered = sorted(candles, key=lambda row: int(row["open_time"]))
    if any(
        int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS
        for a, b in zip(ordered, ordered[1:])
    ):
        raise ValueError("reference_5m_gap")

    hourly = aggregate_1h_ohlc(ordered)
    atr_by_close = wilder_atr(hourly)
    signals = hourly_signals(hourly)
    signal_times = {
        ts: side
        for ts, side in signals.items()
        if evaluation_start_ms <= ts <= evaluation_end_ms
    }

    rows = [
        row for row in ordered
        if int(row["open_time"]) >= evaluation_start_ms
        and int(row["close_time"]) <= evaluation_end_ms
    ]
    if not rows:
        raise ValueError("empty_reference_window")
    if int(rows[0]["open_time"]) != evaluation_start_ms:
        raise ValueError("reference_start_mismatch")

    position: dict[str, Any] | None = None
    actions: list[ReferenceAction] = []

    for candle in rows:
        open_time = int(candle["open_time"])
        close_time = int(candle["close_time"])
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])
        if any(not isfinite(value) or value <= 0 for value in (o, h, l, close)):
            raise ValueError("invalid_reference_ohlc")

        available_hour_close = _hour_close_before(open_time)
        signal_side = (
            signal_times.get(available_hour_close)
            if open_time == available_hour_close + 1
            else None
        )
        had_position_at_bar_start = position is not None

        if position is not None:
            latest_atr = atr_by_close.get(available_hour_close)
            if latest_atr is not None:
                if position["side"] == "LONG":
                    candidate = position["peak_high"] - ATR_MULTIPLE * latest_atr
                    position["stop"] = max(position["stop"], candidate)
                else:
                    candidate = position["trough_low"] + ATR_MULTIPLE * latest_atr
                    position["stop"] = min(position["stop"], candidate)

            active_stop = float(position["stop"])
            exit_price: float | None = None
            exit_time: int | None = None
            reason: str | None = None
            if position["side"] == "LONG":
                if o < active_stop:
                    exit_price, exit_time, reason = o, open_time, "chandelier_stop_gap"
                elif l <= active_stop:
                    exit_price, exit_time, reason = active_stop, close_time, "chandelier_stop"
            else:
                if o > active_stop:
                    exit_price, exit_time, reason = o, open_time, "chandelier_stop_gap"
                elif h >= active_stop:
                    exit_price, exit_time, reason = active_stop, close_time, "chandelier_stop"

            if exit_price is not None and exit_time is not None and reason is not None:
                actions.append(ReferenceAction(
                    "EXIT_TO_FLAT",
                    position["side"],
                    exit_time,
                    exit_time,
                    exit_price,
                    reason,
                    latest_atr,
                    active_stop,
                ))
                position = None

        if signal_side is not None and not had_position_at_bar_start and position is None:
            atr = atr_by_close.get(available_hour_close)
            if atr is None or not isfinite(atr) or atr <= 0:
                raise ValueError("signal_without_valid_atr")
            side = signal_side.upper()
            stop = o - ATR_MULTIPLE * atr if side == "LONG" else o + ATR_MULTIPLE * atr
            actions.append(ReferenceAction(
                "ENTRY",
                side,
                available_hour_close,
                open_time,
                o,
                "BREAKOUT_LONG" if side == "LONG" else "BREAKOUT_SHORT",
                atr,
                stop,
            ))
            position = {
                "side": side,
                "signal_close_time": available_hour_close,
                "entry_time": open_time,
                "entry_price": o,
                "entry_atr": atr,
                "stop": stop,
                "peak_high": o,
                "trough_low": o,
            }

            initial_hit = (
                (side == "LONG" and l <= stop)
                or (side == "SHORT" and h >= stop)
            )
            if initial_hit:
                actions.append(ReferenceAction(
                    "EXIT_TO_FLAT",
                    side,
                    close_time,
                    close_time,
                    stop,
                    "initial_stop",
                    atr,
                    stop,
                ))
                position = None

        if position is not None:
            position["peak_high"] = max(float(position["peak_high"]), h)
            position["trough_low"] = min(float(position["trough_low"]), l)

    final_state: dict[str, Any] = {
        "reference_position_state": "FLAT",
        "reference_signal_close_time": None,
        "reference_entry_time": None,
        "reference_entry_price": None,
        "reference_entry_atr": None,
        "active_stop": None,
        "peak_high": None,
        "trough_low": None,
    }
    if position is not None:
        final_state = {
            "reference_position_state": position["side"],
            "reference_signal_close_time": position["signal_close_time"],
            "reference_entry_time": position["entry_time"],
            "reference_entry_price": position["entry_price"],
            "reference_entry_atr": position["entry_atr"],
            "active_stop": position["stop"],
            "peak_high": position["peak_high"],
            "trough_low": position["trough_low"],
        }

    return {
        "actions": actions,
        "state": final_state,
        "cursor_open_time": int(rows[-1]["open_time"]),
        "cursor_close_time": int(rows[-1]["close_time"]),
    }


def blind_bootstrap_category(replay: dict[str, Any]) -> str:
    return "SAFE_FLAT" if replay["state"]["reference_position_state"] == "FLAT" else "WAIT_SAFE_BOUNDARY"
