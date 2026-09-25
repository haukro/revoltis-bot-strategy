"""Incremental frozen Strategy-B reference adapter for locked IMPLEMENTATION-SPEC-006.

Reference-plane only. No official scoring, PnL, live routing or runtime activation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from math import isfinite
from typing import Any

from .tsmom_b_v1 import ATR_MULTIPLE, FIVE_MIN_MS, aggregate_1h_ohlc, hourly_signals, wilder_atr


@dataclass
class ReferenceState:
    reference_position_state: str = "FLAT"
    reference_signal_close_time: int | None = None
    reference_entry_time: int | None = None
    reference_entry_price: float | None = None
    reference_entry_atr: float | None = None
    active_stop: float | None = None
    peak_high: float | None = None
    trough_low: float | None = None


def action_key(strategy_version_id: str, pair: str, action_type: str, reference_decision_time_ms: int, required_execution_time_ms: int, position_side: str, reason_code: str) -> str:
    payload = "|".join(["STRATEGY_ACTION", strategy_version_id, pair, action_type, str(reference_decision_time_ms), str(required_execution_time_ms), position_side, reason_code])
    return sha256(payload.encode()).hexdigest()


def _hour_close_before(open_time: int) -> int:
    return (open_time // 3_600_000) * 3_600_000 - 1


def _validate_bar(candle: dict[str, Any]) -> None:
    values = [float(candle[k]) for k in ("open", "high", "low", "close")]
    if any(not isfinite(v) or v <= 0 for v in values):
        raise ValueError("invalid_5m_ohlc")
    if int(candle["close_time"]) != int(candle["open_time"]) + FIVE_MIN_MS - 1:
        raise ValueError("invalid_5m_close_time")


def compute_reference_transition(*, history_through_current: list[dict[str, Any]], prior_state: ReferenceState, strategy_version_id: str, pair: str) -> tuple[ReferenceState, list[dict[str, Any]]]:
    """Apply exactly one next 5m reference transition.

    history_through_current must be contiguous and end with the bar being processed.
    It may include arbitrary warmup history before the current bar.
    """
    if not history_through_current:
        raise ValueError("empty_history")
    current = history_through_current[-1]
    _validate_bar(current)
    if any(int(b["open_time"]) - int(a["open_time"]) != FIVE_MIN_MS for a, b in zip(history_through_current, history_through_current[1:])):
        raise ValueError("non_contiguous_5m_history")

    hourly = aggregate_1h_ohlc(history_through_current)
    atr_by_close = wilder_atr(hourly)
    signals = hourly_signals(hourly)

    open_time = int(current["open_time"])
    close_time = int(current["close_time"])
    o, h, l = (float(current[k]) for k in ("open", "high", "low"))
    available_hour_close = _hour_close_before(open_time)
    signal_side = signals.get(available_hour_close) if open_time == available_hour_close + 1 else None

    state = ReferenceState(**asdict(prior_state))
    actions: list[dict[str, Any]] = []
    had_position_at_bar_start = state.reference_position_state in ("LONG", "SHORT")

    if had_position_at_bar_start:
        latest_atr = atr_by_close.get(available_hour_close)
        if latest_atr is not None:
            if state.reference_position_state == "LONG":
                candidate = float(state.peak_high) - ATR_MULTIPLE * latest_atr
                state.active_stop = max(float(state.active_stop), candidate)
            else:
                candidate = float(state.trough_low) + ATR_MULTIPLE * latest_atr
                state.active_stop = min(float(state.active_stop), candidate)

        active_stop = float(state.active_stop)
        exit_price: float | None = None
        reason: str | None = None
        if state.reference_position_state == "LONG":
            if o < active_stop:
                exit_price, reason = o, "chandelier_stop_gap"
            elif l <= active_stop:
                exit_price, reason = active_stop, "chandelier_stop"
        else:
            if o > active_stop:
                exit_price, reason = o, "chandelier_stop_gap"
            elif h >= active_stop:
                exit_price, reason = active_stop, "chandelier_stop"

        if exit_price is not None:
            side = state.reference_position_state
            exit_time = open_time if reason == "chandelier_stop_gap" else close_time
            actions.append({
                "action_type": "EXIT_TO_FLAT",
                "position_side": side,
                "reference_decision_time_ms": exit_time,
                "required_execution_time_ms": exit_time,
                "reference_price": exit_price,
                "reason_code": reason,
                "active_stop": active_stop,
                "reference_atr": latest_atr,
                "idempotency_key": action_key(strategy_version_id, pair, "EXIT_TO_FLAT", exit_time, exit_time, side, reason),
            })
            state = ReferenceState()

    if signal_side is not None and not had_position_at_bar_start and state.reference_position_state == "FLAT":
        atr = atr_by_close.get(available_hour_close)
        if atr is None or not isfinite(atr) or atr <= 0:
            raise ValueError("signal_without_valid_atr")
        side = "LONG" if signal_side == "long" else "SHORT"
        stop = o - ATR_MULTIPLE * atr if side == "LONG" else o + ATR_MULTIPLE * atr
        state = ReferenceState(
            reference_position_state=side,
            reference_signal_close_time=available_hour_close,
            reference_entry_time=open_time,
            reference_entry_price=o,
            reference_entry_atr=atr,
            active_stop=stop,
            peak_high=o,
            trough_low=o,
        )
        entry = {
            "action_type": "ENTRY",
            "position_side": side,
            "reference_decision_time_ms": available_hour_close,
            "required_execution_time_ms": open_time,
            "reference_price": o,
            "reason_code": "BREAKOUT_LONG" if side == "LONG" else "BREAKOUT_SHORT",
            "active_stop": stop,
            "reference_atr": atr,
        }
        entry["idempotency_key"] = action_key(strategy_version_id, pair, "ENTRY", available_hour_close, open_time, side, entry["reason_code"])
        actions.append(entry)

        initial_hit = (side == "LONG" and l <= stop) or (side == "SHORT" and h >= stop)
        if initial_hit:
            exit_action = {
                "action_type": "EXIT_TO_FLAT",
                "position_side": side,
                "reference_decision_time_ms": close_time,
                "required_execution_time_ms": close_time,
                "reference_price": stop,
                "reason_code": "initial_stop",
                "active_stop": stop,
                "reference_atr": atr,
            }
            exit_action["idempotency_key"] = action_key(strategy_version_id, pair, "EXIT_TO_FLAT", close_time, close_time, side, "initial_stop")
            actions.append(exit_action)
            state = ReferenceState()

    if state.reference_position_state in ("LONG", "SHORT"):
        state.peak_high = max(float(state.peak_high), h)
        state.trough_low = min(float(state.trough_low), l)

    return state, actions


def smoke_cases() -> dict[str, bool]:
    key = action_key("TEST-SPEC-002", "ZEC/USDT", "ENTRY", 1, 2, "LONG", "BREAKOUT_LONG")
    return {
        "action_key_stable": key == action_key("TEST-SPEC-002", "ZEC/USDT", "ENTRY", 1, 2, "LONG", "BREAKOUT_LONG"),
        "action_key_type_sensitive": key != action_key("TEST-SPEC-002", "ZEC/USDT", "EXIT_TO_FLAT", 1, 2, "LONG", "BREAKOUT_LONG"),
    }