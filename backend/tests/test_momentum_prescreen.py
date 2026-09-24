from app.momentum_prescreen import (
    FIVE_MIN_MS,
    aggregate_1h,
    breakout_indices,
    analyze_timeframe,
    breakout_day_beta_r2,
)


def _series(start: int, closes: list[float]):
    rows = []
    for i, close in enumerate(closes):
        ts = start + i * FIVE_MIN_MS
        rows.append((ts, close, close))
    return rows


def test_breakout_excludes_current_bar_from_prior_20_high():
    rows = _series(0, [100.0] * 20 + [101.0])
    assert breakout_indices(rows) == {20}


def test_one_hour_aggregation_uses_only_complete_utc_hours():
    start = 0
    rows = _series(start, [100.0 + i for i in range(24)])
    hourly = aggregate_1h(rows)
    assert len(hourly) == 2
    assert hourly[0][0] == 0
    assert hourly[1][0] == 3_600_000
    assert hourly[0][2] == 111.0
    assert hourly[1][2] == 123.0


def test_overlap_and_idiosyncratic_breakouts_are_diagnostic_only():
    start = 0
    z = _series(start, [100.0] * 20 + [101.0] + [100.0] * 25)
    b = _series(start, [100.0] * 20 + [101.0] + [100.0] * 25)
    e = _series(start, [100.0] * 46)
    out = analyze_timeframe(z, b, e, forward_bars=12)
    assert out["breakouts"]["zec_count"] >= 1
    assert out["breakouts"]["btc_given_zec_same_bar_percent"] == 100.0
    assert out["breakouts"]["zec_idiosyncratic_pm3_percent"] == 0.0


def test_breakout_day_r2_detects_identical_zec_btc_returns():
    start = 0
    closes = [100.0] * 20 + [101.0, 102.0, 101.5, 103.0] + [103.0] * 30
    z = _series(start, closes)
    b = _series(start, closes)
    e = _series(start, [100.0] * len(closes))
    out = breakout_day_beta_r2(z, b, e)
    assert out["r_squared"] == 1.0
    assert out["beta_zec_on_btc"] == 1.0
