from app.spec006_adapter import ReferenceState, compute_reference_transition, replay_bootstrap_state
from app.tsmom_b_v1 import FIVE_MIN_MS, ONE_HOUR_MS, simulate_b_v1


PROFILE = {"entry_cost_rate": .0011, "exit_cost_rate": .0011}


def candle(ts, o, h, l, c):
    return {"open_time": ts, "close_time": ts + FIVE_MIN_MS - 1, "open": o, "high": h, "low": l, "close": c, "volume": 1.0, "quote_volume": 1.0}


def base_long_fixture():
    rows = []
    for hour in range(27):
        for j in range(12):
            ts = hour * ONE_HOUR_MS + j * FIVE_MIN_MS
            if hour < 24:
                rows.append(candle(ts, 100, 101, 99, 100))
            elif hour == 24:
                close = 102.0 if j == 11 else 100.0
                high = 102.2 if j == 11 else 101.0
                rows.append(candle(ts, 100, high, 99.5, close))
            else:
                rows.append(candle(ts, 102.5, 103.0, 101.5, 102.4))
    return rows


def base_short_fixture():
    rows = []
    for hour in range(27):
        for j in range(12):
            ts = hour * ONE_HOUR_MS + j * FIVE_MIN_MS
            if hour < 24:
                rows.append(candle(ts, 100, 101, 99, 100))
            elif hour == 24:
                close = 98.0 if j == 11 else 100.0
                low = 97.8 if j == 11 else 99.0
                rows.append(candle(ts, 100, 100.5, low, close))
            else:
                rows.append(candle(ts, 97.5, 98.0, 96.5, 97.4))
    return rows


def run_incremental(rows, start_ms, end_ms):
    state = ReferenceState()
    actions = []
    for idx, bar in enumerate(rows):
        if int(bar["open_time"]) < start_ms or int(bar["close_time"]) > end_ms:
            continue
        state, emitted = compute_reference_transition(
            history_through_current=rows[: idx + 1],
            prior_state=state,
            strategy_version_id="TEST-SPEC-002",
            pair="ZEC/USDT",
        )
        actions.extend(emitted)
    return state, actions


def official(rows, start_ms, end_ms):
    return simulate_b_v1(
        rows, rows, evaluation_start_ms=start_ms, evaluation_end_ms=end_ms,
        stake_amount=50, initial_capital=100, cost_profile=PROFILE,
    )


def assert_first_trade_matches_actions(outcome, actions):
    trade = outcome["trades"][0]
    entries = [a for a in actions if a["action_type"] == "ENTRY"]
    exits = [a for a in actions if a["action_type"] == "EXIT_TO_FLAT"]
    assert len(entries) == 1
    assert len(exits) == 1
    assert entries[0]["required_execution_time_ms"] == trade["entry_time"]
    assert entries[0]["reference_price"] == trade["entry_price"]
    assert exits[0]["required_execution_time_ms"] == trade["exit_time"]
    assert exits[0]["reference_price"] == trade["exit_price"]
    assert exits[0]["reason_code"] == trade["exit_reason"]


def test_same_bar_initial_stop_matches_frozen_b():
    rows = base_long_fixture()
    entry_idx = next(i for i, r in enumerate(rows) if r["open_time"] == 25 * ONE_HOUR_MS)
    rows[entry_idx] = candle(25 * ONE_HOUR_MS, 102.5, 103.0, 95.0, 100.0)
    start, end = 24 * ONE_HOUR_MS, 27 * ONE_HOUR_MS - 1
    outcome = official(rows, start, end)
    _, actions = run_incremental(rows, start, end)
    assert outcome["trades"][0]["exit_reason"] == "initial_stop"
    assert_first_trade_matches_actions(outcome, actions)


def test_gap_through_and_no_retroactive_tighten_match_frozen_b():
    rows = base_long_fixture()
    entry_idx = next(i for i, r in enumerate(rows) if r["open_time"] == 25 * ONE_HOUR_MS)
    rows[entry_idx] = candle(25 * ONE_HOUR_MS, 102.5, 110.0, 100.0, 105.0)
    rows[entry_idx + 1] = candle(25 * ONE_HOUR_MS + FIVE_MIN_MS, 105.0, 105.5, 104.5, 105.0)
    start, end = 24 * ONE_HOUR_MS, 27 * ONE_HOUR_MS - 1
    outcome = official(rows, start, end)
    _, actions = run_incremental(rows, start, end)
    assert outcome["trades"][0]["exit_reason"] == "chandelier_stop_gap"
    assert outcome["trades"][0]["exit_time"] == 25 * ONE_HOUR_MS + FIVE_MIN_MS
    assert_first_trade_matches_actions(outcome, actions)


