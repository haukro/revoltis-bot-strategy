"""Preregistered ZEC/BTC/ETH momentum-proxy pre-screen.

Diagnostic only. No Strategy B PnL, exits, sizing, fees or optimization.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from math import log, sqrt
from statistics import median
from typing import Any


FIVE_MIN_MS = 300_000
ONE_HOUR_MS = 3_600_000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=False).encode()
    ).hexdigest()


def pack_market_series(candles: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        [int(c["open_time"]), float(c["high"]), float(c["close"])]
        for c in candles
    ]
    raw = json.dumps(rows, separators=(",", ":")).encode()
    return {
        "encoding": "gzip+base64",
        "count": len(rows),
        "sha256": _digest(rows),
        "data": base64.b64encode(gzip.compress(raw, mtime=0)).decode(),
    }


def unpack_market_series(snapshot: dict[str, Any]) -> list[tuple[int, float, float]]:
    if snapshot.get("encoding") != "gzip+base64":
        raise ValueError("invalid_snapshot_encoding")
    rows = json.loads(gzip.decompress(base64.b64decode(snapshot["data"])))
    if len(rows) != int(snapshot["count"]) or _digest(rows) != snapshot["sha256"]:
        raise ValueError("snapshot_integrity_error")
    out = [(int(ts), float(high), float(close)) for ts, high, close in rows]
    if not out or any(b[0] - a[0] != FIVE_MIN_MS for a, b in zip(out, out[1:])):
        raise ValueError("non_contiguous_5m_series")
    return out


def aggregate_1h(rows: list[tuple[int, float, float]]) -> list[tuple[int, float, float]]:
    buckets: dict[int, list[tuple[int, float, float]]] = {}
    for row in rows:
        bucket = (row[0] // ONE_HOUR_MS) * ONE_HOUR_MS
        buckets.setdefault(bucket, []).append(row)

    out: list[tuple[int, float, float]] = []
    for bucket in sorted(buckets):
        group = sorted(buckets[bucket])
        if len(group) != 12:
            continue
        if group[0][0] != bucket or group[-1][0] != bucket + 55 * 60_000:
            continue
        if any(b[0] - a[0] != FIVE_MIN_MS for a, b in zip(group, group[1:])):
            continue
        out.append((bucket, max(row[1] for row in group), group[-1][2]))
    return out


def align_three(
    zec: list[tuple[int, float, float]],
    btc: list[tuple[int, float, float]],
    eth: list[tuple[int, float, float]],
) -> tuple[list[tuple[int, float, float]], list[tuple[int, float, float]], list[tuple[int, float, float]]]:
    zd = {r[0]: r for r in zec}
    bd = {r[0]: r for r in btc}
    ed = {r[0]: r for r in eth}
    common = sorted(set(zd) & set(bd) & set(ed))
    return [zd[t] for t in common], [bd[t] for t in common], [ed[t] for t in common]


def log_returns(rows: list[tuple[int, float, float]]) -> list[tuple[int, float]]:
    out = []
    for prev, cur in zip(rows, rows[1:]):
        if prev[2] > 0 and cur[2] > 0:
            out.append((cur[0], log(cur[2] / prev[2])))
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(x * y for x, y in zip(dx, dy)) / denom if denom else None


def beta_ols(xs: list[float], ys: list[float]) -> float | None:
    """Slope for y = alpha + beta*x."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if not denom:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def breakout_indices(rows: list[tuple[int, float, float]], lookback: int = 20) -> set[int]:
    events: set[int] = set()
    for i in range(lookback, len(rows)):
        prior_high = max(rows[j][1] for j in range(i - lookback, i))
        if rows[i][2] > prior_high:
            events.add(i)
    return events


def _within(events: set[int], index: int, radius: int) -> bool:
    return any((index + offset) in events for offset in range(-radius, radius + 1))


def _event_followthrough(
    rows: list[tuple[int, float, float]],
    event_indices: list[int],
    forward_bars: int,
) -> dict[str, Any]:
    values = []
    for i in event_indices:
        j = i + forward_bars
        if j >= len(rows) or rows[i][2] <= 0:
            continue
        values.append((rows[j][2] / rows[i][2] - 1) * 100)
    return {
        "n": len(values),
        "mean_percent": sum(values) / len(values) if values else None,
        "median_percent": median(values) if values else None,
        "positive_share_percent": 100 * sum(v > 0 for v in values) / len(values) if values else None,
    }


