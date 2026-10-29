"""Opt-in observations must preserve the complete pre-extension engine result."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.paper_measurement import hash_json
from app.simulation import simulate
from test_paper_measurement import START, STEP_MS, profile, source_candles
from test_simulation import settings

CASES = ("flat", "open", "closed", "mixed", "open_costs", "closed_costs",
         "mixed_costs", "forced_costs", "two_pairs", "entry_gate", "time_exit", "empty")


def scenario(name):
    open_prices = [100.] * 25 + [90., 91., 91., 91.]
    closed_prices = [100.] * 25 + [90., 91., 80., 100.]
    mixed_prices = closed_prices + [100.] * 25 + [90., 91., 91.]
    prices = ([100.] * 30 if name == "flat" else
              [] if name == "empty" else
              mixed_prices if name.startswith("mixed") else
              closed_prices if name.startswith("closed") else open_prices)
    data = {"TEST/USDT": source_candles(prices)}
    config = settings(stop_loss_percent=5, max_no_trail_hours=.05 if name == "time_exit" else 0)
    kwargs = {"trading_start_time": START, "force_close_at_end": name == "forced_costs"}
    if name in ("two_pairs", "entry_gate"):
        # Reverse insertion order: the engine's event order remains authoritative.
        data = {"Z/USDT": source_candles(prices), "A/USDT": source_candles(prices)}
        config.update(initial_capital=200., max_open_trades=2)
    if name == "entry_gate":
        kwargs["entry_gate"] = lambda pair, candles, index: pair == "Z/USDT"
    if name.endswith("costs") or name in ("two_pairs", "entry_gate"):
        kwargs["cost_models"] = {pair: profile(START) for pair in data}
    return data, config, kwargs


@pytest.mark.parametrize("name", CASES)
def test_default_false_and_opt_in_preserve_frozen_legacy_output(name):
    baseline = json.loads(Path(__file__).with_name("fixtures").joinpath("paper_p3_engine_5ca2d896.json").read_text())
    data, config, kwargs = scenario(name)
    inputs = deepcopy((data, config))
    legacy = simulate(data, config, **kwargs)
    explicit_false = simulate(data, config, **kwargs, include_open_positions=False)
    observed = simulate(data, config, **kwargs, include_open_positions=True)
    rows = observed.pop("open_position_rows")
    assert observed == explicit_false == legacy
    assert hash_json(legacy) == baseline["cases"][name]["result_sha256"]
    assert legacy["open_positions"] == baseline["cases"][name]["open_count"]
    assert len(legacy["trades"]) == baseline["cases"][name]["trade_count"]
    assert "open_position_rows" not in legacy
    assert len(rows) == legacy["open_positions"]
    assert (data, config) == inputs
    for row in rows:
        assert {"pair", "entry_rate", "stake", "opened_at", "entry_ts_ms", "entry_index",
                "high_watermark", "low_watermark"}.issubset(row)
        assert row["entry_ts_ms"] == data[row["pair"]][row["entry_index"]]["close_time"]
        assert row["entry_rate"] == data[row["pair"]][row["entry_index"]]["close"]


def test_position_order_is_deterministic_and_output_is_detached():
    data, config, kwargs = scenario("two_pairs")
    first = simulate(data, config, **kwargs, include_open_positions=True)
    second = simulate(dict(reversed(list(data.items()))), config, **kwargs, include_open_positions=True)
    assert [p["pair"] for p in first["open_position_rows"]] == ["A/USDT", "Z/USDT"]
    assert first["open_position_rows"] == second["open_position_rows"]
    first["open_position_rows"][0]["stake"] = -1
    assert second["open_position_rows"][0]["stake"] == 50
    assert simulate(data, config, **kwargs, include_open_positions=True)["open_position_rows"] == second["open_position_rows"]
