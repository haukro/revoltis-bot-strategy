"""Offline measurement primitives for B1-B8 rev3 (P1).

No scheduler, HTTP, database, clock, or persistence is used here. P2 supplies
the certified sequence and resolved origin evidence; P3 supplies source rows.
Legacy simulation and optimizer entry points do not import this module.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal, DecimalException, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Literal

from .simulation import simulate

STEP_MS = 300_000
GATE_MS = 2_592_000_000
GATE_TRADES = 30
MAX_BOOK_AGE_MS = 60_000


class MeasurementError(ValueError):
    """Stable reason code suitable for a later invalid persisted run."""


def _ms(value: Any) -> int:
    if type(value) is not int:
        raise MeasurementError("invalid_timestamp")
    return value


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise MeasurementError("invalid_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise MeasurementError("invalid_number") from error
    if not result.is_finite():
        raise MeasurementError("invalid_number")
    return result


def _numeric_context(values: Sequence[Decimal]) -> Context:
    """Exact sums/products at the input scale, independent of caller context."""
    nonzero = [value for value in values if value]
    if not nonzero:
        return Context(prec=28, rounding=ROUND_HALF_EVEN)
    highest = max(value.adjusted() for value in nonzero)
    lowest = min(value.as_tuple().exponent for value in nonzero)
    precision = max(28, highest - lowest + len(str(len(values))) + 4)
    return Context(prec=precision, rounding=ROUND_HALF_EVEN)


def _metric_float(value: Decimal) -> float:
    result = float(value)
    if not isfinite(result) or (result == 0 and value != 0):
        raise MeasurementError("unrepresentable_metrics")
    return result


def canonical_json(value: Any) -> str:
    """UTF-8 JSON: sorted string keys, no whitespace/exponents or nonfinite numbers.

    Numbers use their stored decimal value, without rounding: 1, 1.0 and 1e0
    encode as 1; signed zero encodes as 0. Array order is significant.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, Decimal)):
        number = _number(value)
        if number == 0:
            return "0"
        result = format(number, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise MeasurementError("non_string_json_key")
        return "{" + ",".join(canonical_json(key) + ":" + canonical_json(value[key])
                              for key in sorted(value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise MeasurementError("unsupported_json_value")


def hash_json(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def engine_sha256() -> str:
    """Hash the named simulation and cost source bytes, with an unambiguous manifest."""
    root = Path(__file__).parent
    return hash_json({name: sha256((root / name).read_bytes()).hexdigest()
                      for name in ("costs.py", "simulation.py")})


@dataclass(frozen=True)
class CandleWindow:
    boundary_ms: int
    first_open_ms: int
    last_open_ms: int
    expected_count: int


def candle_window(start_ms: int, as_of_ms: int, warmup_bars: int) -> CandleWindow:
    _ms(start_ms)
    _ms(as_of_ms)
    if type(warmup_bars) is not int or warmup_bars < 0 or as_of_ms < start_ms:
        raise MeasurementError("invalid_candle_window")
    boundary = as_of_ms // STEP_MS * STEP_MS
    first = start_ms // STEP_MS * STEP_MS - warmup_bars * STEP_MS
    last = boundary - STEP_MS
    return CandleWindow(boundary, first, last, max(0, (last - first) // STEP_MS + 1))


def closed_only(rows: Sequence[Mapping[str, Any]], *, start_ms: int,
                as_of_ms: int, warmup_bars: int) -> list[dict[str, Any]]:
    """Load normalized source/archive rows without I/O, preserving raw close time.

    Source rows use open_time/close_time; archived rows use open_time_ms/
    close_time_raw_ms. Both must supply confirm. Unconfirmed rows are excluded,
    never silently promoted. Confirmed gaps, duplicates and future rows fail.
    """
    window = candle_window(start_ms, as_of_ms, warmup_bars)
    selected: dict[int, dict[str, Any]] = {}
    for source in rows:
        if not isinstance(source, Mapping):
            raise MeasurementError("incomplete_candles")
        confirmation = source.get("confirm")
        if isinstance(confirmation, bool):
            raise MeasurementError("missing_candle_confirmation")
        if confirmation in (0, "0"):
            continue
        if confirmation not in (1, "1"):
            raise MeasurementError("missing_candle_confirmation")
        opened = _ms(source.get("open_time_ms", source.get("open_time")))
        if opened < window.first_open_ms:
            continue
        if opened % STEP_MS or opened > window.last_open_ms or opened in selected:
            raise MeasurementError("incomplete_candles")
        raw_close = _ms(source.get("close_time_raw_ms", source.get("close_time")))
        if not opened <= raw_close <= opened + STEP_MS or raw_close > as_of_ms:
            raise MeasurementError("invalid_candle_close_time")
        values = {key: source.get(key) for key in
                  ("open", "high", "low", "close", "volume", "quote_volume")}
        numbers = {key: _number(value) for key, value in values.items()}
        if (any(numbers[key] <= 0 for key in ("open", "high", "low", "close"))
                or numbers["volume"] < 0 or numbers["quote_volume"] < 0
                or numbers["low"] > min(numbers["open"], numbers["close"])
                or numbers["high"] < max(numbers["open"], numbers["close"])):
            raise MeasurementError("invalid_candle_values")
        selected[opened] = {"open_time_ms": opened, "close_time_raw_ms": raw_close,
                            **values, "confirm": 1}
    expected = range(window.first_open_ms, window.last_open_ms + 1, STEP_MS)
    if len(selected) != window.expected_count or any(ts not in selected for ts in expected):
        raise MeasurementError("incomplete_candles")
    return [selected[ts] for ts in sorted(selected)]


def cost_book_reason(book_ts_ms: Any, cost_fetched_at_ms: Any) -> str | None:
    if type(book_ts_ms) is not int or type(cost_fetched_at_ms) is not int:
        return "cost_profile_unavailable"
    age = cost_fetched_at_ms - book_ts_ms
    if age < 0:
        return "book_from_future"
    if age > MAX_BOOK_AGE_MS:
        return "book_too_old"
    return None


def _iso_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise MeasurementError("missing_trade_timezone")
    delta = parsed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def simulation_tape(run_id: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Map all engine closed trades to run-owned rows, without filtering the tape."""
    return [{"run_id": run_id, "ordinal": ordinal, "pair": trade["pair"],
             "opened_at_ms": _iso_ms(trade["opened_at"]),
             "closed_at_ms": _iso_ms(trade["closed_at"]) if trade["closed_at"] else None,
             "entry_rate": trade["entry_rate"], "exit_rate": trade["exit_rate"],
             "stake": trade["stake_amount"], "profit_usdt": trade["profit_usdt"],
             "exit_reason": trade["exit_reason"], "raw": deepcopy(trade["raw"])}
            for ordinal, trade in enumerate(result["trades"])]


@dataclass(frozen=True)
class RunEvidence:
    """Snapshot from a later read adapter; None is missing, [] is proven empty.

    origin is resolved by the trusted P2 assess layer, including its grace clock.
    No boolean about origin is inferred from a worker's self-reported source.
    """
    run: Mapping[str, Any]
    candles: Sequence[Mapping[str, Any]] | None
    trades: Sequence[Mapping[str, Any]] | None
    open_positions: Sequence[Mapping[str, Any]] | None
    origin: Literal["verified", "pending", "unverifiable"] = "unverifiable"


def _test_contract(test: Mapping[str, Any]) -> None:
    if not isinstance(test, Mapping) or not isinstance(test.get("settings"), Mapping):
        raise MeasurementError("unsupported_test_protocol")
    if (test.get("label") not in ("official", "scale") or test.get("timeframe") != "5m"
            or test.get("gate_ms") != GATE_MS or test.get("gate_trades") != GATE_TRADES
            or test.get("max_book_age_ms") != MAX_BOOK_AGE_MS):
        raise MeasurementError("unsupported_test_protocol")
    _ms(test["start_ms"])
    if hash_json(test["settings"]) != test["settings_sha256"]:
        raise MeasurementError("settings_mismatch")


def _owned_rows(rows: Sequence[Mapping[str, Any]] | None, run: Mapping[str, Any],
                count_key: str, reason: str) -> list[Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise MeasurementError(reason)
    count = run.get(count_key)
    if type(count) is not int or count < 0 or len(rows) != count:
        raise MeasurementError(reason)
    if any(not isinstance(row, Mapping) or row.get("run_id") != run.get("id")
           or type(row.get("ordinal")) is not int
           for row in rows):
        raise MeasurementError(reason)
    ordered = sorted(rows, key=lambda row: row["ordinal"])
    if [row["ordinal"] for row in ordered] != list(range(count)):
        raise MeasurementError(reason)
    return ordered


def _validated_tape(test: Mapping[str, Any], evidence: RunEvidence) -> list[Mapping[str, Any]]:
    run = evidence.run
    if not isinstance(run, Mapping):
        raise MeasurementError("missing_run_evidence")
    if not run.get("id") or run.get("test_id") != test["id"]:
        raise MeasurementError("run_identity_mismatch")
    if run.get("as_of_ms") != run.get("test_end_ms"):
        raise MeasurementError("horizon_mismatch")
    as_of = _ms(run["test_end_ms"])
    for field, length in (("code_commit", 40), ("engine_sha256", 64)):
        value = run.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{%d}" % length, value) is None:
            raise MeasurementError("unknown_code_version")
    if run.get("settings_sha256") != test["settings_sha256"]:
        raise MeasurementError("settings_mismatch")
    tape = _owned_rows(evidence.trades, run, "trade_count", "missing_run_specific_tape")
    if hash_json(tape) != run.get("tape_sha256"):
        raise MeasurementError("tape_hash_mismatch")
    positions = _owned_rows(evidence.open_positions, run, "open_count", "missing_open_positions")
    required = {"opened_at_ms", "closed_at_ms", "entry_rate", "exit_rate", "stake",
                "profit_usdt", "exit_reason", "raw"}
    for row in tape:
        if (not required.issubset(row) or not isinstance(row["raw"], Mapping)
                or not isinstance(row["exit_reason"], str) or not row["exit_reason"]):
            raise MeasurementError("incomplete_trade_evidence")
        if _number(row["entry_rate"]) <= 0 or _number(row["stake"]) <= 0:
            raise MeasurementError("incomplete_trade_evidence")
        if row["closed_at_ms"] is not None and _number(row["exit_rate"]) <= 0:
            raise MeasurementError("incomplete_trade_evidence")
    for row in positions:
        if (not {"opened_at_ms", "entry_rate", "stake", "raw"}.issubset(row)
                or not isinstance(row["raw"], Mapping) or row.get("closed_at_ms") is not None):
            raise MeasurementError("incomplete_open_position_evidence")
        if _number(row["entry_rate"]) <= 0 or _number(row["stake"]) <= 0:
            raise MeasurementError("incomplete_open_position_evidence")
    for row in [*tape, *positions]:
        if row.get("pair") != test["pair"]:
            raise MeasurementError("trade_pair_mismatch")
        opened = _ms(row.get("opened_at_ms"))
        if opened > as_of:
            raise MeasurementError("trade_after_as_of")
        if row.get("closed_at_ms") is not None:
            closed = _ms(row["closed_at_ms"])
            if closed > as_of or closed < opened:
                raise MeasurementError("trade_after_as_of")
        if row.get("profit_usdt") is not None:
            _number(row["profit_usdt"])
    if evidence.candles is None:
        raise MeasurementError("missing_candle_archive")
    candles = closed_only(evidence.candles, start_ms=test["start_ms"], as_of_ms=as_of,
                          warmup_bars=test["settings"]["warmup_bars"])
    if hash_json(candles) != run.get("candles_sha256"):
        raise MeasurementError("candles_hash_mismatch")
    window = candle_window(test["start_ms"], as_of, test["settings"]["warmup_bars"])
    expected = {"boundary_ms": window.boundary_ms, "last_candle_open_ms": window.last_open_ms,
                "candle_first_open_ms": window.first_open_ms, "candle_last_open_ms": window.last_open_ms,
                "candle_count": window.expected_count, "expected_count": window.expected_count}
    if any(run.get(key) != value for key, value in expected.items()):
        raise MeasurementError("incomplete_candles")
    if candles and run.get("last_candle_close_raw_ms") != candles[-1]["close_time_raw_ms"]:
        raise MeasurementError("incomplete_candles")
    profile = run.get("cost_profile")
    if not isinstance(profile, Mapping) or not profile or hash_json(profile) != run.get("cost_profile_sha256"):
        raise MeasurementError("cost_profile_unavailable")
    reason = cost_book_reason(run.get("book_ts_ms"), run.get("cost_fetched_at_ms"))
    if reason:
        raise MeasurementError(reason)
    if str(profile.get("book_ts")) != str(run["book_ts_ms"]):
        raise MeasurementError("cost_book_timestamp_mismatch")
    for key in ("entry_cost_rate", "exit_cost_rate", "fee_rate", "half_spread", "buy_impact", "sell_impact"):
        if _number(profile.get(key)) < 0:
            raise MeasurementError("cost_profile_unavailable")
    if run.get("status") != "valid":
        raise MeasurementError(run.get("invalid_reason") or "invalid_run")
    dd = _number(run["summary"]["max_drawdown_percent"])
    if dd < 0:
        raise MeasurementError("invalid_drawdown")
    return tape


def _valid_trades(tape: Sequence[Mapping[str, Any]], start_ms: int,
                  as_of_ms: int) -> list[Mapping[str, Any]]:
    return sorted((row for row in tape if row.get("closed_at_ms") is not None
                   and row["opened_at_ms"] >= start_ms
                   and start_ms <= row["closed_at_ms"] <= as_of_ms
                   and row.get("exit_reason") != "end_of_test"
                   and row.get("profit_usdt") is not None),
                  key=lambda row: (row["closed_at_ms"], row["ordinal"]))


def tape_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    profits = [_number(row["profit_usdt"]) for row in trades]
    with localcontext(_numeric_context(profits)):
        gain = sum((value for value in profits if value > 0), Decimal(0))
        loss = -sum((value for value in profits if value < 0), Decimal(0))
        return {"trades_count": len(profits), "net_pnl": _metric_float(gain - loss),
                "gross_profit": _metric_float(gain), "gross_loss": _metric_float(loss),
                "expectancy": _metric_float((gain - loss) / len(profits)) if profits else None,
                "profit_factor": _metric_float(gain / loss) if loss else None,
                "pf_infinite": loss == 0 and gain > 0}


def evaluate_test(test: Mapping[str, Any], runs: Sequence[RunEvidence], *,
                  chronology_verified: bool = False) -> dict[str, Any]:
    """Variant B over persisted snapshots only; never writes a verdict.

    chronology_verified must come from the trusted adapter's full sequence
    certificate, not from the presence of a seq field alone. Pre-gate runs and
    unpersisted claims do not enter the eligibility trace. A pending origin
    stops traversal before any later candidate is considered.
    """
    trace: list[dict[str, Any]] = []
    result: dict[str, Any] = {"verdict": "NOT_EVALUATED", "reason": "gates_not_met",
                              "candidate_run_id": None, "eligibility_trace": trace}
    try:
        _test_contract(test)
        # Pre-gate evidence is not assessed and cannot create a test verdict.
        after_gate = []
        for item in runs:
            if not isinstance(item, RunEvidence) or not isinstance(item.run, Mapping):
                raise MeasurementError("missing_run_evidence")
            if _ms(item.run.get("test_end_ms")) >= test["start_ms"] + GATE_MS:
                after_gate.append(item)
        if after_gate and chronology_verified is not True:
            raise MeasurementError("first_run_unverifiable")
        if any(type(item.run.get("seq")) is not int or item.run["seq"] < 1 for item in after_gate):
            raise MeasurementError("first_run_unverifiable")
        ordered = sorted(after_gate, key=lambda item: item.run["seq"])
        if len({item.run["seq"] for item in ordered}) != len(ordered):
            raise MeasurementError("first_run_unverifiable")
        for evidence in ordered:
            run = evidence.run
            as_of = _ms(run.get("test_end_ms"))
            if evidence.origin == "pending":
                return {**result, "reason": "origin_pending", "pending_run_id": run.get("id")}
            entry = {"run_id": run.get("id"), "seq": run["seq"], "eligibility": "unverifiable"}
            trace.append(entry)
            result["candidate_run_id"] = run.get("id")
            if evidence.origin != "verified":
                raise MeasurementError("tick_origin_unverified")
            tape = _validated_tape(test, evidence)
            valid = _valid_trades(tape, test["start_ms"], as_of)
            entry["valid_trade_count"] = len(valid)
            if len(valid) < GATE_TRADES:
                entry["eligibility"] = "proven_ineligible"
                result["candidate_run_id"] = None
                continue
            c30 = valid[GATE_TRADES - 1]["closed_at_ms"]
            t_star = max(test["start_ms"] + GATE_MS, c30)
            clipped = [row for row in valid if row["closed_at_ms"] <= t_star]
            metrics = tape_metrics(clipped)
            full_dd = _number(run["summary"]["max_drawdown_percent"])
            with localcontext(_numeric_context([full_dd, Decimal("0.01")])):
                dd = full_dd.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            # Compare exact net decimal sums, not the rounded output PF/expectancy.
            values = [_number(row["profit_usdt"]) for row in clipped]
            with localcontext(_numeric_context(values)):
                gain = sum((v for v in values if v > 0), Decimal(0))
                loss = -sum((v for v in values if v < 0), Decimal(0))
                passes = (gain > loss and dd <= 15
                          and ((loss > 0 and gain >= Decimal("1.10") * loss)
                               or (loss == 0 and gain > 0)))
            dd_value = _metric_float(dd)
            entry["eligibility"] = "eligible"
            verdict = "DIAGNOSTIC" if test["label"] == "scale" else ("PASS" if passes else "FAIL")
            return {**result, **metrics, "verdict": verdict, "reason": "evaluated",
                    "c30_ms": c30, "t_star_ms": t_star,
                    "dd_field": "summary.max_drawdown_percent", "dd_value": dd_value}
        return result
    except (MeasurementError, KeyError, TypeError, DecimalException) as error:
        reason = (str(error) if isinstance(error, MeasurementError) else
                  "unrepresentable_metrics" if isinstance(error, DecimalException) else "missing_run_evidence")
        unverifiable_candidate = bool(trace and trace[-1]["eligibility"] == "unverifiable")
        if unverifiable_candidate:
            trace[-1]["reason"] = reason
        return {**result, "verdict": "INVALID", "invalid_reason": reason,
                "reason": "unverifiable_candidate" if unverifiable_candidate else reason}


def replay_run(run_id: str, *, read_snapshot: Callable[[str], tuple[Mapping[str, Any], RunEvidence]]) -> dict[str, Any]:
    """Read one immutable snapshot, replay locally, compare tape and full summary.

    The P1 adapter is a local fixture/JSON reader. A DB reader is a later phase;
    this function has no default adapter and cannot fetch a current order book.
    """
    try:
        test, evidence = read_snapshot(run_id)
        _test_contract(test)
        if not isinstance(evidence, RunEvidence) or not isinstance(evidence.run, Mapping):
            raise MeasurementError("missing_run_evidence")
        if evidence.run.get("id") != run_id:
            raise MeasurementError("run_identity_mismatch")
        if evidence.run.get("engine_sha256") != engine_sha256():
            raise MeasurementError("engine_mismatch")
        _validated_tape(test, evidence)
        assert evidence.candles is not None
        archived = closed_only(evidence.candles, start_ms=test["start_ms"],
                               as_of_ms=evidence.run["test_end_ms"],
                               warmup_bars=test["settings"]["warmup_bars"])
        engine_rows = [{"open_time": row["open_time_ms"], "close_time": row["close_time_raw_ms"],
                        **{key: row[key] for key in ("open", "high", "low", "close", "volume", "quote_volume")}}
                       for row in archived]
        result = simulate({test["pair"]: engine_rows}, deepcopy(test["settings"]),
                          trading_start_time=test["start_ms"], force_close_at_end=False,
                          cost_models={test["pair"]: deepcopy(evidence.run["cost_profile"])})
        tape_match = hash_json(simulation_tape(run_id, result)) == evidence.run["tape_sha256"]
        summary_match = canonical_json(result["metrics"]) == canonical_json(evidence.run["summary"])
        open_count_match = result["open_positions"] == evidence.run["open_count"]
        matches = tape_match and summary_match and open_count_match
        return {"run_id": run_id, "status": "valid" if matches else "invalid",
                "reason": "replay_match" if matches else "replay_mismatch",
                "tape_match": tape_match, "summary_match": summary_match,
                "open_count_match": open_count_match}
    except (MeasurementError, KeyError, TypeError) as error:
        return {"run_id": run_id, "status": "invalid",
                "reason": str(error) if isinstance(error, MeasurementError) else "missing_run_evidence"}
