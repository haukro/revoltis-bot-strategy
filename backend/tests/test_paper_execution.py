from app.paper_execution import (
    BLIND_FORBIDDEN_FIELDS,
    ALLOWED_ORDER_TRANSITIONS,
    blind_safe_operational_projection,
    canonical_key,
    execution_attempt_key,
    fill_idempotency_key,
    order_idempotency_key,
    outbox_idempotency_key,
    realized_pnl_long,
    realized_pnl_short,
    signal_idempotency_key,
    smoke_cases,
    validate_order_transition,
    walk_quote_notional,
)


def test_spec_004_smoke_cases_all_pass():
    checks = smoke_cases()
    assert checks
    assert all(checks.values()), checks


def test_canonical_signal_key_is_server_deterministic():
    a = signal_idempotency_key(
        "TEST-SPEC-002",
        "ZEC/USDT",
        "LONG",
        "2026-09-24T21:59:59.999Z",
        "R_TSMOM_24_LONG",
    )
    b = signal_idempotency_key(
        "TEST-SPEC-002",
        "ZEC/USDT",
        "LONG",
        "2026-09-24T21:59:59.999Z",
        "R_TSMOM_24_LONG",
    )
    assert a == b
    assert a != signal_idempotency_key(
        "TEST-SPEC-002",
        "ZEC/USDT",
        "SHORT",
        "2026-09-24T21:59:59.999Z",
        "R_TSMOM_24_LONG",
    )


def test_outbox_key_includes_event_type():
    assert outbox_idempotency_key("RISK_EVALUATE", "signal", "1", "PRETRADE") != (
        outbox_idempotency_key("BIND_FILL_ATTEMPT", "signal", "1", "PRETRADE")
    )


def test_fill_keys_include_sequence_and_attempt():
    assert fill_idempotency_key("o1", 1, "a1") != fill_idempotency_key("o1", 2, "a1")
    assert fill_idempotency_key("o1", 1, "a1") != fill_idempotency_key("o1", 1, "a2")
    assert execution_attempt_key("o1", 1) != execution_attempt_key("o1", 2)


def test_order_transition_cancel_requested_never_fills():
    assert not validate_order_transition("CANCEL_REQUESTED", "FILLED")
    assert validate_order_transition("CANCEL_REQUESTED", "CANCELLED")
    for terminal in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED", "FAILED"):
        assert ALLOWED_ORDER_TRANSITIONS[terminal] == set()


def test_book_walk_uses_depth_and_reports_partial_fill():
    result = walk_quote_notional(
        side="BUY",
        quote_notional=100.0,
        bids=[["99", "10"]],
        asks=[["100", "0.25"], ["101", "0.25"]],
    )
    assert not result.complete
    assert result.filled_quote_notional == 25 + 25.25
    assert result.filled_base_quantity == 0.5
    assert result.vwap == result.filled_quote_notional / result.filled_base_quantity
    assert result.best_price == 100
    assert result.levels_used == 2


def test_book_walk_never_accepts_missing_two_sided_book():
    try:
        walk_quote_notional(side="BUY", quote_notional=50, bids=[], asks=[["100", "1"]])
    except ValueError as exc:
        assert str(exc) == "missing_two_sided_book"
    else:
        raise AssertionError("missing two-sided book must fail closed")


def test_realized_pnl_uses_fill_vwap_and_fees_once():
    assert realized_pnl_long(100, 110, 2, 0.2, 0.3) == 19.5
    assert realized_pnl_short(100, 90, 2, 0.2, 0.3) == 19.5


def test_blind_safe_projection_removes_reconstructable_data():
    source = {
        "status": "HEALTHY",
        "queue_backlog": 3,
        "pair": "ZEC/USDT",
        "side": "LONG",
        "fill_price": 100,
        "quantity": 1,
        "realized_pnl": 5,
        "correlation_id": "secret-link",
    }
    result = blind_safe_operational_projection(source)
    assert result == {"status": "HEALTHY", "queue_backlog": 3}
    assert set(source).intersection(BLIND_FORBIDDEN_FIELDS)