def analyze_timeframe(
    zec: list[tuple[int, float, float]],
    btc: list[tuple[int, float, float]],
    eth: list[tuple[int, float, float]],
    *,
    forward_bars: int,
) -> dict[str, Any]:
    zec, btc, eth = align_three(zec, btc, eth)
    zr = log_returns(zec)
    br = log_returns(btc)
    er = log_returns(eth)
    common_ret = sorted(set(t for t, _ in zr) & set(t for t, _ in br) & set(t for t, _ in er))
    zd = dict(zr)
    bd = dict(br)
    ed = dict(er)
    zrv = [zd[t] for t in common_ret]
    brv = [bd[t] for t in common_ret]
    erv = [ed[t] for t in common_ret]

    ze = breakout_indices(zec)
    be = breakout_indices(btc)
    ee = breakout_indices(eth)
    z_events = sorted(ze)

    def conditional(events: set[int], radius: int) -> float | None:
        return 100 * sum(_within(events, i, radius) for i in z_events) / len(z_events) if z_events else None

    def idio(radius: int) -> tuple[float | None, list[int], list[int]]:
        idios = [i for i in z_events if not _within(be, i, radius) and not _within(ee, i, radius)]
        shared = [i for i in z_events if i not in set(idios)]
        share = 100 * len(idios) / len(z_events) if z_events else None
        return share, idios, shared

    idio1, _, _ = idio(1)
    idio3, idio3_events, shared3_events = idio(3)

    return {
        "aligned_bars": len(zec),
        "return_observations": len(common_ret),
        "correlation": {
            "zec_btc": pearson(zrv, brv),
            "zec_eth": pearson(zrv, erv),
            "btc_eth": pearson(brv, erv),
        },
        "breakouts": {
            "zec_count": len(ze),
            "btc_count": len(be),
            "eth_count": len(ee),
            "btc_given_zec_same_bar_percent": conditional(be, 0),
            "eth_given_zec_same_bar_percent": conditional(ee, 0),
            "btc_given_zec_pm1_percent": conditional(be, 1),
            "eth_given_zec_pm1_percent": conditional(ee, 1),
            "btc_given_zec_pm3_percent": conditional(be, 3),
            "eth_given_zec_pm3_percent": conditional(ee, 3),
            "zec_idiosyncratic_pm1_percent": idio1,
            "zec_idiosyncratic_pm3_percent": idio3,
        },
        "followthrough_1h": {
            "idiosyncratic_pm3": _event_followthrough(zec, idio3_events, forward_bars),
            "shared_pm3": _event_followthrough(zec, shared3_events, forward_bars),
        },
    }


