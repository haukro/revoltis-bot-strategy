from app.tsmom_c_v1 import (
    ATR_MULTIPLE,
    ATR_PERIOD,
    BREAKOUT_N,
    long_net_return,
    short_net_return,
    smoke_cases,
)


def test_test_spec_003_constants_are_locked():
    assert BREAKOUT_N == 24
    assert ATR_PERIOD == 24
    assert ATR_MULTIPLE == 2.0


def test_tsmom_c_v1_synthetic_smoke_cases_all_pass():
    checks = smoke_cases()
    assert checks
    assert all(checks.values()), checks


def test_long_and_short_costs_are_negative_at_flat_price():
    profile = {
        "entry_cost_rate": 0.0010005926681029335,
        "exit_cost_rate": 0.0010005926681029335,
    }
    assert long_net_return(100.0, 100.0, profile) < 0
    assert short_net_return(100.0, 100.0, profile) < 0
