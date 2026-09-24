"""Operational helpers for IMPLEMENTATION-SPEC-005.

No alpha logic. No live execution.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Iterable


MAX_CANONICAL_FRACTIONAL_DIGITS = 18
EXECUTION_DECIMAL_PLACES = 12
EXECUTION_QUANTUM = Decimal("0.000000000001")


def _canonical_decimal(value: Any) -> str:
    """Canonical exact base-10 string used in immutable book hashing.

    No rounding is allowed. More than 18 fractional digits is rejected.
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_decimal") from exc
    if not d.is_finite():
        raise ValueError("non_finite_decimal")

    sign, digits, exponent = d.as_tuple()
    if exponent < -MAX_CANONICAL_FRACTIONAL_DIGITS:
        raise ValueError("decimal_scale_exceeded")

    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s.startswith("+"):
        s = s[1:]
    if s in ("-0", ""):
        s = "0"
    return s


def _execution_decimal(value: Decimal) -> str:
    """Canonical persisted execution value at DB NUMERIC(30,12) precision."""
    if not value.is_finite():
        raise ValueError("non_finite_execution_decimal")
    quantized = value.quantize(EXECUTION_QUANTUM, rounding=ROUND_HALF_EVEN)
    s = format(quantized, "f").rstrip("0").rstrip(".")
    if s in ("-0", ""):
        return "0"
    return s


def quantize_execution_decimal(value: Any) -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_execution_decimal") from exc
    return _execution_decimal(d)


def quote_fee_amount(quote_notional: Any, fee_rate: Any) -> str:
    try:
        notional = Decimal(str(quote_notional))
        rate = Decimal(str(fee_rate))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_fee_input") from exc
    if notional < 0 or rate < 0 or not notional.is_finite() or not rate.is_finite():
        raise ValueError("invalid_fee_input")
    return _execution_decimal(notional * rate)


def remaining_quote_notional(intended: Any, filled: Any) -> str:
    try:
        remaining = Decimal(str(intended)) - Decimal(str(filled))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_notional_input") from exc
    if remaining < 0:
        remaining = Decimal("0")
    return _execution_decimal(remaining)


def _normalize_levels(
    levels: Iterable[Iterable[Any]],
    *,
    descending: bool,
) -> list[list[str]]:
    merged: dict[Decimal, Decimal] = {}
    for row in levels:
        vals = list(row)
        if len(vals) < 2:
            raise ValueError("invalid_book_level")
        try:
            price = Decimal(str(vals[0]))
            qty = Decimal(str(vals[1]))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("invalid_book_level") from exc
        if not price.is_finite() or not qty.is_finite():
            raise ValueError("invalid_book_level")
        if price <= 0:
            raise ValueError("invalid_book_price")
        if qty <= 0:
            continue
        # Validate scale without rounding.
        _canonical_decimal(price)
        _canonical_decimal(qty)
        merged[price] = merged.get(price, Decimal("0")) + qty

    ordered = sorted(merged.items(), key=lambda x: x[0], reverse=descending)
    out: list[list[str]] = []
    for price, qty in ordered:
        if qty <= 0:
            continue
        out.append([_canonical_decimal(price), _canonical_decimal(qty)])
    if not out:
        raise ValueError("empty_book_side")
    return out


def canonical_book_payload(
    *,
    provider: str,
    pair: str,
    provider_ts_ms: int,
    bids: Iterable[Iterable[Any]],
    asks: Iterable[Iterable[Any]],
) -> dict[str, Any]:
    if not provider or not pair:
        raise ValueError("provider_and_pair_required")
    ts = int(provider_ts_ms)
    if ts <= 0:
        raise ValueError("invalid_provider_timestamp")

    normalized_bids = _normalize_levels(bids, descending=True)
    normalized_asks = _normalize_levels(asks, descending=False)

    best_bid = Decimal(normalized_bids[0][0])
    best_ask = Decimal(normalized_asks[0][0])
    if best_bid >= best_ask:
        raise ValueError("crossed_or_locked_book")

    return {
        "provider": provider.lower(),
        "pair": pair.upper(),
        "provider_ts_ms": ts,
        "bids": normalized_bids,
        "asks": normalized_asks,
    }


def canonical_book_bytes(**kwargs: Any) -> bytes:
    payload = canonical_book_payload(**kwargs)
    # Python dict insertion order is used intentionally and locked by construction.
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_book_hash(**kwargs: Any) -> str:
    return hashlib.sha256(canonical_book_bytes(**kwargs)).hexdigest()



