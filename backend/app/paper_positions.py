"""Map opt-in simulator observations to complete, run-owned position rows."""
from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite

from .paper_measurement import MeasurementError, hash_json


def simulation_open_positions(run_id, result, *, pair, start_ms, as_of_ms):
    count, source = result.get("open_positions"), result.get("open_position_rows")
    if type(count) is not int or count < 0 or not isinstance(source, list) or len(source) != count:
        raise MeasurementError("missing_open_positions")
    rows, seen = [], set()
    for ordinal, position in enumerate(source):
        try:
            if not isinstance(position, dict) or position["pair"] != pair:
                raise ValueError()
            opened, index = position["entry_ts_ms"], position["entry_index"]
            if (type(opened) is not int or not start_ms <= opened <= as_of_ms
                    or type(index) is not int or index < 0 or (pair, index) in seen):
                raise ValueError()
            timestamp = datetime.fromisoformat(position["opened_at"])
            if timestamp.tzinfo is None:
                raise ValueError()
            delta = timestamp.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
            if delta.days*86_400_000 + delta.seconds*1000 + delta.microseconds//1000 != opened:
                raise ValueError()
            for field in ("entry_rate", "stake", "high_watermark", "low_watermark"):
                value = position[field]
                if type(value) not in (int, float) or not isfinite(value) or value <= 0:
                    raise ValueError()
            if not position["low_watermark"] <= position["entry_rate"] <= position["high_watermark"]:
                raise ValueError()
            seen.add((pair, index))
            rows.append({"run_id": run_id, "ordinal": ordinal, "pair": pair,
                         "opened_at_ms": opened, "closed_at_ms": None,
                         "entry_rate": position["entry_rate"], "stake": position["stake"],
                         "raw": deepcopy(position)})
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise MeasurementError("incomplete_open_position_evidence") from error
    # Validate every raw value too. Never persist non-finite or non-JSON evidence.
    hash_json(rows)
    return rows
