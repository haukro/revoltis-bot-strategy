import math

from app.tsmom_b_v1 import (
    ATR_MULTIPLE,
    BREAKOUT_N,
    ATR_PERIOD,
    aggregate_1h_ohlc,
    hourly_signals,
    long_net_return,
    short_net_return,
    smoke_cases,
    wilder_atr,
)


def test_test_spec_002_constants_are_locked():
    assert BREAKOUT_N == 24
    assert ATR_PERIOD == 24
    assert ATR_MULTIPLE == 2.0


def test_tsmom_b_v1_synthetic_smoke_cases_all_pass():
    checks = smoke_cases()
    assert checks
    assert all(checks.values()), checks


def test_long_and_short_round_trip_are_both_net_negative_after_costs():
    profile = {"entry_cost_rate": 0.0011, "exit_cost_rate": 0.0012}
    assert long_net_return(100.0, 100.0, profile) < 0
    assert short_net_return(100.0, 100.0, profile) < 0


def test_short_profit_has_expected_direction():
    profile = {"entry_cost_rate": 0.001, "exit_cost_rate": 0.001}
    assert short_net_return(100.0, 90.0, profile) > 0
    assert short_net_return(100.0, 110.0, profile) < 0
