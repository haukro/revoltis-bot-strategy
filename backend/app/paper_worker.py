"""P3 orchestration. Every authoritative decision remains in the P2 RPCs."""
import asyncio
from copy import deepcopy
from importlib.metadata import version
import platform
import re
from math import isfinite
from uuid import uuid4

import httpx

from .paper_assessment import prepare_assessment
from .paper_measurement import (
    MeasurementError, candle_window, closed_only, cost_book_reason,
    engine_sha256, hash_json, simulation_tape,
)
from .simulation import simulate


class RpcError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class PaperRPC:
    def __init__(self, client, url, key):
        self.client, self.url, self.key = client, url.rstrip("/"), key

    async def call(self, name, **params):
        response = await self.client.post(self.url + "/rest/v1/rpc/" + name, json=params,
            headers={"apikey": self.key, "Authorization": "Bearer " + self.key})
        if response.is_error:
            # No database detail, request headers, URLs or credentials in output.
            message = response.json().get("message", "") if "json" in response.headers.get("content-type", "") else ""
            raise RpcError("candle_conflict" if message == "candle_conflict" else "paper_rpc_failed")
        return response.json()


class PaperWorker:
    def __init__(self, rpc, market, *, code_commit, timeout_seconds=90):
        self.rpc, self.market, self.code_commit = rpc, market, code_commit
        self.timeout_seconds = timeout_seconds

    async def assess(self, run_id):
        snapshot = await self.rpc.call("paper_read_run", p_run_id=run_id)
        args = prepare_assessment(snapshot, evaluator_commit=self.code_commit)
        return await self.rpc.call("paper_assess_run", **args)

    async def build(self, test, horizon):
        """Persistable evidence; no invented tape when data or engine evidence is missing."""
        run_id = str(uuid4())
        window = candle_window(test["start_ms"], horizon, test["settings"]["warmup_bars"])
        run = {"id": run_id, "test_end_ms": horizon,
               "code_commit": self.code_commit, "engine_sha256": engine_sha256(),
               "settings_sha256": hash_json(test["settings"]), "candle_source": "okx",
               "candle_first_open_ms": window.first_open_ms, "candle_last_open_ms": window.last_open_ms,
               "expected_count": window.expected_count, "candle_count": 0,
               "candles_sha256": None, "last_candle_close_raw_ms": None,
               "cost_profile": None, "cost_profile_sha256": None, "book_ts_ms": None,
               "cost_fetched_at_ms": None, "trade_count": None, "open_count": None,
               "tape_sha256": None, "summary": None, "status": "valid", "invalid_reason": None,
               "raw": {"python": platform.python_version(), "dependencies": {
                   name: version(name) for name in ("fastapi", "pydantic", "httpx")}}}
        reasons, candles, trades, positions = [], None, None, None
        if not isinstance(self.code_commit, str) or not re.fullmatch("[0-9a-f]{40}", self.code_commit):
            reasons.append("unknown_code_version")
        if run["settings_sha256"] != test["settings_sha256"]:
            reasons.append("settings_mismatch")
        try:
            source = await self.market.candles(test["pair"], window)
            candles = closed_only(source, start_ms=test["start_ms"], as_of_ms=horizon,
                                  warmup_bars=test["settings"]["warmup_bars"])
            await self.rpc.call("paper_archive_candles", p_source="okx", p_pair=test["pair"],
                                p_timeframe="5m", p_rows=candles)
            run.update(candles_sha256=hash_json(candles), candle_count=len(candles),
                       last_candle_close_raw_ms=candles[-1]["close_time_raw_ms"] if candles else None)
        except (MeasurementError, RpcError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            candles = None
            reasons.append("candle_conflict" if isinstance(error, RpcError) and error.code == "candle_conflict"
                           else "incomplete_candles")
        try:
            profile, fetched = await self.market.cost(test["pair"], float(test["settings"]["stake_amount"]))
            if not isinstance(profile, dict) or any(
                isinstance(profile.get(key), bool) or not isinstance(profile.get(key), (int, float))
                or not isfinite(profile[key]) or profile[key] < 0
                for key in ("entry_cost_rate", "exit_cost_rate", "fee_rate", "half_spread", "buy_impact", "sell_impact")
            ):
                raise MeasurementError("cost_profile_unavailable")
            stamp = int(profile["book_ts"]) if profile.get("book_ts") is not None else None
            run.update(cost_profile=profile, cost_profile_sha256=hash_json(profile),
                       book_ts_ms=stamp, cost_fetched_at_ms=fetched)
            reason = cost_book_reason(stamp, fetched)
            if reason:
                reasons.append(reason)
        except (MeasurementError, httpx.HTTPError, ValueError, KeyError, TypeError):
            reasons.append("cost_profile_unavailable")
        if not reasons:
            engine_rows = [{"open_time": row["open_time_ms"], "close_time": row["close_time_raw_ms"],
                            **{k: row[k] for k in ("open", "high", "low", "close", "volume", "quote_volume")}}
                           for row in candles]
            try:
                # The thread only computes. Cancellation can never perform a late persist.
                result = await asyncio.to_thread(simulate, {test["pair"]: engine_rows}, deepcopy(test["settings"]),
                    trading_start_time=test["start_ms"], force_close_at_end=False,
                    cost_models={test["pair"]: deepcopy(run["cost_profile"])})
                trades = simulation_tape(run_id, result)
                run.update(summary=result["metrics"], trade_count=len(trades), tape_sha256=hash_json(trades))
                run["raw"]["engine_open_count"] = result["open_positions"]
                if result["open_positions"] == 0:
                    positions, run["open_count"] = [], 0
                else:
                    # Frozen engine exposes only the count. Never fabricate rows
                    # or force-close positions to obtain an apparently valid run.
                    reasons.append("missing_open_positions")
            except (ValueError, KeyError, TypeError, ArithmeticError):
                reasons.append("simulation_failed")
        if reasons:
            run.update(status="invalid", invalid_reason=reasons[0])
            run["raw"]["failure_reasons"] = reasons
        return run, trades, positions

    async def _outcome(self, test_id, tick_id, outcome, reason=None, claim=None):
        return await self.rpc.call("paper_worker_outcome", p_test_id=test_id, p_tick_id=tick_id,
            p_outcome=outcome, p_reason=reason, p_claim_id=claim["id"] if claim else None,
            p_lease_token=claim["lease_token"] if claim else None)

    async def _test(self, test, tick_id):
        claim = None
        try:
            for run_id in test["pending_run_ids"]:
                assessment = await self.assess(run_id)
                if assessment["outcome"] == "origin_pending":
                    return await self._outcome(test["id"], tick_id, "evaluation_pending")
                if assessment["outcome"] != "assessed":
                    return await self._outcome(test["id"], tick_id, "error", "assessment_blocked")
                if assessment.get("verdict") is not None:
                    return await self._outcome(test["id"], tick_id, "test_closed")
            if test.get("tick_used"):
                return {"outcome": test.get("tick_outcome") or "tick_already_used"}
            if test["shadow"]:
                stopped = await self.rpc.call("paper_stop_shadow", p_test_id=test["id"])
                if stopped["outcome"] in ("stopped", "test_closed"):
                    return await self._outcome(test["id"], tick_id, "shadow_ended")
            # This is a local worker budget, not certification of a Vercel limit.
            if test["lease_ms"] <= self.timeout_seconds * 1000:
                return await self._outcome(test["id"], tick_id, "error", "lease_below_worker_budget")
            latest = await self.market.latest(test["pair"])
            claimed = await self.rpc.call("paper_claim_run", p_test_id=test["id"],
                p_tick_id=tick_id, p_last_candle_open_ms=latest)
            if claimed["outcome"] != "claimed":
                return {"outcome": claimed["outcome"]}
            claim = claimed["claim"]
            run, trades, positions = await self.build(test, claim["test_end_ms"])
            # AT35: automatic persist never accepts/sends a caller's horizon.
            run.pop("test_end_ms")
            persisted = await self.rpc.call("paper_persist_run", p_claim_id=claim["id"],
                p_lease_token=claim["lease_token"], p_run=run, p_trades=trades, p_positions=positions)
            if persisted["outcome"] != "run_created":
                return await self._outcome(test["id"], tick_id, persisted["outcome"], claim=claim)
            row = persisted["run"]
            response = {"outcome": "run_created", "run_id": row["id"],
                        "status": row["status"], "invalid_reason": row["invalid_reason"]}
            if row["test_end_ms"] >= test["start_ms"] + test["gate_ms"]:
                response["assessment"] = (await self.assess(row["id"]))["outcome"]
            return response
        except asyncio.CancelledError:
            # Best-effort heartbeat for a known timeout. A hard process kill is
            # still recovered by lease expiry; cancellation never resumes persist.
            try:
                async with asyncio.timeout(3):
                    await self._outcome(test["id"], tick_id, "error", "worker_timeout", claim)
            except (TimeoutError, RpcError, httpx.HTTPError):
                pass
            raise
        except (MeasurementError, RpcError, httpx.HTTPError, ValueError, KeyError, TypeError):
            return await self._outcome(test["id"], tick_id, "error", "worker_failed", claim)

    async def tick(self, tick_id):
        results, after = [], None
        async with asyncio.timeout(self.timeout_seconds):
            while True:
                page = await self.rpc.call("paper_worker_page", p_tick_id=tick_id, p_after=after)
                if page["outcome"] != "ready":
                    return {"outcome": page["outcome"], "results": results}
                for test in page["tests"]:
                    outcome = await self._test(test, tick_id)
                    results.append({"test_id": test["id"], **outcome})
                if not page["tests"] or len(page["tests"]) < 100:
                    break
                after = page["tests"][-1]["id"]
        return {"outcome": "processed", "results": results}

    async def diagnostic(self, test_id):
        async with asyncio.timeout(self.timeout_seconds):
            context = await self.rpc.call("paper_diagnostic_context", p_test_id=test_id)
            if context["outcome"] != "ready":
                return context
            run, trades, positions = await self.build(context["test"], context["test_end_ms"])
            row = await self.rpc.call("paper_persist_diagnostic", p_test_id=test_id,
                                      p_run=run, p_trades=trades, p_positions=positions)
            return {"outcome": "diagnostic_created", "run_id": row["id"], "status": row["status"],
                    "invalid_reason": row["invalid_reason"]}
