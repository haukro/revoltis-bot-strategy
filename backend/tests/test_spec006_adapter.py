from app.spec006_adapter import replay_frozen_reference
from app.tsmom_b_v1 import FIVE_MIN_MS, ONE_HOUR_MS, simulate_b_v1


PROFILE = {"entry_cost_rate": .0011, "exit_cost_rate": .0011}


def candle(ts, o, h, l, c):
    return {
        "open_time": ts,
        "close_time": ts + FIVE_MIN_MS - 1,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
        "quote_volume": 1.0,
    }


def base_fixture():
    rows = []
    for hour in range(26):
        for j in range(12):
            ts = hour * ONE_HOUR_MS + j * FIVE_MIN_MS
            if hour < 24:
                rows.append(candle(ts, 100, 101, 99, 100))
            elif hour == 24:
                close = 102.0 if j == 11 else 100.0
                high = 102.2 if j == 11 else 101.0
                rows.append(candle(ts, 100, high, 99.5, close))
            else:
                if j == 0:
                    rows.append(candle(ts, 102.5, 103.0, 102.0, 102.4))
                else:
                    rows.append(candle(ts, 102.4, 102.8, 101.5, 101.8))
    return rows


def official(rows):
    return simulate_b_v1(
        rows,
        rows,
        evaluation_start_ms=24 * ONE_HOUR_MS,
        evaluation_end_ms=26 * ONE_HOUR_MS - 1,
        stake_amount=50,
        initial_capital=100,
        cost_profile=PROFILE,
    )


def reference(rows):
    return replay_frozen_reference(
        rows,
        evaluation_start_ms=24 * ONE_HOUR_MS,
        evaluation_end_ms=26 * ONE_HOUR_MS - 1,
    )


def test_incremental_reference_entry_matches_frozen_b():
    rows = base_fixture()
    engine = official(rows)
    replay = reference(rows)
    entry = next(a for a in replay["actions"] if a.action_type == "ENTRY")
    trade = engine["trades"][0]
    assert entry.required_execution_time_ms == trade["entry_time"]
    assert entry.reference_price == trade["entry_price"]
    assert entry.position_side == trade["side"].upper()


def test_incremental_reference_same_bar_initial_stop_matches_frozen_b():
    rows = base_fixture()
    idx = next(i for i, row in enumerate(rows) if row["open_time"] == 25 * ONE_HOUR_MS)
    rows[idx].update({"open": 102.5, "high": 103.0, "low": 95.0, "close": 101.0})
    engine = official(rows)
    replay = reference(rows)
    trade = engine["trades"][0]
    exits = [a for a in replay["actions"] if a.action_type == "EXIT_TO_FLAT"]
    assert trade["exit_reason"] == "initial_stop"
    assert exits[0].reason_code == trade["exit_reason"]
    assert exits[0].required_execution_time_ms == trade["exit_time"]
    assert exits[0].reference_price == trade["exit_price"]
    assert replay["state"]["reference_position_state"] == "FLAT"


def test_incremental_reference_no_retroactive_tighten_and_gap_match_frozen_b():
    rows = base_fixture()
    idx = next(i for i, row in enumerate(rows) if row["open_time"] == 25 * ONE_HOUR_MS)
    rows[idx].update({"open": 102.5, "high": 110.0, "low": 100.0, "close": 105.0})
    rows[idx + 1].update({"open": 105.0, "high": 105.5, "low": 104.5, "close": 105.0})
    engine = official(rows)
    replay = reference(rows)
    trade = engine["trades"][0]
    exit_action = next(a for a in replay["actions"] if a.action_type == "EXIT_TO_FLAT")
    assert trade["exit_reason"] == "chandelier_stop_gap"
    assert exit_action.reason_code == trade["exit_reason"]
    assert exit_action.required_execution_time_ms == trade["exit_time"]
    assert exit_action.reference_price == trade["exit_price"]


def test_incremental_reference_rejects_true_5m_gap():
    rows = base_fixture()
    del rows[300]
    try:
        reference(rows)
    except ValueError as exc:
        assert str(exc) == "reference_5m_gap"
    else:
        raise AssertionError("true 5m gap must fail closed")
