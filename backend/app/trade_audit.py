"""Trade evidence and a bounded, offline replay. No market fetch or grid search."""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import zlib
from collections import Counter
from math import isfinite
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
    reasons = {"stop_loss": "stop_loss", "trailing_profit": "trailing", "end_of_test": "window_end"}
    tape = []
    for trade in result.get("trades", []):
        raw = trade.get("raw", {})
        tape.append({"pair": pair, "bar": bar, "variant_id": variant_id, "window": window,
                     "entry_ts": trade["opened_at"], "exit_ts": trade["closed_at"],
                     "duration_min": raw.get("duration_min"),
                     "entry_px": trade["entry_rate"], "exit_px": trade["exit_rate"],
                     "pnl_net": trade["profit_usdt"], "stake_amount": trade["stake_amount"],
                     "cost_per_side": fee, "mae": raw.get("mae"), "mfe": raw.get("mfe"),
                     "cost_components": raw.get("cost_components"),
                     "excursion_unit": "gross_price_percent",
                     "exit_reason": reasons.get(trade.get("exit_reason"), "other"),
                     "sl_before_trail": raw.get("sl_before_trail", False),
                     "trailing_start_percent": raw.get("trailing_start_percent"),
                     "mfe_reached_trailing_start": raw.get("trailing_start_reached"),
                     "trail_active_before_exit_candle": raw.get("trail_active_before_exit_candle")})
    return tape


def tape_summary(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["pnl_net"] > 0]
    losses = [t for t in trades if t["pnl_net"] < 0]
    counts = Counter(t["exit_reason"] for t in trades)
    mfe_known = all(t.get("mfe") is not None for t in losses)
    return {"n": len(trades), "win": len(wins), "loss": len(losses),
            "breakeven": len(trades) - len(wins) - len(losses),
            "pnl_net": round(sum(t["pnl_net"] for t in trades), 6),
            "avg_win": sum(t["pnl_net"] for t in wins) / len(wins) if wins else None,
            "avg_loss": -sum(t["pnl_net"] for t in losses) / len(losses) if losses else None,
            "exit_reasons": {reason: {"n": counts[reason], "share_percent": 100 * counts[reason] / len(trades) if trades else 0}
                             for reason in ("stop_loss", "trailing", "window_end", "other")},
            "avg_mfe_losses": sum(t["mfe"] for t in losses) / len(losses) if losses and mfe_known else None,
            "losses_mfe_at_least_trailing_start": sum(t.get("mfe_reached_trailing_start") is True for t in losses)
                if losses and mfe_known else None,
            "sl_before_trail": sum(t.get("sl_before_trail") is True for t in trades)}


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