def walk_canonical_quote_notional(
    *,
    side: str,
    quote_notional: Any,
    bids: Iterable[Iterable[Any]],
    asks: Iterable[Iterable[Any]],
) -> dict[str, Any]:
    """Walk a canonical book using exact Decimal arithmetic.

    Returns canonical decimal strings so the caller may persist them without
    binary-float drift.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError("invalid_side")
    try:
        requested = Decimal(str(quote_notional))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_quote_notional") from exc
    if not requested.is_finite() or requested <= 0:
        raise ValueError("invalid_quote_notional")

    nb = _normalize_levels(bids, descending=True)
    na = _normalize_levels(asks, descending=False)
    best_bid = Decimal(nb[0][0])
    best_ask = Decimal(na[0][0])
    if best_bid >= best_ask:
        raise ValueError("crossed_or_locked_book")

    levels = na if side == "BUY" else nb
    best = best_ask if side == "BUY" else best_bid
    remaining = requested
    quote_filled = Decimal("0")
    base_filled = Decimal("0")
    levels_used = 0

    for price_s, qty_s in levels:
        if remaining <= 0:
            break
        price = Decimal(price_s)
        available_base = Decimal(qty_s)
        available_quote = price * available_base
        take_quote = min(remaining, available_quote)
        take_base = take_quote / price
        quote_filled += take_quote
        base_filled += take_base
        remaining -= take_quote
        levels_used += 1

    complete = remaining <= Decimal("0")
    if base_filled <= 0:
        return {
            "side": side,
            "requested_quote_notional": _execution_decimal(requested),
            "filled_quote_notional": "0",
            "filled_base_quantity": "0",
            "vwap": None,
            "best_price": _execution_decimal(best),
            "spread_bps": None,
            "impact_bps": None,
            "levels_used": 0,
            "complete": False,
        }

    vwap = quote_filled / base_filled
    mid = (best_bid + best_ask) / Decimal("2")
    spread_bps = (best_ask - best_bid) / mid * Decimal("10000")
    impact_bps = (
        (vwap / best - Decimal("1")) * Decimal("10000")
        if side == "BUY"
        else (Decimal("1") - vwap / best) * Decimal("10000")
    )

    return {
        "side": side,
        "requested_quote_notional": _execution_decimal(requested),
        "filled_quote_notional": _execution_decimal(quote_filled),
        "filled_base_quantity": _execution_decimal(base_filled),
        "vwap": _execution_decimal(vwap),
        "best_price": _execution_decimal(best),
        "spread_bps": _execution_decimal(spread_bps),
        "impact_bps": _execution_decimal(impact_bps),
        "levels_used": levels_used,
        "complete": complete,
    }

def blind_worker_projection(row: dict[str, Any], age_category: str) -> dict[str, Any]:
    """Blind-safe worker projection: no lifetime throughput counters."""
    return {
        "worker_name": row.get("worker_name"),
        "status": row.get("status"),
        "last_success_age_category": age_category,
        "last_error_code": row.get("last_error_code"),
        "software_commit": row.get("software_commit"),
    }


def blind_reconciliation_projection(
    *,
    latest_run: dict[str, Any] | None,
    severity_counts: list[dict[str, Any]],
) -> dict[str, Any]:
    safe_counts = [
        {
            "check_code": row.get("check_code"),
            "severity": row.get("severity"),
            "count": int(row.get("count") or 0),
        }
        for row in severity_counts
    ]
    return {
        "latest_status": (latest_run or {}).get("status"),
        "completed_at": (latest_run or {}).get("completed_at"),
        "audit_integrity_passed": (latest_run or {}).get("audit_integrity_passed"),
        "open_issue_counts": safe_counts,
    }


def smoke_cases() -> dict[str, bool]:
    a = canonical_book_payload(
        provider="OKX",
        pair="zec/usdt",
        provider_ts_ms=123,
        bids=[["10.0000", "1.0"], ["9.5", "2"], ["10", "2.00"], ["8", "0"]],
        asks=[["10.50", "1"], ["11.000", "2"]],
    )
    b = canonical_book_payload(
        provider="okx",
        pair="ZEC/USDT",
        provider_ts_ms=123,
        bids=[["9.5000", "2.000"], ["10.0", "3"]],
        asks=[["11", "2.0"], ["10.5", "1.0000"]],
    )
    h1 = canonical_book_hash(
        provider="OKX",
        pair="zec/usdt",
        provider_ts_ms=123,
        bids=[["10.0000", "1.0"], ["9.5", "2"], ["10", "2.00"], ["8", "0"]],
        asks=[["10.50", "1"], ["11.000", "2"]],
    )
    h2 = canonical_book_hash(
        provider="okx",
        pair="ZEC/USDT",
        provider_ts_ms=123,
        bids=[["9.5000", "2.000"], ["10.0", "3"]],
        asks=[["11", "2.0"], ["10.5", "1.0000"]],
    )

    scale_rejected = False
    try:
        canonical_book_hash(
            provider="okx",
            pair="ZEC/USDT",
            provider_ts_ms=123,
            bids=[["10.1234567890123456789", "1"]],
            asks=[["11", "1"]],
        )
    except ValueError as exc:
        scale_rejected = str(exc) == "decimal_scale_exceeded"

    crossed_rejected = False
    try:
        canonical_book_hash(
            provider="okx",
            pair="ZEC/USDT",
            provider_ts_ms=123,
            bids=[["11", "1"]],
            asks=[["10", "1"]],
        )
    except ValueError as exc:
        crossed_rejected = str(exc) == "crossed_or_locked_book"

    exact_walk = walk_canonical_quote_notional(
        side="BUY",
        quote_notional="50",
        bids=[["99.9", "10"]],
        asks=[["100", "0.2"], ["100.5", "1"]],
    )

    return {
        "canonical_format_equivalent": a == b,
        "canonical_hash_equivalent": h1 == h2,
        "bid_sort_best_outward": a["bids"][0][0] == "10",
        "ask_sort_best_outward": a["asks"][0][0] == "10.5",
        "duplicate_price_levels_merged": a["bids"][0][1] == "3",
        "zero_qty_dropped": all(level[0] != "8" for level in a["bids"]),
        "scale_over_18_rejected_without_rounding": scale_rejected,
        "crossed_book_rejected": crossed_rejected,
        "decimal_book_walk_complete": exact_walk["complete"] is True,
        "decimal_book_walk_uses_depth": exact_walk["levels_used"] == 2,
    }