def test_chandelier_touch_matches_frozen_b():
    rows = base_long_fixture()
    entry_idx = next(i for i, r in enumerate(rows) if r["open_time"] == 25 * ONE_HOUR_MS)
    rows[entry_idx] = candle(25 * ONE_HOUR_MS, 102.5, 106.0, 100.0, 105.0)
    # Keep next open above tightened stop, but touch it intrabar.
    rows[entry_idx + 1] = candle(25 * ONE_HOUR_MS + FIVE_MIN_MS, 105.0, 105.5, 101.0, 103.0)
    start, end = 24 * ONE_HOUR_MS, 27 * ONE_HOUR_MS - 1
    outcome = official(rows, start, end)
    _, actions = run_incremental(rows, start, end)
    assert outcome["trades"][0]["exit_reason"] == "chandelier_stop"
    assert_first_trade_matches_actions(outcome, actions)


def test_action_idempotency_replay_is_stable():
    rows = base_long_fixture()
    idx = next(i for i, r in enumerate(rows) if r["open_time"] == 25 * ONE_HOUR_MS)
    history = rows[: idx + 1]
    a_state, a = compute_reference_transition(history_through_current=history, prior_state=ReferenceState(), strategy_version_id="TEST-SPEC-002", pair="ZEC/USDT")
    b_state, b = compute_reference_transition(history_through_current=history, prior_state=ReferenceState(), strategy_version_id="TEST-SPEC-002", pair="ZEC/USDT")
    assert a == b
    assert a_state == b_state


def test_short_same_bar_initial_stop_matches_frozen_b():
    rows = base_short_fixture()
    entry_idx = next(i for i, r in enumerate(rows) if r["open_time"] == 25 * ONE_HOUR_MS)
    rows[entry_idx] = candle(25 * ONE_HOUR_MS, 97.5, 105.0, 96.5, 99.0)
    start, end = 24 * ONE_HOUR_MS, 27 * ONE_HOUR_MS - 1
    outcome = official(rows, start, end)
    _, actions = run_incremental(rows, start, end)
    assert outcome["trades"][0]["side"] == "short"
    assert outcome["trades"][0]["exit_reason"] == "initial_stop"
    assert_first_trade_matches_actions(outcome, actions)


def test_signal_while_open_does_not_create_second_incremental_entry():
    rows = base_long_fixture()
    start, end = 24 * ONE_HOUR_MS, 27 * ONE_HOUR_MS - 1
    outcome = official(rows, start, end)
    _, actions = run_incremental(rows, start, end)
    entries = [a for a in actions if a["action_type"] == "ENTRY"]
    assert outcome["ignored_signals_while_open"] >= 1
    assert len(entries) == 1


def test_bootstrap_replay_returns_reference_state_without_dispatch_side_effects():
    rows = base_long_fixture()
    cutoff = 25 * ONE_HOUR_MS + 3 * FIVE_MIN_MS
    state = replay_bootstrap_state(
        rows,
        evaluation_start_ms=24 * ONE_HOUR_MS,
        cutoff_open_ms=cutoff,
        strategy_version_id="TEST-SPEC-002",
        pair="ZEC/USDT",
    )
    assert state.reference_position_state == "LONG"
    assert state.reference_entry_time == 25 * ONE_HOUR_MS


def test_bootstrap_requires_exact_cutoff_bar():
    rows = base_long_fixture()
    missing = 25 * ONE_HOUR_MS + 7 * FIVE_MIN_MS
    rows = [row for row in rows if row["open_time"] != missing]
    try:
        replay_bootstrap_state(
            rows,
            evaluation_start_ms=24 * ONE_HOUR_MS,
            cutoff_open_ms=missing,
            strategy_version_id="TEST-SPEC-002",
            pair="ZEC/USDT",
        )
    except ValueError as exc:
        assert str(exc) in {"bootstrap_cutoff_missing", "non_contiguous_5m_history"}
    else:
        raise AssertionError("missing cutoff must fail closed")
