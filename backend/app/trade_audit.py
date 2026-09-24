"""Trade evidence and a bounded, offline replay. No market fetch or grid search."""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import zlib
from collections import Counter
from datetime import datetime
from math import isfinite, log
from statistics import median, pstdev
from typing import Any

from .simulation import simulate


ENGINE_VERSION = "close_execution_v1"
AUDIT_TARGETS = {
    "UNI/USDT": {"closed_trades": 21, "realized_profit": -0.6733,
                 "windows": [(5, 1.3003), (7, -3.2424), (9, 1.2688)]},
    "ZEC/USDT": {"closed_trades": 23, "realized_profit": -0.6865,
                 "windows": [(9, 1.2986), (7, -1.7375), (7, -0.2476)]},
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pack_snapshot(candles: list[dict], holdout_start: int) -> dict:
    # Holdout candles are deliberately absent from the replay artifact.
    data = json.dumps(candles, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"encoding": "gzip+base64", "sha256": digest(candles), "count": len(candles),
            "holdout_start_ms": holdout_start, "engine_version": ENGINE_VERSION,
            "data": base64.b64encode(gzip.compress(data, mtime=0)).decode()}


def unpack_snapshot(snapshot: dict) -> list[dict]:
    if snapshot.get("encoding") != "gzip+base64" or snapshot.get("engine_version") != ENGINE_VERSION:
        raise ValueError("snapshot_format_mismatch")
    try:
        data = gzip.decompress(base64.b64decode(snapshot["data"], validate=True))
        candles = json.loads(data)
    except (OSError, EOFError, binascii.Error, UnicodeError, zlib.error, json.JSONDecodeError) as error:
        raise ValueError("invalid_snapshot_encoding") from error
    if len(candles) != snapshot["count"] or digest(candles) != snapshot["sha256"]:
        raise ValueError("snapshot_hash_mismatch")
    times = [int(c["open_time"]) for c in candles]
    if not times or times != sorted(set(times)) or any(int(c["close_time"]) >= snapshot["holdout_start_ms"] for c in candles):
        raise ValueError("invalid_validation_snapshot")
    return candles


def trade_tape(result: dict, pair: str, bar: str, variant_id: str, window: str, fee: float) -> list[dict]:
    # The engine owns exit priority and the canonical reason. Audit only maps
    # the engine's reason into the reporting taxonomy; it never infers a reason
    # from prices, PnL, duration or excursion data.
    reasons = {
        "stop_loss": "stop_loss",
        "trailing_profit": "trailing_profit",
        "max_no_trail_hours": "max_no_trail",
        "end_of_test": "end_of_test",
    }
    bar_minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(bar)
    tape = []
    for trade in result.get("trades", []):
        raw = trade.get("raw", {})
        hold_minutes = raw.get("duration_min")
        hold_bars = raw.get("holding_candles")
        if hold_bars is None and hold_minutes is not None and bar_minutes:
            hold_bars = int(round(float(hold_minutes) / bar_minutes))
        trailing_activated = raw.get("trailing_start_reached")
        tape.append({"pair": pair, "bar": bar, "variant_id": variant_id, "window": window,
                     "entry_ts": trade["opened_at"], "exit_ts": trade["closed_at"],
                     "duration_min": hold_minutes, "hold_minutes": hold_minutes, "hold_bars": hold_bars,
                     "entry_px": trade["entry_rate"], "exit_px": trade["exit_rate"],
                     "pnl_net": trade["profit_usdt"], "stake_amount": trade["stake_amount"],
                     "cost_per_side": fee, "mae": raw.get("mae"), "mfe": raw.get("mfe"),
                     "cost_components": raw.get("cost_components"),
                     "excursion_unit": "gross_price_percent",
                     "exit_reason": reasons.get(trade.get("exit_reason"), "other"),
                     "engine_exit_reason": trade.get("exit_reason"),
                     "sl_before_trail": raw.get("sl_before_trail", False),
                     "trailing_start_percent": raw.get("trailing_start_percent"),
                     "trailing_activated": trailing_activated,
                     "mfe_reached_trailing_start": trailing_activated,
                     "trail_active_before_exit_candle": raw.get("trail_active_before_exit_candle")})
    return tape


def tape_summary(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["pnl_net"] > 0]
    losses = [t for t in trades if t["pnl_net"] < 0]
    counts = Counter(t["exit_reason"] for t in trades)
    mfe_known = all(t.get("mfe") is not None for t in losses)
    hold_minutes = [float(t["hold_minutes"]) for t in trades if t.get("hold_minutes") is not None]
    timeout = [t for t in trades if t.get("exit_reason") == "max_no_trail"]
    timeout_wins = [t for t in timeout if t["pnl_net"] > 0]
    timeout_losses = [t for t in timeout if t["pnl_net"] < 0]
    trail_known = [t for t in trades if t.get("trailing_activated") is not None]
    trail_activated = [t for t in trail_known if t.get("trailing_activated") is True]
    return {"n": len(trades), "win": len(wins), "loss": len(losses),
            "breakeven": len(trades) - len(wins) - len(losses),
            "pnl_net": round(sum(t["pnl_net"] for t in trades), 6),
            "avg_win": sum(t["pnl_net"] for t in wins) / len(wins) if wins else None,
            "avg_loss": -sum(t["pnl_net"] for t in losses) / len(losses) if losses else None,
            "exit_reasons": {reason: {"n": counts[reason], "share_percent": 100 * counts[reason] / len(trades) if trades else 0}
                             for reason in ("stop_loss", "trailing_profit", "max_no_trail", "end_of_test", "other")},
            "avg_mfe_losses": sum(t["mfe"] for t in losses) / len(losses) if losses and mfe_known else None,
            "losses_mfe_at_least_trailing_start": sum(t.get("mfe_reached_trailing_start") is True for t in losses)
                if losses and mfe_known else None,
            "sl_before_trail": sum(t.get("sl_before_trail") is True for t in trades),
            "avg_hold_minutes": sum(hold_minutes) / len(hold_minutes) if hold_minutes else None,
            "median_hold_minutes": median(hold_minutes) if hold_minutes else None,
            "timeout_positive": len(timeout_wins),
            "timeout_negative": len(timeout_losses),
            "timeout_breakeven": len(timeout) - len(timeout_wins) - len(timeout_losses),
            "timeout_profitable_share_percent": 100 * len(timeout_wins) / len(timeout) if timeout else None,
            "trailing_activated": len(trail_activated),
            "trailing_activated_share_percent": 100 * len(trail_activated) / len(trail_known) if trail_known else None,
            "trail_never_activated": len(trail_known) - len(trail_activated),
            "trail_never_activated_share_percent": 100 * (len(trail_known) - len(trail_activated)) / len(trail_known) if trail_known else None}


def _iso_ms(value: str) -> int:
    return int(round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def entry_path_features(candles: list[dict], trade: dict, trail_start_percent: float = 1.6) -> dict:
    """Attach pre-entry regime and post-entry path diagnostics without changing the trade."""
    by_close = {int(c["close_time"]): i for i, c in enumerate(candles)}
    entry_ms, exit_ms = _iso_ms(trade["entry_ts"]), _iso_ms(trade["exit_ts"])
    index = by_close.get(entry_ms)
    exit_index = by_close.get(exit_ms)
    if index is None or exit_index is None or exit_index < index:
        raise ValueError("trade_timestamp_not_in_snapshot")

    entry = float(trade["entry_px"])

    def prior_return(bars: int) -> float | None:
        if index < bars:
            return None
        prior = float(candles[index - bars]["close"])
        return (entry / prior - 1) * 100 if prior else None

    def realized_vol(bars: int) -> float | None:
        if index < bars:
            return None
        values = []
        for j in range(index - bars + 1, index + 1):
            before, after = float(candles[j - 1]["close"]), float(candles[j]["close"])
            if before <= 0 or after <= 0:
                return None
            values.append(log(after / before) * 100)
        return pstdev(values) if values else None

    swing_start = index - 287
    if swing_start >= 0:
        swing = candles[swing_start:index + 1]
        high_24h = max(float(c["high"]) for c in swing)
        low_24h = min(float(c["low"]) for c in swing)
        below_high = (entry / high_24h - 1) * 100 if high_24h else None
        above_low = (entry / low_24h - 1) * 100 if low_24h else None
        range_position = (entry - low_24h) / (high_24h - low_24h) * 100 if high_24h > low_24h else None
    else:
        high_24h = low_24h = below_high = above_low = range_position = None

    thresholds = {
        "time_to_trail_start_min": entry * (1 + trail_start_percent / 100),
        "time_to_mae_1pct_min": entry * .99,
        "time_to_mae_6pct_min": entry * .94,
    }
    hit: dict[str, float | None] = {key: None for key in thresholds}
    for candle in candles[index + 1:exit_index + 1]:
        minutes = (int(candle["close_time"]) - entry_ms) / 60_000
        if hit["time_to_trail_start_min"] is None and float(candle["high"]) >= thresholds["time_to_trail_start_min"]:
            hit["time_to_trail_start_min"] = minutes
        if hit["time_to_mae_1pct_min"] is None and float(candle["low"]) <= thresholds["time_to_mae_1pct_min"]:
            hit["time_to_mae_1pct_min"] = minutes
        if hit["time_to_mae_6pct_min"] is None and float(candle["low"]) <= thresholds["time_to_mae_6pct_min"]:
            hit["time_to_mae_6pct_min"] = minutes

    trail_time = hit["time_to_trail_start_min"]
    mae1_time = hit["time_to_mae_1pct_min"]
    if trail_time is None and mae1_time is None:
        first_path_event = "neither"
    elif trail_time is None:
        first_path_event = "mae_1pct"
    elif mae1_time is None:
        first_path_event = "trail_start"
    elif trail_time < mae1_time:
        first_path_event = "trail_start"
    elif mae1_time < trail_time:
        first_path_event = "mae_1pct"
    else:
        first_path_event = "same_bar"

    return {
        **trade,
        "pre_return_1h_pct": prior_return(12),
        "pre_return_4h_pct": prior_return(48),
        "rv_12_bars_pct": realized_vol(12),
        "rv_24_bars_pct": realized_vol(24),
        "rv_72_bars_pct": realized_vol(72),
        "high_24h": high_24h,
        "low_24h": low_24h,
        "distance_from_24h_high_pct": below_high,
        "distance_above_24h_low_pct": above_low,
        "range_position_24h_pct": range_position,
        "entry_hour_utc": datetime.fromisoformat(trade["entry_ts"].replace("Z", "+00:00")).hour,
        **hit,
        "first_path_event": first_path_event,
    }


def entry_path_summary(trades: list[dict]) -> dict:
    def numeric(key: str) -> list[float]:
        return [float(t[key]) for t in trades if t.get(key) is not None]

    wins = [t for t in trades if float(t["pnl_net"]) > 0]
    losses = [t for t in trades if float(t["pnl_net"]) < 0]
    trail = [t for t in trades if t.get("time_to_trail_start_min") is not None]
    no_trail = [t for t in trades if t.get("time_to_trail_start_min") is None]
    mae1_first = [t for t in trades if t.get("first_path_event") == "mae_1pct"]
    trail_first = [t for t in trades if t.get("first_path_event") == "trail_start"]

    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": 100 * len(wins) / len(trades) if trades else None,
        "trail_reached": len(trail),
        "trail_reached_share_percent": 100 * len(trail) / len(trades) if trades else None,
        "trail_never_reached": len(no_trail),
        "trail_never_reached_share_percent": 100 * len(no_trail) / len(trades) if trades else None,
        "mae_1pct_before_trail": len(mae1_first),
        "mae_1pct_before_trail_share_percent": 100 * len(mae1_first) / len(trades) if trades else None,
        "trail_before_mae_1pct": len(trail_first),
        "trail_before_mae_1pct_share_percent": 100 * len(trail_first) / len(trades) if trades else None,
        "avg_pre_return_1h_pct": _mean(numeric("pre_return_1h_pct")),
        "avg_pre_return_4h_pct": _mean(numeric("pre_return_4h_pct")),
        "avg_rv_12_bars_pct": _mean(numeric("rv_12_bars_pct")),
        "avg_rv_24_bars_pct": _mean(numeric("rv_24_bars_pct")),
        "avg_rv_72_bars_pct": _mean(numeric("rv_72_bars_pct")),
        "avg_distance_from_24h_high_pct": _mean(numeric("distance_from_24h_high_pct")),
        "avg_distance_above_24h_low_pct": _mean(numeric("distance_above_24h_low_pct")),
        "avg_range_position_24h_pct": _mean(numeric("range_position_24h_pct")),
        "median_time_to_trail_start_min": median(numeric("time_to_trail_start_min")) if numeric("time_to_trail_start_min") else None,
        "median_time_to_mae_1pct_min": median(numeric("time_to_mae_1pct_min")) if numeric("time_to_mae_1pct_min") else None,
        "median_time_to_mae_6pct_min": median(numeric("time_to_mae_6pct_min")) if numeric("time_to_mae_6pct_min") else None,
        "avg_mfe": _mean(numeric("mfe")),
        "avg_mae": _mean(numeric("mae")),
        "win_avg_mfe": _mean([float(t["mfe"]) for t in wins if t.get("mfe") is not None]),
        "loss_avg_mfe": _mean([float(t["mfe"]) for t in losses if t.get("mfe") is not None]),
        "win_avg_mae": _mean([float(t["mae"]) for t in wins if t.get("mae") is not None]),
        "loss_avg_mae": _mean([float(t["mae"]) for t in losses if t.get("mae") is not None]),
    }


def entry_path_audit(candles: list[dict], trades: list[dict], trail_start_percent: float = 1.6) -> dict:
    enriched = [entry_path_features(candles, trade, trail_start_percent) for trade in trades]
    windows = {}
    for name in ("wf1", "wf2", "wf3"):
        group = [t for t in enriched if t.get("window") == name]
        windows[name] = entry_path_summary(group)
    trail_groups = {
        "trail_reached": entry_path_summary([t for t in enriched if t.get("time_to_trail_start_min") is not None]),
        "trail_never_reached": entry_path_summary([t for t in enriched if t.get("time_to_trail_start_min") is None]),
    }
    outcome_groups = {
        "wins": entry_path_summary([t for t in enriched if float(t["pnl_net"]) > 0]),
        "losses": entry_path_summary([t for t in enriched if float(t["pnl_net"]) < 0]),
    }
    return {
        "summary": entry_path_summary(enriched),
        "windows": windows,
        "trail_groups": trail_groups,
        "outcome_groups": outcome_groups,
        "trades": enriched,
    }


def matches_target(row: dict) -> bool:
    expected = AUDIT_TARGETS.get(row.get("pair"))
    metrics = row.get("walk_forward_metrics") or {}
    if not expected or row.get("timeframe") != "5m" or row.get("variant") != 2:
        return False
    windows = row.get("validation_windows") or []
    return (metrics.get("closed_trades") == expected["closed_trades"]
            and metrics.get("realized_profit") == expected["realized_profit"]
            and [(w.get("closed_trades"), w.get("realized_profit")) for w in windows] == expected["windows"]
            and row.get("validation_passed") is False and not row.get("holdout_evaluated"))


def prepare_replay(records: list[dict]) -> list[tuple[dict, dict, list[dict]]]:
    prepared = []
    for pair in AUDIT_TARGETS:
        matches = [(record, row) for record in records for row in record.get("result", {}).get("variant_results", [])
                   if row.get("pair") == pair and matches_target(row)]
        if len(matches) != 1:
            raise ValueError(f"{pair}: original_report_missing_or_ambiguous")
        record, row = matches[0]
        result = record["result"]
        if pair not in result.get("locked_pairs", []) or result.get("source") != "okx":
            raise ValueError(f"{pair}: original_lock_or_source_missing")
        snapshot = result.get("replay_snapshots", {}).get(row.get("snapshot_id"))
        if not snapshot:
            raise ValueError(f"{pair}: original_candle_snapshot_missing")
        if row.get("settings_sha256") != digest(row["settings"]) or row.get("cost_per_side") != .0015 or row.get("cost_components"):
            raise ValueError(f"{pair}: settings_or_cost_mismatch")
        if row["settings"].get("selected_pairs") != [pair] or row["settings"].get("timeframe") != "5m":
            raise ValueError(f"{pair}: settings_pair_or_bar_mismatch")
        data = unpack_snapshot(snapshot)
        boundaries = row.get("validation_boundaries", [])
        if len(boundaries) != 3 or [b.get("window") for b in boundaries] != ["wf1", "wf2", "wf3"]:
            raise ValueError(f"{pair}: original_windows_missing")
        prior_end = None
        for b in boundaries:
            start, stop, trading = b["warmup_start_index"], b["stop_index"], b["trading_start_index"]
            if not (0 <= start <= trading < stop <= len(data)) or (prior_end is not None and trading != prior_end):
                raise ValueError(f"{pair}: invalid_window_boundaries")
            if int(data[trading]["open_time"]) != b["trading_start_ms"]:
                raise ValueError(f"{pair}: window_timestamp_mismatch")
            prior_end = stop
        if prior_end != len(data):
            raise ValueError(f"{pair}: validation_end_mismatch")
        prepared.append((record, row, data))
    if not all(r["result"].get("version_id") for r, _, _ in prepared) or len({r["result"].get("version_id") for r, _, _ in prepared}) != 1:
        raise ValueError("original_versions_differ")
    if len({tuple(sorted(r["result"]["locked_pairs"])) for r, _, _ in prepared}) != 1:
        raise ValueError("original_locks_differ")
    return prepared


def replay_validation(records: list[dict]) -> dict:
    """Only the two explicit rejected variants. Preflight both before any simulation."""
    from .optimizer import aggregate_metrics
    try:
        prepared = prepare_replay(records)
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "blocked", "reason": str(error), "trades": [], "qualified": False,
                "strategy_code": None, "verdict": "NEPREŠIEL"}
    outputs = []
    for record, row, data in prepared:
        windows, tape = [], []
        for b in row["validation_boundaries"]:
            run = simulate({row["pair"]: data[b["warmup_start_index"]:b["stop_index"]]},
                           row["settings"], fee=row["cost_per_side"], force_close_at_end=True,
                           trading_start_time=b["trading_start_ms"])
            windows.append(run["metrics"])
            tape.extend(trade_tape(run, row["pair"], "5m", row["variant_id"], b["window"], row["cost_per_side"]))
        combined = aggregate_metrics(windows, float(row["settings"]["initial_capital"]))
        # Compare every recorded metric, each WF separately as well as aggregate.
        def same(expected: dict, actual: dict) -> bool:
            for key, value in expected.items():
                if key not in actual:
                    return False
                if value is None:
                    if actual[key] is not None:
                        return False
                elif not isinstance(value, (int, float)) or not isinstance(actual[key], (int, float)):
                    return False
                elif not isfinite(float(value)) or not isfinite(float(actual[key])) or abs(float(actual[key]) - float(value)) >= 1e-7:
                    return False
            return True
        matched = same(row["walk_forward_metrics"], combined) and all(same(old, new) for old, new in zip(row["validation_windows"], windows))
        matched = matched and len(tape) == combined["closed_trades"]
        outputs.append({"source_job_id": record["id"], "pair": row["pair"], "bar": "5m", "variant_id": row["variant_id"],
                        "matched": matched, "validation_windows": windows, "metrics": combined,
                        "trades": tape, "summary": tape_summary(tape)})
    matched = all(row["matched"] for row in outputs)
    if not matched:
        # Never present a different run's tape as the historical evidence.
        for row in outputs:
            row["trades"] = []
            row["summary"] = None
    return {"status": "matched" if matched else "mismatch", "pairs": outputs,
            "trades": [t for row in outputs for t in row["trades"]], "qualified": False,
            "strategy_code": None, "verdict": "NEPREŠIEL"}
