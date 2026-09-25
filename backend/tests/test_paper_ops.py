from app.paper_ops import (
    canonical_book_hash,
    canonical_book_payload,
    walk_canonical_quote_notional,
    walk_canonical_base_quantity,
    quote_fee_amount,
    remaining_quote_notional,
    smoke_cases,
)


def test_spec_005_paper_ops_smoke_cases_all_pass():
    checks = smoke_cases()
    assert checks
    assert all(checks.values()), checks


def test_equivalent_books_hash_identically():
    a = canonical_book_hash(
        provider="OKX",
        pair="zec/usdt",
        provider_ts_ms=123,
        bids=[["10.000", "1"], ["10", "2"], ["9.5", "2"]],
        asks=[["10.5", "1.0"], ["11.0", "2"]],
    )
    b = canonical_book_hash(
        provider="okx",
        pair="ZEC/USDT",
        provider_ts_ms=123,
        bids=[["9.5000", "2.0000"], ["10", "3.000"]],
        asks=[["11", "2"], ["10.500", "1"]],
    )
    assert a == b


def test_zero_qty_dropped_and_duplicate_price_merged():
    payload = canonical_book_payload(
        provider="okx",
        pair="BTC/USDT",
        provider_ts_ms=999,
        bids=[["100", "1"], ["100.0", "2"], ["99", "0"]],
        asks=[["101", "1"]],
    )
    assert payload["bids"] == [["100", "3"]]


def test_more_than_18_fractional_digits_is_rejected_without_rounding():
    try:
        canonical_book_hash(
            provider="okx",
            pair="BTC/USDT",
            provider_ts_ms=999,
            bids=[["100.1234567890123456789", "1"]],
            asks=[["101", "1"]],
        )
    except ValueError as exc:
        assert str(exc) == "decimal_scale_exceeded"
    else:
        raise AssertionError("19 fractional digits must be rejected")


def test_crossed_book_rejected():
    try:
        canonical_book_hash(
            provider="okx",
            pair="BTC/USDT",
            provider_ts_ms=999,
            bids=[["102", "1"]],
            asks=[["101", "1"]],
        )
    except ValueError as exc:
        assert str(exc) == "crossed_or_locked_book"
    else:
        raise AssertionError("crossed book must fail closed")


def test_decimal_book_walk_partial_and_complete():
    partial = walk_canonical_quote_notional(
        side="BUY",
        quote_notional="50",
        bids=[["99.9", "10"]],
        asks=[["100", "0.2"]],
    )
    assert partial["complete"] is False
    assert partial["filled_quote_notional"] == "20"
    assert partial["filled_base_quantity"] == "0.2"

    full = walk_canonical_quote_notional(
        side="BUY",
        quote_notional="50",
        bids=[["99.9", "10"]],
        asks=[["100", "0.2"], ["100.5", "1"]],
    )
    assert full["complete"] is True
    assert full["levels_used"] == 2
    assert full["vwap"] is not None


def test_execution_money_helpers_match_db_precision():
    assert remaining_quote_notional("50", "20.123456789012") == "29.876543210988"
    assert quote_fee_amount("50", "0.001") == "0.05"


def test_base_quantity_exit_walk_uses_remaining_base_qty():
    result = walk_canonical_base_quantity(
        side="SELL",
        base_quantity="0.5",
        bids=[["100", "0.2"], ["99.5", "1"]],
        asks=[["100.5", "2"]],
    )
    assert result["complete"] is True
    assert result["filled_base_quantity"] == "0.5"
    assert result["levels_used"] == 2
    assert result["vwap"] is not None
