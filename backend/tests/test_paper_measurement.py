"""P1 acceptance tests use synthetic candles and mocked I/O only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
import json
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock, patch

import pytest

from app.costs import book_costs
from app.paper_measurement import (
    GATE_MS, STEP_MS, MeasurementError, RunEvidence, candle_window,
    canonical_json, closed_only, cost_book_reason, engine_sha256,
    evaluate_test, hash_json, replay_run, simulation_tape,
)
from app.simulation import simulate
from test_simulation import candles as legacy_candles, settings

START = 1_700_000_000_000 // STEP_MS * STEP_MS
PAIR = "TEST/USDT"


def test_config(label="official", start_ms=START, warmup=24):
    config = settings(warmup_bars=warmup, timeframe="5m", selected_pairs=[PAIR])
    return {"id": "test-1", "label": label, "pair": PAIR, "timeframe": "5m",
            "start_ms": start_ms, "gate_ms": GATE_MS, "gate_trades": 30,
            "max_book_age_ms": 60_000, "settings": config,
            "settings_sha256": hash_json(config)}


test_config.__test__ = False


def source_candles(prices, first=START):
    return [{"open_time": first + index * STEP_MS,
             "close_time": first + (index + 1) * STEP_MS - 1,
             "open": close * .999, "high": close * 1.002, "low": close * .998,
             "close": close, "volume": 1_000_000, "quote_volume": close * 1_000_000,
             "confirm": 1} for index, close in enumerate(prices)]


@lru_cache(maxsize=16)
def archive(start_ms, as_of_ms, warmup):
    window = candle_window(start_ms, as_of_ms, warmup)
    return tuple(closed_only(source_candles([100.] * window.expected_count, window.first_open_ms),
                             start_ms=start_ms, as_of_ms=as_of_ms, warmup_bars=warmup))


def profile(ts):
    return book_costs({"asks": [["100.1", "100"]], "bids": [["99.9", "100"]], "ts": str(ts)}, 50)


def evidence(test=None, *, run_id="run-1", seq=1, as_of=None, pnls=None, dd=5):
    test = test or test_config()
    as_of = as_of if as_of is not None else test["start_ms"] + GATE_MS + STEP_MS
    pnls = [1] * 30 if pnls is None else pnls
    trades = [{"run_id": run_id, "ordinal": index, "pair": PAIR,
               "opened_at_ms": test["start_ms"] + index * STEP_MS,
               "closed_at_ms": test["start_ms"] + (index + 1) * STEP_MS,
               "entry_rate": 100, "exit_rate": 100 + pnl if pnl is not None else 100,
               "stake": 50, "profit_usdt": pnl, "exit_reason": "trailing_profit", "raw": {}}
              for index, pnl in enumerate(pnls)]
    data = archive(test["start_ms"], as_of, test["settings"]["warmup_bars"])
    window = candle_window(test["start_ms"], as_of, test["settings"]["warmup_bars"])
    costs = profile(as_of)
    row = {"id": run_id, "test_id": test["id"], "seq": seq, "status": "valid",
           "as_of_ms": as_of, "test_end_ms": as_of, "boundary_ms": window.boundary_ms,
           "last_candle_open_ms": window.last_open_ms,
           "last_candle_close_raw_ms": data[-1]["close_time_raw_ms"],
           "candle_first_open_ms": window.first_open_ms, "candle_last_open_ms": window.last_open_ms,
           "candle_count": len(data), "expected_count": window.expected_count,
           "code_commit": "b200950cffa6c349a6044c4ff70d28f9d7b9e8dd",
           "engine_sha256": engine_sha256(), "settings_sha256": test["settings_sha256"],
           "candles_sha256": hash_json(data), "cost_profile": costs,
           "cost_profile_sha256": hash_json(costs), "book_ts_ms": as_of,
           "cost_fetched_at_ms": as_of, "trade_count": len(trades), "open_count": 0,
           "tape_sha256": hash_json(trades), "summary": {"max_drawdown_percent": dd}}
    return RunEvidence(row, data, trades, [], "verified")


def with_tape(item, trades):
    return replace(item, trades=trades, run={**item.run, "trade_count": len(trades),
                                            "tape_sha256": hash_json(trades)})


def evaluate(items, test=None, **kwargs):
    return evaluate_test(test or test_config(), items, chronology_verified=True, **kwargs)


def test_at4_closed_only_excludes_live_candle_and_never_closes_after_as_of():
    rows = source_candles([100] * 25 + [90, 91, 80])
    rows[-1]["confirm"] = 0
    end = rows[-2]["open_time"] + STEP_MS
    loaded = closed_only(rows, start_ms=START, as_of_ms=end, warmup_bars=0)
    assert len(loaded) == len(rows) - 1
    assert all(row["open_time_ms"] + STEP_MS <= end for row in loaded)
    engine_rows = [{**row, "open_time": row["open_time_ms"],
                    "close_time": row["close_time_raw_ms"]} for row in loaded]
    result = simulate({PAIR: engine_rows}, settings(stop_loss_percent=5),
                      trading_start_time=START, force_close_at_end=False,
                      cost_models={PAIR: profile(end)})
    assert result["open_positions"] == 1 and result["trades"] == []
    assert all(row["closed_at_ms"] <= end for row in simulation_tape("r", result))


@pytest.mark.parametrize("alter", ["missing", "duplicate", "future", "unconfirmed_gap"])
def test_loader_rejects_incomplete_archive(alter):
    rows = source_candles([100] * 3)
    end = START + 3 * STEP_MS
    if alter == "missing":
        rows.pop(1)
    elif alter == "duplicate":
        rows.append(deepcopy(rows[1]))
    elif alter == "future":
        rows += source_candles([100], end)
    else:
        rows[1]["confirm"] = 0
    with pytest.raises(MeasurementError, match="incomplete_candles"):
        closed_only(rows, start_ms=START, as_of_ms=end, warmup_bars=0)


@pytest.mark.parametrize("confirmation", [None, True, False, 2])
def test_missing_confirmation_is_not_inferred(confirmation):
    rows = source_candles([100])
    rows[0]["confirm"] = confirmation
    with pytest.raises(MeasurementError):
        closed_only(rows, start_ms=START, as_of_ms=START + STEP_MS, warmup_bars=0)


def test_at5_replay_matches_saved_tape_and_summary_without_network(monkeypatch):
    test, item = replay_fixture()
    original = deepcopy(item)
    reader = lambda run_id: (test, item)
    assert replay_run(item.run["id"], read_snapshot=reader) == {
        "run_id": item.run["id"], "status": "valid", "reason": "replay_match",
        "tape_match": True, "summary_match": True, "open_count_match": True}
    assert item == original
    changed = replace(item, run={**item.run, "summary": {**item.run["summary"], "realized_profit": 999}})
    assert replay_run(item.run["id"], read_snapshot=lambda _: (test, changed))["reason"] == "replay_mismatch"
    wrong_engine = replace(item, run={**item.run, "engine_sha256": "a" * 64})
    with patch("app.paper_measurement.simulate", side_effect=AssertionError("must not simulate")):
        assert replay_run(item.run["id"], read_snapshot=lambda _: (test, wrong_engine))["reason"] == "engine_mismatch"


def replay_fixture():
    test = test_config()
    test["settings"]["stop_loss_percent"] = 5
    test["settings_sha256"] = hash_json(test["settings"])
    prices = [100.] * 25 + [90., 91., 80.] + [100.] * 32
    rows = source_candles(prices, START - 24 * STEP_MS)
    end = rows[-1]["open_time"] + STEP_MS
    data = closed_only(rows, start_ms=START, as_of_ms=end, warmup_bars=24)
    result = simulate({PAIR: rows}, test["settings"], trading_start_time=START,
                      force_close_at_end=False, cost_models={PAIR: profile(end)})
    assert len(result["trades"]) > 0 and result["open_positions"] == 0
    item = evidence(test, as_of=end, pnls=[])
    item = replace(item, candles=data, run={**item.run, "candles_sha256": hash_json(data),
                                          "summary": result["metrics"]})
    return test, with_tape(item, simulation_tape(item.run["id"], result))


@pytest.mark.parametrize("first_status", ["valid", "invalid"])
def test_at12_at20_missing_own_tape_is_candidate_not_zero_trades(first_status):
    first = evidence()
    first = replace(first, trades=None, run={**first.run, "status": first_status})
    later = evidence(run_id="run-2", seq=2, as_of=first.run["test_end_ms"] + STEP_MS, pnls=[1] * 40)
    result = evaluate([later, first])
    assert result["verdict"] == "INVALID"
    assert result["candidate_run_id"] == "run-1"
    assert result["reason"] == "unverifiable_candidate"
    assert result["invalid_reason"] == "missing_run_specific_tape"
    assert result["eligibility_trace"][0]["eligibility"] == "unverifiable"
    assert len(result["eligibility_trace"]) == 1


@pytest.mark.parametrize("status", ["valid", "invalid"])
def test_at13_pre_gate_invalid_run_is_not_a_test_verdict(status):
    item = RunEvidence({"id": "pre-gate", "seq": 1, "test_end_ms": START + GATE_MS - 1,
                        "status": status}, None, None, None)
    result = evaluate_test(test_config(), [item])
    assert result["verdict"] == "NOT_EVALUATED"
    assert result["candidate_run_id"] is None and result["eligibility_trace"] == []


def test_at14_tape_is_clipped_at_t_star_but_dd_is_full_run():
    item = evidence(pnls=[1] * 30 + [-5], dd=16)
    trades = deepcopy(item.trades)
    trades[-1].update(opened_at_ms=START + GATE_MS, closed_at_ms=item.run["test_end_ms"])
    result = evaluate([with_tape(item, trades)])
    assert result["t_star_ms"] == START + GATE_MS
    assert result["net_pnl"] == 30 and result["trades_count"] == 30
    assert result["dd_value"] == 16 and result["verdict"] == "FAIL"


def test_at19_gate_uses_test_end_not_raw_close_or_boundary():
    # S is deliberately off the 5m grid: B is still before the exact 30d gate.
    test = test_config(start_ms=START + 1)
    end = test["start_ms"] + GATE_MS
    item = evidence(test, as_of=end)
    assert item.run["boundary_ms"] < end
    result = evaluate([item], test)
    assert result["verdict"] == "PASS" and result["t_star_ms"] == end


def test_candidate_own_c30_controls_t_star_and_includes_tied_closes():
    item = evidence(pnls=[1] * 31, as_of=START + GATE_MS + 2 * STEP_MS)
    trades = deepcopy(item.trades)
    for row in trades[29:]:
        row["closed_at_ms"] = START + GATE_MS + STEP_MS
    result = evaluate([with_tape(item, trades)])
    assert result["c30_ms"] == result["t_star_ms"] == START + GATE_MS + STEP_MS
    assert result["trades_count"] == 31


@pytest.mark.parametrize("age,reason", [(0, None), (60_000, None), (60_001, "book_too_old"),
                                        (-1, "book_from_future"), (None, "cost_profile_unavailable")])
def test_at30_cost_book_both_sides_and_missing_time(age, reason):
    fetched = START + age if age is not None else None
    assert cost_book_reason(START, fetched) == reason
    item = evidence()
    item = replace(item, run={**item.run, "cost_fetched_at_ms": item.run["book_ts_ms"] + age if age is not None else None})
    result = evaluate([item])
    assert result["verdict"] == ("PASS" if reason is None else "INVALID")
    if reason:
        assert result["invalid_reason"] == reason
    assert cost_book_reason(None, START) == "cost_profile_unavailable"


@pytest.mark.parametrize("pnls,verdict,pf,infinite,expectancy", [
    ([1] * 30, "PASS", None, True, 1),
    ([0] * 30, "FAIL", None, False, 0),
    ([1.09, -1] + [0] * 28, "FAIL", 1.09, False, .003),
    ([1.10, -1] + [0] * 28, "PASS", 1.1, False, 1 / 300),
    ([-1] * 30, "FAIL", 0, False, -1),
])
def test_at31_net_pf_and_zero_pnl_semantics(pnls, verdict, pf, infinite, expectancy):
    result = evaluate([evidence(pnls=pnls)])
    assert result["verdict"] == verdict
    assert result["profit_factor"] == pf and result["pf_infinite"] is infinite
    assert result["expectancy"] == pytest.approx(expectancy)


@pytest.mark.parametrize("offset,warmup", [(0, 0), (1, 24), (STEP_MS - 1, 40)])
def test_at32_range_alignment_and_warmup(offset, warmup):
    start = START + offset
    window = candle_window(start, START + GATE_MS + 1, warmup)
    assert window.first_open_ms == START - warmup * STEP_MS
    assert window.last_open_ms == START + GATE_MS - STEP_MS
    assert window.expected_count == 8640 + warmup


def test_at33_thirty_trades_before_time_gate_remain_not_evaluated():
    item = evidence(as_of=START + GATE_MS - 1)
    assert len(item.trades) == 30
    result = evaluate([item])
    assert result["verdict"] == "NOT_EVALUATED" and result["candidate_run_id"] is None


@pytest.mark.parametrize("close_offset", [STEP_MS - 1, STEP_MS])
def test_at34a_boundary_does_not_use_close_time_convention(close_offset):
    rows = source_candles([100])
    rows[0]["close_time"] = START + close_offset
    loaded = closed_only(rows, start_ms=START, as_of_ms=START + STEP_MS, warmup_bars=0)
    assert loaded[0]["close_time_raw_ms"] == START + close_offset
    window = candle_window(START, START + STEP_MS, 0)
    assert window.boundary_ms == START + STEP_MS
    assert window.last_open_ms == START and window.expected_count == 1


def test_at40_absent_ticks_and_unpersisted_claims_are_not_runs():
    assert evaluate([])["verdict"] == "NOT_EVALUATED"
    item = evidence()
    assert evaluate([item])["candidate_run_id"] == "run-1"
    bad = replace(item, run={**item.run, "status": "invalid", "invalid_reason": "incomplete_candles"})
    result = evaluate([bad])
    assert result["verdict"] == "INVALID" and result["candidate_run_id"] == "run-1"


def test_at42_infinite_pf_does_not_override_dd_and_empty_tape_is_not_missing():
    assert evaluate([evidence(dd=15.01)])["verdict"] == "FAIL"
    result = evaluate([evidence(pnls=[])])
    assert result["verdict"] == "NOT_EVALUATED"
    assert result["eligibility_trace"][0]["eligibility"] == "proven_ineligible"


def test_complete_ineligible_run_does_not_block_next_candidate():
    first = evidence(pnls=[1] * 29)
    later = evidence(run_id="run-2", seq=2, as_of=first.run["test_end_ms"] + STEP_MS)
    result = evaluate([later, first])
    assert result["candidate_run_id"] == "run-2"
    assert [row["eligibility"] for row in result["eligibility_trace"]] == ["proven_ineligible", "eligible"]


def test_pending_origin_prevents_skipping_even_with_fewer_than_thirty_trades():
    first = replace(evidence(pnls=[1]), origin="pending")
    later = evidence(run_id="run-2", seq=2, as_of=first.run["test_end_ms"] + STEP_MS)
    result = evaluate([first, later])
    assert result["verdict"] == "NOT_EVALUATED" and result["reason"] == "origin_pending"
    assert result["pending_run_id"] == "run-1" and result["eligibility_trace"] == []
    result = evaluate([replace(first, origin="unverifiable"), later])
    assert result["verdict"] == "INVALID" and result["invalid_reason"] == "tick_origin_unverified"


def test_no_chronology_certificate_no_verdict_certification():
    result = evaluate_test(test_config(), [evidence()])
    assert result["verdict"] == "INVALID" and result["reason"] == "first_run_unverifiable"


def test_duplicate_seq_and_unassociated_tape_fail_closed():
    item = evidence()
    assert evaluate([item, item])["reason"] == "first_run_unverifiable"
    trades = deepcopy(item.trades)
    trades[0].pop("run_id")
    assert evaluate([with_tape(item, trades)])["invalid_reason"] == "missing_run_specific_tape"


@pytest.mark.parametrize("change,reason", [
    ({"tape_sha256": "bad"}, "tape_hash_mismatch"),
    ({"candles_sha256": "bad"}, "candles_hash_mismatch"),
    ({"settings_sha256": "bad"}, "settings_mismatch"),
    ({"code_commit": None}, "unknown_code_version"),
    ({"cost_profile_sha256": "bad"}, "cost_profile_unavailable"),
    ({"expected_count": 0}, "incomplete_candles"),
    ({"as_of_ms": 0}, "horizon_mismatch"),
])
def test_evidence_corruption_is_invalid(change, reason):
    item = evidence()
    result = evaluate([replace(item, run={**item.run, **change})])
    assert result["verdict"] == "INVALID" and result["invalid_reason"] == reason


@pytest.mark.parametrize("field", ["entry_rate", "exit_rate", "stake", "raw", "exit_reason"])
def test_complete_count_and_hash_cannot_substitute_for_missing_trade_fields(field):
    item = evidence()
    trades = deepcopy(item.trades)
    trades[0].pop(field)
    result = evaluate([with_tape(item, trades)])
    assert result["verdict"] == "INVALID"
    assert result["invalid_reason"] == "incomplete_trade_evidence"


def test_forced_warmup_and_null_pnl_trades_do_not_satisfy_count_gate():
    item = evidence(pnls=[1] * 32)
    trades = deepcopy(item.trades)
    trades[0]["exit_reason"] = "end_of_test"
    trades[1]["opened_at_ms"] = START - 1
    trades[2]["profit_usdt"] = None
    result = evaluate([with_tape(item, trades)])
    assert result["verdict"] == "NOT_EVALUATED"
    assert result["eligibility_trace"][0]["valid_trade_count"] == 29


def test_future_trade_cannot_be_hidden_by_filter():
    item = evidence()
    trades = deepcopy(item.trades)
    trades[0]["closed_at_ms"] = item.run["test_end_ms"] + 1
    assert evaluate([with_tape(item, trades)])["invalid_reason"] == "trade_after_as_of"


def test_scale_only_complete_eligible_candidate_is_diagnostic():
    test = test_config(label="scale")
    assert evaluate([evidence(test)], test)["verdict"] == "DIAGNOSTIC"
    assert evaluate([evidence(test, pnls=[])], test)["verdict"] == "NOT_EVALUATED"
    assert evaluate([replace(evidence(test), trades=None)], test)["verdict"] == "INVALID"


def test_canonical_json_is_exact_stable_and_rejects_nonfinite():
    assert canonical_json({"z": [-0., 1e-7, True, None], "a": 1.0}) == '{"a":1,"z":[0,0.0000001,true,null]}'
    assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2., "a": 1.})
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(MeasurementError):
            hash_json({"bad": invalid})


def legacy_outputs():
    """AT18: same synthetic inputs as the saved b200950 golden fixture."""
    from app import main
    from app.optimizer import optimize
    data = legacy_candles(([100.] * 25 + [90., 91., 80.] + [100.] * 32) * 10)
    config = settings(selected_pairs=[PAIR], timeframe="5m", stop_loss_percent=5)
    request = main.SimulationRequest(settings=main.StrategySettings(**config),
                                     start_time=data[0]["open_time"], end_time=data[-1]["close_time"],
                                     candle_limit=len(data), force_close_at_end=True)
    with patch("app.main.load_okx_candles", AsyncMock(return_value=data)), \
         patch("app.main.supabase_upsert", AsyncMock(side_effect=lambda _, row: row)), \
         patch("app.main.supabase_upsert_many", AsyncMock(side_effect=lambda _, rows: rows)):
        historical = asyncio.run(main.run_standalone_simulation(request))
    for key in ("id", "started_at", "finished_at"):
        historical["run"].pop(key)
    optimized = optimize({(PAIR, "5m"): data}, config, 2, locked_pairs=[PAIR])
    # Avoid gzip header differences between supported Python builds; raw snapshot
    # hashes/counts and every economic/qualification output remain in the golden.
    def without_compressed_bytes(value):
        if isinstance(value, dict):
            return {k: without_compressed_bytes(v) for k, v in value.items()
                    if not (k == "data" and value.get("encoding") == "gzip+base64")}
        if isinstance(value, list):
            return [without_compressed_bytes(v) for v in value]
        return value
    return without_compressed_bytes({"historical": historical, "optimizer": optimized})


def test_at18_legacy_endpoint_and_optimizer_match_b200950():
    expected = json.loads((Path(__file__).parent / "fixtures/paper_p1_legacy_b200950.json").read_text())
    assert legacy_outputs() == expected


@pytest.mark.parametrize("certificate", ["false", "true", 1, None])
def test_review_chronology_requires_boolean_true(certificate):
    result = evaluate_test(test_config(), [evidence()], chronology_verified=certificate)
    assert result["verdict"] == "INVALID"
    assert result["reason"] == "first_run_unverifiable"


@pytest.mark.parametrize("field,value", [("code_commit", "unknown"), ("engine_sha256", "unknown"),
                                         ("code_commit", True), ("engine_sha256", 123)])
def test_review_code_identity_requires_full_hashes(field, value):
    item = evidence()
    result = evaluate([replace(item, run={**item.run, field: value})])
    assert result["verdict"] == "INVALID"
    assert result["invalid_reason"] == "unknown_code_version"


@pytest.mark.parametrize("with_later_candidate", [False, True])
def test_review_pre_gate_missing_sequence_does_not_block_test(with_later_candidate):
    pre = RunEvidence({"id": "pre", "test_end_ms": START + GATE_MS - 1,
                       "status": "invalid"}, None, None, None)
    result = evaluate([pre] + ([evidence(seq=2)] if with_later_candidate else []))
    assert result["verdict"] == ("PASS" if with_later_candidate else "NOT_EVALUATED")
    assert all(row["run_id"] != "pre" for row in result["eligibility_trace"])


def open_position():
    return {"run_id": "run-1", "ordinal": 0, "pair": PAIR, "opened_at_ms": START,
            "entry_rate": 100, "stake": 50, "closed_at_ms": None, "raw": {}}


@pytest.mark.parametrize("field", ["entry_rate", "stake", "raw"])
def test_review_partial_open_position_is_missing_evidence(field):
    item = evidence()
    position = open_position()
    position.pop(field)
    result = evaluate([replace(item, open_positions=[position], run={**item.run, "open_count": 1})])
    assert result["verdict"] == "INVALID"
    assert result["invalid_reason"] == "incomplete_open_position_evidence"


def test_review_complete_open_position_is_accepted_without_forced_exit():
    item = evidence()
    result = evaluate([replace(item, open_positions=[open_position()], run={**item.run, "open_count": 1})])
    assert result["verdict"] == "PASS" and result["trades_count"] == 30


@pytest.mark.parametrize("kind", ["null_trade", "null_position", "null_candle", "list_profile"])
def test_review_malformed_evidence_returns_invalid_instead_of_raising(kind):
    item = evidence()
    if kind == "null_trade":
        item = replace(item, trades=[None] + list(item.trades[1:]))
    elif kind == "null_position":
        item = replace(item, open_positions=[None], run={**item.run, "open_count": 1})
    elif kind == "null_candle":
        item = replace(item, candles=[None] + list(item.candles[1:]))
    else:
        item = replace(item, run={**item.run, "cost_profile": [1], "cost_profile_sha256": hash_json([1])})
    result = evaluate([item])
    assert result["verdict"] == "INVALID"
    assert result["eligibility_trace"][0]["eligibility"] == "unverifiable"


def test_review_missing_replay_snapshot_returns_invalid():
    result = replay_run("missing", read_snapshot=lambda run_id: {}[run_id])
    assert result == {"run_id": "missing", "status": "invalid", "reason": "missing_run_evidence"}


@pytest.mark.parametrize("profits", [[1e308, -1e-308] + [0] * 28,
                                     [1e308] * 30,
                                     [Decimal("1e-400")] * 30])
def test_review_metrics_must_remain_finite_and_preserve_nonzero_values(profits):
    item = evidence()
    trades = deepcopy(item.trades)
    for row, profit in zip(trades, profits):
        row["profit_usdt"] = profit
    result = evaluate([with_tape(item, trades)])
    assert result["verdict"] == "INVALID"
    assert result["invalid_reason"] == "unrepresentable_metrics"
    assert result["eligibility_trace"][0]["eligibility"] == "unverifiable"
    json.dumps(result, allow_nan=False)


def test_review_decimal_context_cannot_change_pf_boundary():
    item = evidence(pnls=[1.09, -1] + [0] * 28)
    with localcontext() as context:
        context.prec = 2
        result = evaluate([item])
    assert result["verdict"] == "FAIL"
    assert result["profit_factor"] == 1.09


def test_review_large_finite_drawdown_does_not_crash_quantization():
    result = evaluate([evidence(dd=1e30)])
    assert result["verdict"] == "FAIL"
    assert result["dd_value"] == 1e30