def breakout_day_beta_r2(
    zec5: list[tuple[int, float, float]],
    btc5: list[tuple[int, float, float]],
    eth5: list[tuple[int, float, float]],
) -> dict[str, Any]:
    zec5, btc5, eth5 = align_three(zec5, btc5, eth5)
    ze = breakout_indices(zec5)
    breakout_days = {
        zec5[i][0] // 86_400_000
        for i in ze
    }
    zr = dict(log_returns(zec5))
    br = dict(log_returns(btc5))
    common = sorted(
        t for t in (set(zr) & set(br))
        if (t // 86_400_000) in breakout_days
    )
    x = [br[t] for t in common]
    y = [zr[t] for t in common]
    corr = pearson(x, y)
    beta = beta_ols(x, y)
    return {
        "breakout_utc_days": len(breakout_days),
        "return_observations": len(common),
        "beta_zec_on_btc": beta,
        "correlation_zec_btc": corr,
        "r_squared": corr * corr if corr is not None else None,
    }


def analyze_block(
    zec5: list[tuple[int, float, float]],
    btc5: list[tuple[int, float, float]],
    eth5: list[tuple[int, float, float]],
) -> dict[str, Any]:
    zec5, btc5, eth5 = align_three(zec5, btc5, eth5)
    zec1 = aggregate_1h(zec5)
    btc1 = aggregate_1h(btc5)
    eth1 = aggregate_1h(eth5)
    return {
        "5m": analyze_timeframe(zec5, btc5, eth5, forward_bars=12),
        "1h": analyze_timeframe(zec1, btc1, eth1, forward_bars=1),
        "breakout_day_beta_5m": breakout_day_beta_r2(zec5, btc5, eth5),
    }


def pooled_analysis(blocks: list[dict[str, list[tuple[int, float, float]]]]) -> dict[str, Any]:
    # Correlations and beta are recomputed from all within-block observations.
    # Breakout counts/overlaps are summed blockwise so no 20-bar lookback crosses a block boundary.
    per = [analyze_block(b["ZEC"], b["BTC"], b["ETH"]) for b in blocks]

    def pooled_tf(tf: str) -> dict[str, Any]:
        # correlation uses concatenated within-block return vectors
        pairs = {"zec_btc": ([], []), "zec_eth": ([], []), "btc_eth": ([], [])}
        breakout_sums = {
            "zec_count": 0,
            "btc_count": 0,
            "eth_count": 0,
        }
        numerators = {
            "btc_given_zec_same_bar_percent": 0.0,
            "eth_given_zec_same_bar_percent": 0.0,
            "btc_given_zec_pm1_percent": 0.0,
            "eth_given_zec_pm1_percent": 0.0,
            "btc_given_zec_pm3_percent": 0.0,
            "eth_given_zec_pm3_percent": 0.0,
            "zec_idiosyncratic_pm1_percent": 0.0,
            "zec_idiosyncratic_pm3_percent": 0.0,
        }
        follow_idio = []
        follow_shared = []

        for block in blocks:
            z = block["ZEC"] if tf == "5m" else aggregate_1h(block["ZEC"])
            b = block["BTC"] if tf == "5m" else aggregate_1h(block["BTC"])
            e = block["ETH"] if tf == "5m" else aggregate_1h(block["ETH"])
            z, b, e = align_three(z, b, e)
            zr = dict(log_returns(z)); br = dict(log_returns(b)); er = dict(log_returns(e))
            common = sorted(set(zr) & set(br) & set(er))
            for t in common:
                pairs["zec_btc"][0].append(zr[t]); pairs["zec_btc"][1].append(br[t])
                pairs["zec_eth"][0].append(zr[t]); pairs["zec_eth"][1].append(er[t])
                pairs["btc_eth"][0].append(br[t]); pairs["btc_eth"][1].append(er[t])

            ze = breakout_indices(z); be = breakout_indices(b); ee = breakout_indices(e)
            z_events = sorted(ze)
            breakout_sums["zec_count"] += len(ze)
            breakout_sums["btc_count"] += len(be)
            breakout_sums["eth_count"] += len(ee)

            for key, events, radius in (
                ("btc_given_zec_same_bar_percent", be, 0),
                ("eth_given_zec_same_bar_percent", ee, 0),
                ("btc_given_zec_pm1_percent", be, 1),
                ("eth_given_zec_pm1_percent", ee, 1),
                ("btc_given_zec_pm3_percent", be, 3),
                ("eth_given_zec_pm3_percent", ee, 3),
            ):
                numerators[key] += sum(_within(events, i, radius) for i in z_events)
            numerators["zec_idiosyncratic_pm1_percent"] += sum(
                not _within(be, i, 1) and not _within(ee, i, 1) for i in z_events
            )
            idio_events = [
                i for i in z_events
                if not _within(be, i, 3) and not _within(ee, i, 3)
            ]
            numerators["zec_idiosyncratic_pm3_percent"] += len(idio_events)
            shared_events = [i for i in z_events if i not in set(idio_events)]
            fb = 12 if tf == "5m" else 1
            for i in idio_events:
                if i + fb < len(z):
                    follow_idio.append((z[i + fb][2] / z[i][2] - 1) * 100)
            for i in shared_events:
                if i + fb < len(z):
                    follow_shared.append((z[i + fb][2] / z[i][2] - 1) * 100)

        zcount = breakout_sums["zec_count"]
        def follow(vals: list[float]) -> dict[str, Any]:
            return {
                "n": len(vals),
                "mean_percent": sum(vals) / len(vals) if vals else None,
                "median_percent": median(vals) if vals else None,
                "positive_share_percent": 100 * sum(v > 0 for v in vals) / len(vals) if vals else None,
            }

        return {
            "correlation": {k: pearson(v[0], v[1]) for k, v in pairs.items()},
            "breakouts": {
                **breakout_sums,
                **{
                    k: (100 * v / zcount if zcount else None)
                    for k, v in numerators.items()
                },
            },
            "followthrough_1h": {
                "idiosyncratic_pm3": follow(follow_idio),
                "shared_pm3": follow(follow_shared),
            },
        }

    # pooled breakout-day beta/r2 from all within-block breakout days
    xs: list[float] = []
    ys: list[float] = []
    total_days = 0
    for block in blocks:
        z, b, e = align_three(block["ZEC"], block["BTC"], block["ETH"])
        ze = breakout_indices(z)
        days = {z[i][0] // 86_400_000 for i in ze}
        total_days += len(days)
        zr = dict(log_returns(z)); br = dict(log_returns(b))
        for t in sorted(set(zr) & set(br)):
            if t // 86_400_000 in days:
                xs.append(br[t]); ys.append(zr[t])
    corr = pearson(xs, ys)
    beta = beta_ols(xs, ys)

    result = {
        "5m": pooled_tf("5m"),
        "1h": pooled_tf("1h"),
        "breakout_day_beta_5m": {
            "breakout_utc_days": total_days,
            "return_observations": len(xs),
            "beta_zec_on_btc": beta,
            "correlation_zec_btc": corr,
            "r_squared": corr * corr if corr is not None else None,
        },
    }
    overlap = result["5m"]["breakouts"]["btc_given_zec_pm3_percent"]
    r2 = result["breakout_day_beta_5m"]["r_squared"]
    idio = result["5m"]["breakouts"]["zec_idiosyncratic_pm3_percent"]
    if (overlap is not None and overlap >= 60) or (r2 is not None and r2 >= .50):
        verdict = "DO_NOT_BUILD_ZEC_ONLY_B"
    elif (
        overlap is not None and overlap < 60
        and r2 is not None and r2 < .50
        and idio is not None and idio >= 40
    ):
        verdict = "ZEC_B_PREREGISTRATION_ELIGIBLE"
    else:
        verdict = "INCONCLUSIVE_FOR_ZEC_B"
    result["verdict"] = verdict
    result["routing_inputs"] = {
        "zec_btc_5m_pm3_overlap_percent": overlap,
        "zec_btc_breakout_day_r_squared": r2,
        "zec_5m_idiosyncratic_pm3_percent": idio,
    }
    return {"blocks": per, "pooled": result}
