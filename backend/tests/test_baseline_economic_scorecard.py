from app.main import _baseline_economic_metrics


def test_baseline_economic_metrics_split_trail_and_never_trail():
    tape = [
        {"pnl_net": 1.0, "trailing_activated": True, "hold_minutes": 60},
        {"pnl_net": 0.5, "trailing_activated": True, "hold_minutes": 30},
        {"pnl_net": -0.75, "trailing_activated": False, "hold_minutes": 120},
        {"pnl_net": -0.25, "trailing_activated": False, "hold_minutes": 240},
    ]
    out = _baseline_economic_metrics(tape, block_minutes=30 * 24 * 60, capital=100)
    assert out["trades"] == 4
    assert out["net_pnl_usdt"] == 0.5
    assert out["net_expectancy_usdt_per_trade"] == 0.125
    assert out["profit_factor_net"] == 1.5
    assert out["trail_hit"]["net_pnl_usdt"] == 1.5
    assert out["trail_never_reached"]["net_pnl_usdt"] == -1.0
    assert out["time_in_market_percent"] == round(100 * 450 / (30 * 24 * 60), 4)
