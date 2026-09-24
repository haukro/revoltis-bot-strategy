"""Strategy-neutral paper execution primitives for IMPLEMENTATION-SPEC-004.

This module contains deterministic, replayable helpers only. It does not submit live
orders and does not contain alpha logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Any, Iterable, Literal


OrderState = Literal[
    "CREATED",
    "ACCEPTED",
    "PENDING_FILL",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "FAILED",
]


ALLOWED_ORDER_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"ACCEPTED", "REJECTED"},
    "ACCEPTED": {"PENDING_FILL", "CANCEL_REQUESTED"},
    "PENDING_FILL": {"PARTIALLY_FILLED", "FILLED", "CANCEL_REQUESTED", "EXPIRED", "FAILED"},
    "PARTIALLY_FILLED": {"FILLED", "CANCEL_REQUESTED", "FAILED"},
    "CANCEL_REQUESTED": {"CANCELLED"},
    "FILLED": set(),
    "CANCELLED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
    "FAILED": set(),
}

TERMINAL_ORDER_STATES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "FAILED"}

BLIND_FORBIDDEN_FIELDS = {
    "pair",
    "side",
    "signal_close_time",
    "intended_entry_time",
    "intended_notional",
    "fill_price",
    "fill_quantity",
    "filled_at",
    "average_entry_price",
    "quantity",
    "gross_entry_notional",
    "realized_pnl",
    "unrealized_pnl",
    "profit_factor",
    "expectancy",
    "win_rate",
    "equity_curve",
    "correlation_id",
}


def _part(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def canonical_key(event_type: str, *parts: Any) -> str:
    """Return a canonical server-generated SHA-256 idempotency key."""
    if not event_type or not event_type.strip():
        raise ValueError("event_type_required")
    payload = "|".join([event_type.strip().upper(), *(_part(x) for x in parts)])
    return sha256(payload.encode("utf-8")).hexdigest()


def signal_idempotency_key(
    strategy_version_id: str,
    pair: str,
    side: str,
    signal_close_time: Any,
    rule_id: str,
) -> str:
    return canonical_key(
        "SIGNAL",
        strategy_version_id,
        pair,
        side,
        signal_close_time,
        rule_id,
    )


def outbox_idempotency_key(
    event_type: str,
    entity_type: str,
    entity_id: Any,
    logical_step: str,
) -> str:
    return canonical_key("OUTBOX", event_type, entity_type, entity_id, logical_step)


def order_idempotency_key(signal_id: Any, intent_type: str, order_type: str) -> str:
    return canonical_key("ORDER", signal_id, intent_type, order_type)


def execution_attempt_key(order_id: Any, attempt_seq: int) -> str:
    if attempt_seq < 1:
        raise ValueError("invalid_attempt_seq")
    return canonical_key("FILL_ATTEMPT", order_id, attempt_seq)


def fill_idempotency_key(order_id: Any, fill_seq: int, execution_attempt_id: Any) -> str:
    if fill_seq < 1:
        raise ValueError("invalid_fill_seq")
    return canonical_key("FILL", order_id, fill_seq, execution_attempt_id)


def validate_order_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED_ORDER_TRANSITIONS.get(from_state, set())


@dataclass(frozen=True)
class BookFill:
    side: str
    requested_quote_notional: float
    filled_quote_notional: float
    filled_base_quantity: float
    vwap: float | None
    best_price: float | None
    spread_bps: float | None
    impact_bps: float | None
    levels_used: int
    complete: bool


def _clean_levels(levels: Iterable[Iterable[Any]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in levels:
        values = list(row)
        if len(values) < 2:
            raise ValueError("invalid_book_level")
        price = float(values[0])
        qty = float(values[1])
        if not isfinite(price) or not isfinite(qty) or price <= 0 or qty < 0:
            raise ValueError("invalid_book_level")
        if qty > 0:
            out.append((price, qty))
    return out


def walk_quote_notional(
    *,
    side: Literal["BUY", "SELL"],
    quote_notional: float,
    bids: Iterable[Iterable[Any]],
    asks: Iterable[Iterable[Any]],
) -> BookFill:
    """Deterministically walk an immutable book snapshot for quote notional.

    BUY consumes asks until the requested quote amount is spent.
    SELL consumes bids until the requested quote proceeds are reached.

    This is a paper-execution primitive, not proof that synthetic spot shorts are live
    executable without inventory/borrow.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError("invalid_side")
    q = float(quote_notional)
    if not isfinite(q) or q <= 0:
        raise ValueError("invalid_quote_notional")

    clean_bids = sorted(_clean_levels(bids), key=lambda x: x[0], reverse=True)
    clean_asks = sorted(_clean_levels(asks), key=lambda x: x[0])
    if not clean_bids or not clean_asks:
        raise ValueError("missing_two_sided_book")

    best_bid = clean_bids[0][0]
    best_ask = clean_asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10_000

    levels = clean_asks if side == "BUY" else clean_bids
    best = best_ask if side == "BUY" else best_bid
    remaining = q
    base_qty = 0.0
    quote_filled = 0.0
    levels_used = 0

    for price, available_base in levels:
        if remaining <= 1e-12:
            break
        available_quote = price * available_base
        take_quote = min(remaining, available_quote)
        take_base = take_quote / price
        quote_filled += take_quote
        base_qty += take_base
        remaining -= take_quote
        levels_used += 1

    complete = remaining <= max(1e-9, q * 1e-12)
    if base_qty <= 0:
        return BookFill(
            side=side,
            requested_quote_notional=q,
            filled_quote_notional=0.0,
            filled_base_quantity=0.0,
            vwap=None,
            best_price=best,
            spread_bps=spread_bps,
            impact_bps=None,
            levels_used=0,
            complete=False,
        )

    vwap = quote_filled / base_qty
    if side == "BUY":
        impact_bps = (vwap / best - 1) * 10_000
    else:
        impact_bps = (1 - vwap / best) * 10_000

    return BookFill(
        side=side,
        requested_quote_notional=q,
        filled_quote_notional=quote_filled,
        filled_base_quantity=base_qty,
        vwap=vwap,
        best_price=best,
        spread_bps=spread_bps,
        impact_bps=impact_bps,
        levels_used=levels_used,
        complete=complete,
    )


