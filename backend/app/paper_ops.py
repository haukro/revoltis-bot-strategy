"""Operational helpers for IMPLEMENTATION-SPEC-005.

No alpha logic. No live execution.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


MAX_CANONICAL_FRACTIONAL_DIGITS = 18


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

    return {
        "canonical_format_equivalent": a == b,
        "canonical_hash_equivalent": h1 == h2,
        "bid_sort_best_outward": a["bids"][0][0] == "10",
        "ask_sort_best_outward": a["asks"][0][0] == "10.5",
        "duplicate_price_levels_merged": a["bids"][0][1] == "3",
        "zero_qty_dropped": all(level[0] != "8" for level in a["bids"]),
        "scale_over_18_rejected_without_rounding": scale_rejected,
        "crossed_book_rejected": crossed_rejected,
    }