def realized_pnl_long(
    entry_vwap: float,
    exit_vwap: float,
    quantity: float,
    allocated_entry_fees: float,
    exit_fees: float,
) -> float:
    return (exit_vwap - entry_vwap) * quantity - allocated_entry_fees - exit_fees


def realized_pnl_short(
    entry_vwap: float,
    exit_vwap: float,
    quantity: float,
    allocated_entry_fees: float,
    exit_fees: float,
) -> float:
    return (entry_vwap - exit_vwap) * quantity - allocated_entry_fees - exit_fees


def blind_safe_operational_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove fields that would make blinded strategy performance reconstructable.

    This helper is defense-in-depth only. SPEC-004 requires DB/service authorization
    to deny row-level blinded execution data before this layer is reached.
    """
    return {
        key: value
        for key, value in payload.items()
        if key not in BLIND_FORBIDDEN_FIELDS
    }


def smoke_cases() -> dict[str, bool]:
    bids = [["99.9", "0.30"], ["99.8", "1.0"]]
    asks = [["100.1", "0.20"], ["100.2", "1.0"]]

    buy = walk_quote_notional(
        side="BUY",
        quote_notional=50.0,
        bids=bids,
        asks=asks,
    )
    sell = walk_quote_notional(
        side="SELL",
        quote_notional=50.0,
        bids=bids,
        asks=asks,
    )

    signal_key = signal_idempotency_key(
        "TEST-SPEC-002",
        "ZEC/USDT",
        "LONG",
        "2026-09-24T21:59:59.999Z",
        "R_TSMOM_24_LONG",
    )

    return {
        "canonical_signal_key_stable": signal_key == signal_idempotency_key(
            "TEST-SPEC-002",
            "ZEC/USDT",
            "LONG",
            "2026-09-24T21:59:59.999Z",
            "R_TSMOM_24_LONG",
        ),
        "outbox_key_event_type_changes_key": outbox_idempotency_key(
            "RISK_EVALUATE", "signal", "abc", "PRETRADE"
        )
        != outbox_idempotency_key(
            "BIND_FILL_ATTEMPT", "signal", "abc", "PRETRADE"
        ),
        "book_walk_buy_complete": buy.complete and buy.vwap is not None and buy.vwap >= 100.1,
        "book_walk_sell_complete": sell.complete and sell.vwap is not None and sell.vwap <= 99.9,
        "book_walk_never_uses_last_price": buy.best_price == 100.1 and sell.best_price == 99.9,
        "cancel_requested_cannot_fill": not validate_order_transition("CANCEL_REQUESTED", "FILLED"),
        "terminal_state_cannot_transition": not validate_order_transition("FILLED", "CANCELLED"),
        "long_pnl_formula": abs(realized_pnl_long(100, 110, 1, 0.1, 0.1) - 9.8) < 1e-12,
        "short_pnl_formula": abs(realized_pnl_short(100, 90, 1, 0.1, 0.1) - 9.8) < 1e-12,
        "blind_projection_removes_reconstructable_fields": (
            blind_safe_operational_projection(
                {
                    "status": "HEALTHY",
                    "queue_backlog": 2,
                    "side": "LONG",
                    "fill_price": 123.0,
                    "realized_pnl": 7.0,
                }
            )
            == {"status": "HEALTHY", "queue_backlog": 2}
        ),
    }
