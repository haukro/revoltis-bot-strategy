from __future__ import annotations

import os
import re
import asyncio
import math
import secrets
from uuid import uuid4
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .local_store import LocalStore
from .simulation import simulate
from .optimizer import optimize, present_optimizer_record, assess_candidate, aggregate_metrics, walk_forward_windows
from .trade_audit import matches_target, prepare_replay, replay_validation, unpack_snapshot, trade_tape, tape_summary, entry_path_audit
from .costs import book_costs, FEE_SCHEDULE, FEE_TAKER, FEE_MAKER, net_return
from .tsmom_b_v1 import (
    pack_ohlc_snapshot,
    unpack_ohlc_snapshot,
    simulate_b_v1,
    smoke_cases as tsmom_b_v1_smoke_cases,
)
from .tsmom_c_v1 import (
    pack_ohlc_snapshot as pack_c_ohlc_snapshot,
    unpack_ohlc_snapshot as unpack_c_ohlc_snapshot,
    continuity_certificate as tsmom_c_v1_continuity_certificate,
    simulate_c_v1,
    smoke_cases as tsmom_c_v1_smoke_cases,
)
from .momentum_prescreen import pack_market_series, unpack_market_series, analyze_block, pooled_analysis
from .paper_execution import smoke_cases as paper_execution_smoke_cases
from .paper_ops import (
    canonical_book_payload,
    canonical_book_hash,
    walk_canonical_quote_notional,
    quote_fee_amount,
    remaining_quote_notional,
    walk_canonical_base_quantity,
    smoke_cases as paper_ops_smoke_cases,
)
from .rebound_experiment import (
    filter_b as rebound_filter_b,
    rebound_confirmation_context,
    enrich_trade_path,
    paired_metrics,
    smoke_cases as rebound_smoke_cases,
)


class KillSwitchRequest(BaseModel):
    target_state: Literal["HALT_NEW_ENTRIES", "HALTED"]
    reason_code: str = Field(min_length=1, max_length=160)


class RecoveryRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=160)


class StrategySettings(BaseModel):
    selected_pairs: list[str] = Field(default_factory=lambda: ["PEPE/USDT", "DOGE/USDT", "WIF/USDT", "BONK/USDT", "SUI/USDT"])
    timeframe: Literal["1m", "3m", "5m", "15m"] = "3m"
    initial_capital: float = Field(100, ge=10, le=100000)
    stake_amount: float = Field(50, ge=5)
    max_open_trades: int = Field(1, ge=1, le=5)
    daily_trade_limit: int = Field(10, ge=1, le=100)
    bb_period: int = Field(20, ge=5, le=100)
    bb_deviation: float = Field(2.0, ge=0.5, le=5)
    rsi_period: int = Field(14, ge=2, le=50)
    rsi_oversold: int = Field(35, ge=5, le=60)
    atr_period: int = Field(14, ge=2, le=50)
    atr_min_percent: float = Field(0.15, ge=0.01, le=10)
    atr_max_percent: float = Field(4.0, ge=0.1, le=20)
    min_volume_ratio: float = Field(0.8, ge=0.1, le=5)
    min_quote_volume_usdt: float = Field(10000, ge=0)
    rebound_min_percent: float = Field(0.3, ge=0.01, le=5)
    rebound_max_percent: float = Field(0.6, ge=0.01, le=10)
    stop_loss_percent: float = Field(4.0, ge=0.1, le=50)
    trailing_start_percent: float = Field(1.2, ge=0.1, le=20)
    trailing_distance_percent: float = Field(0.5, ge=0.05, le=10)
    max_no_trail_hours: float = Field(0, ge=0, le=168)


class SimulatedTrade(BaseModel):
    id: str
    pair: str
    status: Literal["open", "closed", "cancelled"]
    opened_at: str | None = None
    closed_at: str | None = None
    entry_rate: float | None = None
    exit_rate: float | None = None
    stake_amount: float | None = None
    profit_usdt: float = 0
    exit_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SyncBatch(BaseModel):
    source: str = "freqtrade-dry-run"
    trades: list[SimulatedTrade] = Field(default_factory=list)


class StrategyVersionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)
    settings: StrategySettings
    universe: dict[str, Any] | None = None


class UniversePickRequest(BaseModel):
    quote_ccy: str = "USDT"
    max_picks: int = Field(5, ge=1, le=8)
    timeframe: Literal["5m", "15m"] = "5m"
    trade_notional_usdt: float = Field(50, ge=5, le=10000)
    lookback_hours: int = Field(48, ge=24, le=72)
    lock_hours: int = Field(12, ge=6, le=24)
    correlation_lookback_hours: int = Field(24, ge=12, le=48)
    max_pairwise_correlation: float = Field(.75, ge=.2, le=.99)
    min_listing_age_days: int = Field(14, ge=1, le=365)
    min_quote_volume_24h: float = Field(2_000_000, ge=0)
    max_spread_ratio: float = Field(.0008, gt=0, le=.01)
    max_book_impact_ratio: float = Field(.001, gt=0, le=.02)
    min_atr_ratio: float = Field(.004, ge=0, le=.1)
    max_atr_ratio: float = Field(.025, gt=0, le=.2)
    max_abs_change_24h_ratio: float = Field(.25, gt=0, le=2)
    max_data_age_seconds: int = Field(30, ge=5, le=300)


class BacktestResultCreate(BaseModel):
    strategy_version_id: str
    timerange: str = Field(min_length=8, max_length=80)
    data_source: str = Field(default="Freqtrade backtest", max_length=100)
    metrics: dict[str, float | int | str | None]
    report: dict[str, Any] = Field(default_factory=dict)


class SignalDiagnostic(BaseModel):
    id: str | None = None
    pair: str
    occurred_at: str
    decision: Literal["entered", "rejected"]
    reason: str = Field(min_length=2, max_length=100)
    strategy_version_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class SimulationRequest(BaseModel):
    settings: StrategySettings
    candle_limit: int = Field(default=500, ge=100, le=45000)
    start_time: int | None = Field(default=None, ge=0)
    end_time: int | None = Field(default=None, ge=0)
    force_close_at_end: bool = False


class OptimizerRequest(BaseModel):
    settings: StrategySettings
    version_id: str | None = None
    pairs: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    timeframes: list[Literal["1m", "3m", "5m", "15m"]] = Field(default_factory=lambda: ["1m", "3m", "5m", "15m"])
    history_days: Literal[1, 7, 14, 30, 90] = 7
    trials_per_market: int = Field(default=3, ge=1, le=8)


class OptimizerFinalizeRequest(BaseModel):
    version_id: str
    source_job_ids: list[str] = Field(min_length=1, max_length=20)


class ValidationReplayRequest(BaseModel):
    # Deliberately no settings, dates, pair, timeframe or grid parameters.
    source_job_ids: list[str] = Field(min_length=1, max_length=2)
    model_config = {"extra": "forbid"}


class TimeoutStudyRequest(BaseModel):
    source_job_id: str
    version_id: str
    max_no_trail_hours: Literal[4, 8, 12, 24]
    model_config = {"extra": "forbid"}


class Previous90dOOSRequest(BaseModel):
    source_job_id: str
    version_id: str
    model_config = {"extra": "forbid"}


DEFAULT = StrategySettings()
local_store = LocalStore()
app = FastAPI(title="Revoltis Bot Strategy API", version="1.0.0")
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
OKX_BASE_URL = "https://www.okx.com"
OKX_TICKERS_URL = f"{OKX_BASE_URL}/api/v5/market/tickers"
OKX_INSTRUMENTS_URL = f"{OKX_BASE_URL}/api/v5/public/instruments"
OKX_BOOKS_URL = f"{OKX_BASE_URL}/api/v5/market/books"
OKX_CANDLES_URL = f"{OKX_BASE_URL}/api/v5/market/candles"
OKX_HISTORY_CANDLES_URL = f"{OKX_BASE_URL}/api/v5/market/history-candles"
ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}
TIMEFRAME_MILLISECONDS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
optimizer_jobs: dict[str, dict[str, Any]] = {}
validation_replay_lock = asyncio.Lock()
validation_replay_attempts: dict[tuple[str, ...], dict] = {}
OKX_HISTORY_CONCURRENCY = 3
okx_history_semaphore = asyncio.Semaphore(OKX_HISTORY_CONCURRENCY)
okx_history_rate_lock = asyncio.Lock()
okx_history_last_request = 0.0
okx_candle_cache: dict[tuple[str, str, int, int, int], tuple[float, list[dict[str, Any]]]] = {}
OKX_HISTORY_MIN_INTERVAL = .15
OKX_CANDLE_CACHE_SECONDS = 15 * 60


def supabase_headers() -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}


def persistence_status() -> dict:
    durable = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    return {"mode": "supabase" if durable else "demo", "durable": durable,
            "production_ready": durable or not bool(os.getenv("VERCEL"))}


def require_durable_production_store() -> None:
    if not persistence_status()["production_ready"]:
        raise HTTPException(503, "persistence_unavailable: Supabase nie je nakonfigurovaný. Lock sa v produkcii nedá trvalo uložiť.")


async def supabase_get(table: str, query: str = "") -> list[dict]:
    url = os.getenv("SUPABASE_URL")
    if not url or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        rows = local_store.get(table)
        if "order=" in query:
            order_field = query.split("order=", 1)[1].split(".", 1)[0]
            rows.sort(key=lambda item: item.get(order_field) or "", reverse=".desc" in query)
        if "limit=" in query:
            try:
                rows = rows[:int(query.split("limit=", 1)[1].split("&", 1)[0])]
            except ValueError:
                pass
        return rows
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{url}/rest/v1/{table}?{query}", headers=supabase_headers())
        response.raise_for_status()
        return response.json()


async def supabase_upsert(table: str, record: dict) -> dict:
    url = os.getenv("SUPABASE_URL")
    if not url or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        return local_store.upsert(table, [record])[0]
    headers = supabase_headers() | {"Prefer": "resolution=merge-duplicates,return=representation"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{url}/rest/v1/{table}?on_conflict=id", headers=headers, json=record)
        response.raise_for_status()
        return response.json()[0]


async def supabase_upsert_many(table: str, records: list[dict]) -> list[dict]:
    if not records:
        return []
    url = os.getenv("SUPABASE_URL")
    if not url or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        return local_store.upsert(table, records)
    headers = supabase_headers() | {"Prefer": "resolution=merge-duplicates,return=representation"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{url}/rest/v1/{table}?on_conflict=id", headers=headers, json=records)
        response.raise_for_status()
        return response.json()


async def supabase_rpc(function_name: str, payload: dict[str, Any]) -> Any:
    """Call a Supabase RPC using the service role. No local in-memory fallback."""
    url = os.getenv("SUPABASE_URL")
    if not url or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        raise HTTPException(503, "persistence_unavailable: RPC vyžaduje Supabase.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{url}/rest/v1/rpc/{function_name}",
            headers=supabase_headers(),
            json=payload,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return None


def _require_internal_secret(provided: str | None, env_name: str) -> None:
    expected = os.getenv(env_name)
    if not expected:
        raise HTTPException(503, f"{env_name.lower()}_not_configured")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(401, "unauthorized")


async def _paper_ops_snapshot() -> dict[str, Any]:
    result = await supabase_rpc("paper_ops_health_snapshot", {"p_account_key": "paper-default"})
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return result[0]
    raise HTTPException(503, "ops_health_unavailable")



async def _paper_active_ops_policy() -> dict[str, Any]:
    state = await supabase_get(
        "ops_policy_state",
        "account_key=eq.paper-default&select=policy_version_id&limit=1",
    )
    policy_id = (state[0] if state else {}).get("policy_version_id")
    if not policy_id:
        raise HTTPException(503, "paper_ops_policy_not_configured")
    rows = await supabase_get("ops_policy_versions", f"id=eq.{policy_id}&limit=1")
    if not rows:
        raise HTTPException(503, "paper_ops_policy_missing")
    return rows[0]


async def _paper_runtime_binding(strategy_version_id: str) -> dict[str, Any] | None:
    rows = await supabase_get(
        "paper_strategy_runtime_bindings",
        f"strategy_version_id=eq.{strategy_version_id}&limit=1",
    )
    return rows[0] if rows else None


async def _fetch_okx_execution_book(pair: str, depth: int) -> dict[str, Any]:
    if depth < 1 or depth > 400:
        raise ValueError("invalid_market_book_depth")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.get(
            OKX_BOOKS_URL,
            params={"instId": pair.replace("/", "-"), "sz": depth},
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("okx_book_unavailable")
    book = payload["data"][0]
    provider_ts_ms = int(book.get("ts") or 0)
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    canonical = canonical_book_payload(
        provider="okx",
        pair=pair,
        provider_ts_ms=provider_ts_ms,
        bids=bids,
        asks=asks,
    )
    snapshot_hash = canonical_book_hash(
        provider="okx",
        pair=pair,
        provider_ts_ms=provider_ts_ms,
        bids=bids,
        asks=asks,
    )
    return {
        **canonical,
        "snapshot_hash": snapshot_hash,
        "received_at": datetime.now(UTC).isoformat(),
    }


async def _paper_ack(
    outbox_id: str,
    worker_id: str,
    lease_generation: int,
    error: str | None = None,
) -> Any:
    return await supabase_rpc(
        "paper_ack_outbox",
        {
            "p_outbox_id": outbox_id,
            "p_worker_id": worker_id,
            "p_lease_generation": lease_generation,
            "p_error": error,
        },
    )


@app.get("/api/paper/spec-004/smoke")
async def paper_spec_004_smoke():
    """Synthetic-only checks for the locked strategy-neutral execution primitives."""
    checks = paper_execution_smoke_cases()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "live_trading": False,
        "alpha_logic_touched": False,
        "strategy_b_modified": False,
        "spec": "IMPLEMENTATION-SPEC-004",
        "spec_commit": "d94f624a685941c1eba9f3e1fe166371422437ce",
    }


@app.get("/api/paper/system-health")
async def paper_system_health():
    """Blind-safe operational health only. No strategy/trade performance."""
    require_durable_production_store()
    rows = await supabase_get("kill_switch_state", "account_key=eq.paper-default&limit=1")
    state = rows[0] if rows else None
    return {
        "status": "ok" if state else "degraded",
        "persistence": persistence_status(),
        "kill_switch": {
            "state": (state or {}).get("state"),
            "reason_code": (state or {}).get("reason_code"),
            "updated_at": (state or {}).get("updated_at"),
        },
        "live_trading": False,
        "blind_safe": True,
    }


@app.get("/api/paper/market-health")
async def paper_market_health():
    """Blind-safe aggregate feed health; never returns pair/timeframe rows."""
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    counts = snapshot.get("market_health_counts", {})
    degraded = sum(
        int(counts.get(key, 0) or 0)
        for key in ("STALE", "DEGRADED", "HALTED")
    )
    return {
        "status": "degraded" if degraded else "ok",
        "status_counts": counts,
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/spec-005/smoke")
async def paper_spec_005_smoke():
    checks = paper_ops_smoke_cases()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "live_trading": False,
        "alpha_logic_touched": False,
        "strategy_b_modified": False,
        "spec": "IMPLEMENTATION-SPEC-005",
        "spec_commit": "78590fea472050021bddaa62ba199aa6fe95611d",
    }


@app.get("/api/paper/queue-health")
async def paper_queue_health():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    return {
        "queue": snapshot.get("queue", {}),
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/worker-health")
async def paper_worker_health():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    return {
        "workers": snapshot.get("workers", []),
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/reconciliation")
async def paper_reconciliation_health():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    return {
        "reconciliation": snapshot.get("reconciliation", {}),
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/audit-health")
async def paper_audit_health():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    reconciliation = snapshot.get("reconciliation", {})
    latest = reconciliation.get("latest", {}) if isinstance(reconciliation, dict) else {}
    return {
        "audit_integrity_passed": latest.get("audit_integrity_passed"),
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/kill-switch")
async def paper_kill_switch_status():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    return {
        "kill_switch": snapshot.get("kill_switch", {}),
        "blind_safe": True,
        "live_trading": False,
    }


@app.get("/api/paper/blind-status")
async def paper_blind_status():
    require_durable_production_store()
    snapshot = await _paper_ops_snapshot()
    return {
        "blind": snapshot.get("blind", {}),
        "blind_safe": True,
        "live_trading": False,
    }


@app.post("/api/paper/kill-switch/activate")
async def paper_kill_switch_activate(
    request: KillSwitchRequest,
    x_ops_admin_token: str | None = Header(default=None, alias="X-Ops-Admin-Token"),
):
    require_durable_production_store()
    _require_internal_secret(x_ops_admin_token, "OPS_ADMIN_TOKEN")
    return await supabase_rpc(
        "paper_set_kill_switch_state",
        {
            "p_target_state": request.target_state,
            "p_reason_code": request.reason_code,
            "p_actor": "ops-api",
            "p_software_commit": os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown"),
            "p_account_key": "paper-default",
        },
    )


@app.post("/api/paper/kill-switch/request-recovery")
async def paper_kill_switch_request_recovery(
    request: RecoveryRequest,
    x_ops_admin_token: str | None = Header(default=None, alias="X-Ops-Admin-Token"),
):
    require_durable_production_store()
    _require_internal_secret(x_ops_admin_token, "OPS_ADMIN_TOKEN")
    return await supabase_rpc(
        "paper_request_recovery",
        {
            "p_reason_code": request.reason_code,
            "p_actor": "ops-api",
            "p_software_commit": os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown"),
            "p_account_key": "paper-default",
        },
    )


@app.post("/api/paper/kill-switch/enable")
async def paper_kill_switch_enable(
    x_ops_admin_token: str | None = Header(default=None, alias="X-Ops-Admin-Token"),
):
    require_durable_production_store()
    _require_internal_secret(x_ops_admin_token, "OPS_ADMIN_TOKEN")
    return await supabase_rpc(
        "paper_enable_after_recovery",
        {
            "p_actor": "ops-api",
            "p_software_commit": os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown"),
            "p_account_key": "paper-default",
        },
    )


@app.post("/api/internal/paper/recover")
async def internal_paper_recover(
    x_paper_scheduler_token: str | None = Header(default=None, alias="X-Paper-Scheduler-Token"),
):
    require_durable_production_store()
    _require_internal_secret(x_paper_scheduler_token, "PAPER_SCHEDULER_TOKEN")
    policy = await _paper_active_ops_policy()
    limit = int(policy.get("max_worker_batch") or 0)
    if limit < 1:
        raise HTTPException(503, "paper_max_worker_batch_not_configured")

    run_id = str(uuid4())
    commit = os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown")
    worker = "paper-recovery-worker"
    await supabase_rpc(
        "paper_record_worker_heartbeat",
        {
            "p_worker_name": worker,
            "p_run_id": run_id,
            "p_phase": "START",
            "p_status": "RUNNING",
            "p_claimed_count": 0,
            "p_processed_count": 0,
            "p_error_code": None,
            "p_software_commit": commit,
        },
    )

    processed = 0
    try:
        candidates = await supabase_rpc(
            "paper_recovery_candidates",
            {"p_account_key": "paper-default", "p_limit": limit},
        )
        candidates = candidates or {}

        for reservation_id in candidates.get("stale_reservations", [])[:limit]:
            result = await supabase_rpc(
                "paper_release_stale_reservation",
                {
                    "p_reservation_id": reservation_id,
                    "p_worker_id": worker,
                    "p_software_commit": commit,
                    "p_account_key": "paper-default",
                },
            )
            if (result or {}).get("released"):
                processed += 1

        remaining = max(0, limit - processed)
        for order_id in candidates.get("kill_switch_cancel_orders", [])[:remaining]:
            result = await supabase_rpc(
                "paper_recover_order",
                {
                    "p_order_id": order_id,
                    "p_action": "KILL_SWITCH_CANCEL",
                    "p_worker_id": worker,
                    "p_software_commit": commit,
                    "p_account_key": "paper-default",
                },
            )
            if (result or {}).get("applied"):
                processed += 1

        remaining = max(0, limit - processed)
        for order_id in candidates.get("expired_orders", [])[:remaining]:
            result = await supabase_rpc(
                "paper_recover_order",
                {
                    "p_order_id": order_id,
                    "p_action": "ORDER_TIMEOUT",
                    "p_worker_id": worker,
                    "p_software_commit": commit,
                    "p_account_key": "paper-default",
                },
            )
            if (result or {}).get("applied"):
                processed += 1

        await supabase_rpc(
            "paper_record_worker_heartbeat",
            {
                "p_worker_name": worker,
                "p_run_id": run_id,
                "p_phase": "COMPLETE",
                "p_status": "IDLE",
                "p_claimed_count": len(candidates.get("stale_reservations", []))
                    + len(candidates.get("kill_switch_cancel_orders", []))
                    + len(candidates.get("expired_orders", [])),
                "p_processed_count": processed,
                "p_error_code": None,
                "p_software_commit": commit,
            },
        )
        return {
            "status": "ok",
            "processed": processed,
            "live_trading": False,
            "alpha_logic_touched": False,
            "strategy_b_modified": False,
        }
    except Exception:
        try:
            await supabase_rpc(
                "paper_record_worker_heartbeat",
                {
                    "p_worker_name": worker,
                    "p_run_id": run_id,
                    "p_phase": "COMPLETE",
                    "p_status": "FAILED",
                    "p_claimed_count": 0,
                    "p_processed_count": processed,
                    "p_error_code": "RECOVERY_WORKER_FAILED",
                    "p_software_commit": commit,
                },
            )
        finally:
            raise


@app.post("/api/internal/paper/outbox-tick")
async def internal_paper_outbox_tick(
    x_paper_scheduler_token: str | None = Header(default=None, alias="X-Paper-Scheduler-Token"),
):
    require_durable_production_store()
    _require_internal_secret(x_paper_scheduler_token, "PAPER_SCHEDULER_TOKEN")

    ops_policy = await _paper_active_ops_policy()
    limit = int(ops_policy.get("max_worker_batch") or 0)
    depth = int(ops_policy.get("market_book_depth") or 0)
    lease_seconds = int(os.getenv("PAPER_OUTBOX_LEASE_SECONDS", "0") or 0)
    if limit < 1:
        raise HTTPException(503, "paper_max_worker_batch_not_configured")
    if depth < 1 or depth > 400:
        raise HTTPException(503, "paper_market_book_depth_not_configured")
    if lease_seconds < 1:
        raise HTTPException(503, "paper_outbox_lease_seconds_not_configured")

    run_id = str(uuid4())
    commit = os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown")
    worker = f"paper-outbox-worker:{run_id}"
    claimed = await supabase_rpc(
        "paper_claim_outbox",
        {
            "p_worker_id": worker,
            "p_limit": limit,
            "p_lease_seconds": lease_seconds,
        },
    )
    claimed = claimed or []

    await supabase_rpc(
        "paper_record_worker_heartbeat",
        {
            "p_worker_name": "paper-outbox-worker",
            "p_run_id": run_id,
            "p_phase": "START",
            "p_status": "RUNNING",
            "p_claimed_count": len(claimed),
            "p_processed_count": 0,
            "p_error_code": None,
            "p_software_commit": commit,
        },
    )

    processed = 0
    errors = 0
    spec006_entry_notional = 50.0

    for item in claimed:
        outbox_id = item["id"]
        generation = int(item["lease_generation"])
        event_type = item["event_type"]
        entity_id = item["entity_id"]
        try:
            if event_type == "STRATEGY_ACTION_DISPATCH":
                actions = await supabase_get(
                    "paper_strategy_actions",
                    f"id=eq.{entity_id}&limit=1",
                )
                if not actions:
                    raise ValueError("strategy_action_not_found")
                action = actions[0]

                if action["action_type"] == "ENTRY":
                    binding = await _paper_runtime_binding(action["strategy_version_id"])
                    if not binding or not binding.get("enabled"):
                        # A queued historical ENTRY must never become eligible after a later re-enable.
                        await supabase_rpc(
                            "paper_spec006_fence_entry_lifecycle",
                            {
                                "p_entry_action_id": entity_id,
                                "p_reason": "RUNTIME_BINDING_DISABLED",
                                "p_worker_id": worker,
                                "p_software_commit": commit,
                            },
                        )
                        ack = await _paper_ack(outbox_id, worker, generation, None)
                        if (ack or {}).get("acknowledged"):
                            processed += 1
                        continue

                    await supabase_rpc(
                        "paper_spec006_dispatch_entry_action",
                        {
                            "p_action_id": entity_id,
                            "p_intended_notional": spec006_entry_notional,
                            "p_blind_test_id": "TEST-SPEC-002-forward-2026",
                            "p_worker_id": worker,
                            "p_software_commit": commit,
                        },
                    )
                    ack = await _paper_ack(outbox_id, worker, generation, None)
                    if (ack or {}).get("acknowledged"):
                        processed += 1
                    continue

                if action["action_type"] == "EXIT_TO_FLAT":
                    intents = await supabase_get(
                        "paper_exit_intents",
                        f"exit_action_id=eq.{entity_id}&limit=1",
                    )
                    if not intents:
                        raise ValueError("exit_intent_not_found")
                    result = await supabase_rpc(
                        "paper_spec006_queue_exit_attempt",
                        {
                            "p_exit_intent_id": intents[0]["id"],
                            "p_software_commit": commit,
                        },
                    )
                    reason = (result or {}).get("reason")
                    if reason in ("WAIT_ENTRY_IN_FLIGHT", "WAIT_OTHER_OWNER", "PAUSED_KILL_SWITCH"):
                        await _paper_ack(outbox_id, worker, generation, reason)
                        errors += 1
                    else:
                        ack = await _paper_ack(outbox_id, worker, generation, None)
                        if (ack or {}).get("acknowledged"):
                            processed += 1
                    continue

                raise ValueError("unsupported_strategy_action")

            if event_type == "RISK_EVALUATE":
                signals = await supabase_get("paper_signals", f"id=eq.{entity_id}&limit=1")
                if not signals:
                    raise ValueError("signal_not_found")
                signal = signals[0]
                spec006_lifecycle = await supabase_get(
                    "paper_entry_lifecycles",
                    f"paper_signal_id=eq.{entity_id}&limit=1",
                )
                binding = await _paper_runtime_binding(signal["strategy_version_id"])
                if not binding or not binding.get("enabled"):
                    if spec006_lifecycle:
                        await supabase_rpc(
                            "paper_spec006_fence_entry_lifecycle",
                            {
                                "p_entry_action_id": spec006_lifecycle[0]["entry_action_id"],
                                "p_reason": "RUNTIME_BINDING_DISABLED",
                                "p_worker_id": worker,
                                "p_software_commit": commit,
                            },
                        )
                        ack = await _paper_ack(outbox_id, worker, generation, None)
                        if (ack or {}).get("acknowledged"):
                            processed += 1
                    else:
                        await _paper_ack(outbox_id, worker, generation, "RUNTIME_BINDING_DISABLED")
                        errors += 1
                    continue

                risk = await supabase_rpc(
                    "paper_reserve_risk",
                    {
                        "p_signal_id": entity_id,
                        "p_policy_version_id": binding["risk_policy_version_id"],
                        "p_account_key": "paper-default",
                        "p_worker_id": worker,
                        "p_software_commit": commit,
                    },
                )
                if (risk or {}).get("decision") == "APPROVED":
                    reservations = await supabase_get(
                        "risk_reservations",
                        f"signal_id=eq.{entity_id}&limit=1",
                    )
                    reservation = reservations[0] if reservations else None
                    if reservation and reservation.get("status") in ("RESERVED", "PARTIALLY_CONSUMED"):
                        if spec006_lifecycle:
                            await supabase_rpc(
                                "paper_spec006_create_entry_order_from_approved_signal",
                                {
                                    "p_signal_id": entity_id,
                                    "p_worker_id": worker,
                                    "p_software_commit": commit,
                                },
                            )
                        else:
                            await supabase_rpc(
                                "paper_create_order_from_approved_signal",
                                {
                                    "p_signal_id": entity_id,
                                    "p_side": "BUY" if signal["side"] == "LONG" else "SELL",
                                    "p_intent_type": "ENTRY",
                                    "p_order_type": "MARKET",
                                    "p_worker_id": worker,
                                    "p_software_commit": commit,
                                },
                            )
                ack = await _paper_ack(outbox_id, worker, generation, None)
                if (ack or {}).get("acknowledged"):
                    processed += 1
                continue

            if event_type == "BIND_FILL_ATTEMPT":
                orders = await supabase_get("paper_orders", f"id=eq.{entity_id}&limit=1")
                if not orders:
                    raise ValueError("order_not_found")
                order = orders[0]

                spec006_lifecycle = []
                if not order.get("exit_intent_id"):
                    spec006_lifecycle = await supabase_get(
                        "paper_entry_lifecycles",
                        f"paper_entry_order_id=eq.{entity_id}&limit=1",
                    )
                is_spec006 = bool(order.get("exit_intent_id") or spec006_lifecycle)

                reservation = None
                if order["intent_type"] == "ENTRY":
                    reservations = await supabase_get(
                        "risk_reservations",
                        f"signal_id=eq.{order['signal_id']}&limit=1",
                    )
                    if not reservations:
                        raise ValueError("risk_reservation_not_found")
                    reservation = reservations[0]
                    policy_version_id = reservation["policy_version_id"]
                elif is_spec006 and order["intent_type"] == "EXIT":
                    binding = await _paper_runtime_binding(order["strategy_version_id"])
                    if not binding:
                        raise ValueError("runtime_binding_not_configured")
                    policy_version_id = binding["risk_policy_version_id"]
                else:
                    reservations = await supabase_get(
                        "risk_reservations",
                        f"signal_id=eq.{order['signal_id']}&limit=1",
                    )
                    if not reservations:
                        raise ValueError("risk_reservation_not_found")
                    reservation = reservations[0]
                    policy_version_id = reservation["policy_version_id"]

                policies = await supabase_get(
                    "risk_policy_versions",
                    f"id=eq.{policy_version_id}&limit=1",
                )
                if not policies:
                    raise ValueError("risk_policy_not_found")
                risk_policy = policies[0]
                fee_rate = risk_policy.get("fee_rate")
                if fee_rate is None:
                    if is_spec006 and order["intent_type"] == "EXIT":
                        # Durable flatten intent must survive configuration faults.
                        await _paper_ack(
                            outbox_id,
                            worker,
                            generation,
                            "EXECUTION_POLICY_UNAVAILABLE",
                        )
                        errors += 1
                        continue
                    await supabase_rpc(
                        "paper_terminalize_order",
                        {
                            "p_order_id": entity_id,
                            "p_terminal_state": "FAILED",
                            "p_reason_code": "EXECUTION_FEE_POLICY_MISSING",
                            "p_worker_id": worker,
                            "p_software_commit": commit,
                            "p_account_key": "paper-default",
                        },
                    )
                    await _paper_ack(outbox_id, worker, generation, None)
                    processed += 1
                    continue

                fetched = await _fetch_okx_execution_book(order["pair"], depth)
                snapshot = await supabase_rpc(
                    "paper_ingest_market_snapshot",
                    {
                        "p_pair": fetched["pair"],
                        "p_provider": fetched["provider"],
                        "p_provider_ts_ms": fetched["provider_ts_ms"],
                        "p_received_at": fetched["received_at"],
                        "p_bid_levels": fetched["bids"],
                        "p_ask_levels": fetched["asks"],
                        "p_best_bid": fetched["bids"][0][0],
                        "p_best_ask": fetched["asks"][0][0],
                        "p_snapshot_hash": fetched["snapshot_hash"],
                        "p_software_commit": commit,
                    },
                )
                attempt_seq = int((item.get("payload") or {}).get("attempt_seq") or 1)
                bound = await supabase_rpc(
                    "paper_spec006_bind_execution_attempt" if is_spec006 else "paper_bind_execution_attempt",
                    {
                        "p_outbox_id": outbox_id,
                        "p_lease_generation": generation,
                        "p_order_id": entity_id,
                        "p_market_snapshot_id": snapshot["id"],
                        "p_policy_version_id": policy_version_id,
                        "p_attempt_seq": attempt_seq,
                        "p_worker_id": worker,
                        "p_software_commit": commit,
                    },
                )
                if not (bound or {}).get("execution_attempt_id"):
                    # Lease lost or event became terminal while fetching market data.
                    continue

                bound_snapshot_id = bound.get("market_snapshot_id") or snapshot["id"]
                if bound_snapshot_id != snapshot["id"]:
                    rows = await supabase_get(
                        "execution_market_snapshots",
                        f"id=eq.{bound_snapshot_id}&limit=1",
                    )
                    if not rows:
                        raise ValueError("bound_market_snapshot_missing")
                    use_snapshot = rows[0]
                    bids = use_snapshot["bid_levels"]
                    asks = use_snapshot["ask_levels"]
                    provider_ts_ms = int(use_snapshot["provider_ts_ms"])
                else:
                    bids = fetched["bids"]
                    asks = fetched["asks"]
                    provider_ts_ms = int(fetched["provider_ts_ms"])

                if is_spec006 and order["intent_type"] == "EXIT":
                    remaining_base_qty = (item.get("payload") or {}).get("remaining_base_qty")
                    if remaining_base_qty is None:
                        raise ValueError("spec006_exit_missing_locked_base_qty")
                    fill = walk_canonical_base_quantity(
                        side=order["side"],
                        base_quantity=remaining_base_qty,
                        bids=bids,
                        asks=asks,
                    )
                else:
                    remaining_notional = remaining_quote_notional(
                        order["intended_notional"],
                        order.get("filled_notional") or 0,
                    )
                    if remaining_notional == "0":
                        await _paper_ack(outbox_id, worker, generation, None)
                        processed += 1
                        continue
                    fill = walk_canonical_quote_notional(
                        side=order["side"],
                        quote_notional=remaining_notional,
                        bids=bids,
                        asks=asks,
                    )
                if fill["filled_quote_notional"] == "0":
                    await _paper_ack(outbox_id, worker, generation, "NO_EXECUTABLE_DEPTH")
                    errors += 1
                    continue

                if (
                    not fill["complete"]
                    and not (is_spec006 and order["intent_type"] == "EXIT")
                    and not risk_policy.get("partial_fill_allowed")
                ):
                    await supabase_rpc(
                        "paper_terminalize_order",
                        {
                            "p_order_id": entity_id,
                            "p_terminal_state": "FAILED",
                            "p_reason_code": "INSUFFICIENT_BOOK_DEPTH",
                            "p_worker_id": worker,
                            "p_software_commit": commit,
                            "p_account_key": "paper-default",
                        },
                    )
                    await _paper_ack(outbox_id, worker, generation, None)
                    processed += 1
                    continue

                prior_fills = await supabase_get(
                    "paper_fills",
                    f"order_id=eq.{entity_id}&select=fill_seq&order=fill_seq.desc&limit=1",
                )
                fill_seq = int(prior_fills[0]["fill_seq"]) + 1 if prior_fills else 1
                fee_amount = quote_fee_amount(fill["filled_quote_notional"], fee_rate)
                filled_at = datetime.fromtimestamp(provider_ts_ms / 1000, UTC).isoformat()

                applied = await supabase_rpc(
                    "paper_spec006_apply_fill" if is_spec006 else "paper_apply_fill",
                    {
                        "p_outbox_id": outbox_id,
                        "p_lease_generation": generation,
                        "p_order_id": entity_id,
                        "p_execution_attempt_id": bound["execution_attempt_id"],
                        "p_fill_seq": fill_seq,
                        "p_fill_quantity": fill["filled_base_quantity"],
                        "p_fill_price": fill["vwap"],
                        "p_fee_amount": fee_amount,
                        "p_spread_bps": fill["spread_bps"],
                        "p_impact_bps": fill["impact_bps"],
                        "p_filled_at": filled_at,
                        "p_account_key": "paper-default",
                        "p_worker_id": worker,
                        "p_software_commit": commit,
                    },
                )
                if (applied or {}).get("applied") or (applied or {}).get("idempotent_replay"):
                    ack = await _paper_ack(outbox_id, worker, generation, None)
                    if (ack or {}).get("acknowledged"):
                        processed += 1
                continue

            # Unknown events are released for retry and surfaced as worker errors.
            await _paper_ack(outbox_id, worker, generation, f"UNHANDLED_EVENT:{event_type}")
            errors += 1
        except Exception as exc:
            errors += 1
            try:
                await _paper_ack(
                    outbox_id,
                    worker,
                    generation,
                    f"{type(exc).__name__}:{str(exc)[:120]}",
                )
            except Exception:
                pass

    await supabase_rpc(
        "paper_record_worker_heartbeat",
        {
            "p_worker_name": "paper-outbox-worker",
            "p_run_id": run_id,
            "p_phase": "COMPLETE",
            "p_status": "IDLE" if errors == 0 else "DEGRADED",
            "p_claimed_count": 0,
            "p_processed_count": processed,
            "p_error_code": None if errors == 0 else "OUTBOX_ITEM_ERRORS",
            "p_software_commit": commit,
        },
    )
    return {
        "status": "ok" if errors == 0 else "degraded",
        "claimed": len(claimed),
        "processed": processed,
        "errors": errors,
        "live_trading": False,
        "alpha_logic_touched": False,
        "strategy_b_modified": False,
    }


@app.post("/api/internal/paper/reconcile")
async def internal_paper_reconcile(
    x_paper_scheduler_token: str | None = Header(default=None, alias="X-Paper-Scheduler-Token"),
):
    """Run one bounded reconciliation pass. Server/scheduler only."""
    require_durable_production_store()
    _require_internal_secret(x_paper_scheduler_token, "PAPER_SCHEDULER_TOKEN")
    run_id = str(uuid4())
    commit = os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown")
    await supabase_rpc(
        "paper_record_worker_heartbeat",
        {
            "p_worker_name": "paper-reconciliation-worker",
            "p_run_id": run_id,
            "p_phase": "START",
            "p_status": "RUNNING",
            "p_claimed_count": 0,
            "p_processed_count": 0,
            "p_error_code": None,
            "p_software_commit": commit,
        },
    )
    try:
        result = await supabase_rpc(
            "paper_run_reconciliation",
            {
                "p_account_key": "paper-default",
                "p_worker_id": "paper-reconciliation-worker",
                "p_software_commit": commit,
            },
        )
    except Exception:
        try:
            await supabase_rpc(
                "paper_record_worker_heartbeat",
                {
                    "p_worker_name": "paper-reconciliation-worker",
                    "p_run_id": run_id,
                    "p_phase": "COMPLETE",
                    "p_status": "FAILED",
                    "p_claimed_count": 0,
                    "p_processed_count": 0,
                    "p_error_code": "RECONCILIATION_RPC_FAILED",
                    "p_software_commit": commit,
                },
            )
        finally:
            raise

    await supabase_rpc(
        "paper_record_worker_heartbeat",
        {
            "p_worker_name": "paper-reconciliation-worker",
            "p_run_id": run_id,
            "p_phase": "COMPLETE",
            "p_status": "IDLE" if (result or {}).get("status") in ("PASSED", "WARNING") else "DEGRADED",
            "p_claimed_count": 0,
            "p_processed_count": 1 if (result or {}).get("started") else 0,
            "p_error_code": None if (result or {}).get("status") in ("PASSED", "WARNING") else "RECONCILIATION_CRITICAL",
            "p_software_commit": commit,
        },
    )
    return {
        "status": "ok",
        "reconciliation": result,
        "live_trading": False,
        "alpha_logic_touched": False,
        "strategy_b_modified": False,
    }


@app.get("/api/health")
async def health():
    configured = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    return {"status": "ok", "mode": "supabase" if configured else "demo", "live_trading": False,
            "persistence": persistence_status(), "fee_schedule": FEE_SCHEDULE,
            "fee_taker": FEE_TAKER, "fee_maker": FEE_MAKER, "default_role": "taker"}


async def load_pair_cost_model(pair: str, notional: float) -> dict:
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.get(OKX_BOOKS_URL, params={"instId": pair.replace("/", "-"), "sz": 400})
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("incomplete_order_book")
    book = payload["data"][0]
    age = int(datetime.now(UTC).timestamp() * 1000) - int(book.get("ts", 0))
    if not -5000 <= age <= 30_000:
        raise ValueError("stale_order_book")
    return {"pair": pair, **book_costs(book, notional)}


@app.get("/api/market/cost-model")
async def inspect_cost_model(pairs: str = "UNI/USDT,ZEC/USDT", notional: float = 50):
    """Read current books only. No job, candles, strategy update or orders."""
    names = list(dict.fromkeys(pairs.split(",")))
    if not 1 <= len(names) <= 8 or not 5 <= notional <= 10000 or any(not re.fullmatch(r"[A-Z0-9]+/USDT", p) for p in names):
        raise HTTPException(422, "Neplatný pár alebo objem.")
    profiles, errors = {}, []
    for pair in names:
        try:
            profiles[pair] = await load_pair_cost_model(pair, notional)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            errors.append({"pair": pair, "reason": str(error)[:160]})
    return {"status": "degraded_no_pick" if errors else "ok", "source": "okx", "fee_schedule": FEE_SCHEDULE,
            "profiles": profiles, "errors": errors, "historical_l2": False}


@app.get("/api/market/scan")
async def market_scan(quote: str = "USDT", limit: int = 12):
    """Rank public OKX spot markets. Read-only and does not require credentials."""
    quote = quote.upper()
    if quote not in {"USDT", "USDC", "EUR"}:
        raise HTTPException(422, "Podporované meny sú USDT, USDC a EUR.")
    if not 3 <= limit <= 30:
        raise HTTPException(422, "Limit musí byť 3 až 30 trhov.")
    try:
        async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
            response = await client.get(OKX_TICKERS_URL, params={"instType": "SPOT"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, f"OKX skener nie je dostupný: {error}")
    if payload.get("code") != "0":
        raise HTTPException(502, payload.get("msg") or "OKX vrátil neplatnú odpoveď.")

    markets = []
    excluded_bases = {"USDT", "USDC", "USD", "EUR", "DAI", "FDUSD"}
    for ticker in payload.get("data", []):
        instrument = str(ticker.get("instId", ""))
        if not instrument.endswith(f"-{quote}"):
            continue
        base = instrument.removesuffix(f"-{quote}")
        if base in excluded_bases:
            continue
        try:
            last = float(ticker.get("last") or 0)
            bid = float(ticker.get("bidPx") or 0)
            ask = float(ticker.get("askPx") or 0)
            open_24h = float(ticker.get("open24h") or 0)
            high_24h = float(ticker.get("high24h") or 0)
            low_24h = float(ticker.get("low24h") or 0)
            quote_volume = float(ticker.get("volCcy24h") or 0)
        except (TypeError, ValueError):
            continue
        if min(last, bid, ask, open_24h) <= 0 or quote_volume <= 0:
            continue
        spread = max(0.0, (ask - bid) / ((ask + bid) / 2) * 100)
        volatility = max(0.0, (high_24h - low_24h) / open_24h * 100)
        change = (last / open_24h - 1) * 100
        # Prefer liquid, tight markets with enough movement to cover costs.
        liquidity_points = min(55.0, max(0.0, (math.log10(quote_volume) - 4) * 13.75))
        spread_points = max(0.0, 25.0 - spread * 125)
        volatility_points = max(0.0, 20.0 - abs(volatility - 5.0) * 2.5)
        score = round(liquidity_points + spread_points + volatility_points, 1)
        markets.append({
            "pair": f"{base}/{quote}", "instrument": instrument, "last": last,
            "change_24h_percent": round(change, 2), "volume_24h": round(quote_volume, 2),
            "spread_percent": round(spread, 4), "volatility_24h_percent": round(volatility, 2),
            "score": score,
        })
    markets.sort(key=lambda market: (market["score"], market["volume_24h"]), reverse=True)
    return {
        "source": "OKX public spot API", "quote": quote, "scanned": len(markets),
        "generated_at": datetime.now(UTC).isoformat(), "markets": markets[:limit],
        "live_trading": False, "method": "likvidita 55 % · spread 25 % · volatilita 20 %",
    }


@app.get("/api/market/candles")
async def market_candles(pair: str, timeframe: str = "3m", limit: int = 480):
    """Read public OKX spot candles. This endpoint cannot place orders."""
    instrument = pair.replace("/", "-").upper()
    if not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", instrument):
        raise HTTPException(422, "Neplatný obchodný pár.")
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(422, "Nepodporovaný interval sviečok.")
    if not 10 <= limit <= 1500:
        raise HTTPException(422, "Počet sviečok musí byť 10 až 1500.")
    try:
        minutes = TIMEFRAME_MILLISECONDS[timeframe] // 60_000
        end_time = int(datetime.now(UTC).timestamp() * 1000)
        start_time = end_time - (limit * minutes * 60 * 1000)
        candles = await load_okx_candles(pair, timeframe, limit, start_time, end_time)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Dáta z OKX nie sú dostupné: {error}")
    return {"source": "OKX public spot API", "pair": pair.upper(), "timeframe": timeframe, "candles": candles, "live_trading": False}


async def load_okx_candles(pair: str, timeframe: str, limit: int, start_time: int | None = None, end_time: int | None = None, progress_callback: Any | None = None, cache_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Load complete OKX history without exceeding the public rate limit."""
    global okx_history_last_request
    instrument = pair.replace("/", "-").upper()
    step = TIMEFRAME_MILLISECONDS[timeframe]
    end = end_time or int(datetime.now(UTC).timestamp() * 1000)
    start = start_time if start_time is not None else end - limit * step
    cache_key = (instrument, timeframe, int(limit), int(start), int(end))
    cached = okx_candle_cache.get(cache_key)
    now = asyncio.get_running_loop().time()
    if cached and now - cached[0] < OKX_CANDLE_CACHE_SECONDS:
        if cache_info is not None:
            cache_info.update({"key": f"okx:{instrument}:{timeframe}:{start}:{end}", "cached": True, "retries_429": 0})
        return cached[1]
    if cache_info is not None:
        cache_info.update({"key": f"okx:{instrument}:{timeframe}:{start}:{end}", "cached": False, "retries_429": 0})
    page_size = 100
    expected = min(limit, max(1, (end - start) // step + 1))
    batches: list[list[list[str]]] = []
    async with okx_history_semaphore:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            async def request_page(url: str, params: dict[str, Any]) -> list[list[str]]:
                nonlocal client
                global okx_history_last_request
                response = None
                for attempt in range(5):
                    # Pace request starts globally, but do not hold the rate lock
                    # while the network request is in flight. This keeps the
                    # conservative ~6.7 req/s start rate while allowing bounded
                    # overlap between slow OKX responses.
                    async with okx_history_rate_lock:
                        elapsed = asyncio.get_running_loop().time() - okx_history_last_request
                        if elapsed < OKX_HISTORY_MIN_INTERVAL:
                            await asyncio.sleep(OKX_HISTORY_MIN_INTERVAL - elapsed)
                        okx_history_last_request = asyncio.get_running_loop().time()
                    response = await client.get(url, params=params)
                    if response.status_code != 429:
                        break
                    if cache_info is not None:
                        cache_info["retries_429"] = int(cache_info.get("retries_429", 0)) + 1
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(8.0, float(2 ** attempt))
                    await asyncio.sleep(delay)
                assert response is not None
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != "0":
                    raise httpx.HTTPStatusError(payload.get("msg") or "OKX candle error", request=response.request, response=response)
                return payload.get("data", [])

            # For a window ending well in the past, anchor history directly at
            # the requested end. Starting from the live candles endpoint would
            # waste pages walking back from "now" and can truncate old windows.
            wall_clock_ms = int(datetime.now(UTC).timestamp() * 1000)
            historical_only = end < wall_clock_ms - 2 * step
            if historical_only:
                cursor = end + step
                if cache_info is not None:
                    cache_info["historical_anchor"] = True
            else:
                # The current-candles endpoint reduces the number of historical pages
                # for windows that reach the present.
                recent = await request_page(OKX_CANDLES_URL, {"instId": instrument, "bar": timeframe, "limit": min(300, int(expected))})
                batches.append(recent)
                cursor = min((int(row[0]) for row in recent), default=end + step)
                if cache_info is not None:
                    cache_info["historical_anchor"] = False
            history_pages = max(0, min(450, math.ceil(max(0, cursor - start) / step / page_size)))
            for index in range(history_pages):
                if progress_callback:
                    progress_callback(index + 1, history_pages)
                batch = await request_page(OKX_HISTORY_CANDLES_URL, {"instId": instrument, "bar": timeframe, "after": cursor, "limit": page_size})
                if not batch:
                    break
                batches.append(batch)
                next_cursor = min(int(row[0]) for row in batch)
                if next_cursor >= cursor:
                    break
                cursor = next_cursor
                if cursor <= start:
                    break
    unique: dict[int, list[str]] = {}
    for batch in batches:
        for row in batch:
            timestamp = int(row[0])
            if start <= timestamp <= end:
                unique[timestamp] = row
    rows = [unique[key] for key in sorted(unique)][-limit:]
    result = [{"open_time": int(row[0]), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "quote_volume": float(row[7] or 0), "close_time": int(row[0]) + step - 1} for row in rows]
    okx_candle_cache[cache_key] = (asyncio.get_running_loop().time(), result)
    if len(okx_candle_cache) > 64:
        oldest = min(okx_candle_cache, key=lambda key: okx_candle_cache[key][0])
        okx_candle_cache.pop(oldest, None)
    return result


def percentile_rank(values: list[float], value: float, reverse: bool = False) -> float:
    if len(values) < 2:
        return 1.0
    rank = sum(1 for item in values if item <= value) / len(values)
    return 1 - rank + 1 / len(values) if reverse else rank


def pearson(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size < 12:
        return 0.0
    left, right = left[-size:], right[-size:]
    left_mean, right_mean = sum(left) / size, sum(right) / size
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
    return numerator / denominator if denominator else 0.0


def candle_metrics(candles: list[dict[str, Any]], request: UniversePickRequest) -> dict[str, Any]:
    closes = [float(item["close"]) for item in candles]
    if len(closes) < 30:
        raise ValueError("insufficient_candles")
    true_ranges = []
    for index in range(1, len(candles)):
        item, previous = candles[index], candles[index - 1]
        true_ranges.append(max(float(item["high"]) - float(item["low"]), abs(float(item["high"]) - float(previous["close"])), abs(float(item["low"]) - float(previous["close"]))))
    atr_ratio = sum(true_ranges[-14:]) / min(14, len(true_ranges)) / closes[-1]
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    trend_strength = abs(closes[-1] - closes[0]) / path if path else 0.0
    crosses = 0
    previous_side = None
    for index in range(19, len(closes)):
        mid = sum(closes[index - 19:index + 1]) / 20
        side = closes[index] >= mid
        if previous_side is not None and side != previous_side:
            crosses += 1
        previous_side = side
    volumes = [float(item.get("quote_volume") or float(item["volume"]) * float(item["close"])) for item in candles]
    volume_stability = min(1.0, median(volumes) / (sum(volumes) / len(volumes))) if volumes and sum(volumes) else 0.0
    cutoff = int(candles[-1]["open_time"]) - request.correlation_lookback_hours * 3_600_000
    step = TIMEFRAME_MILLISECONDS[request.timeframe]
    returns_by_time = {str(candles[index]["open_time"]): math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))
                       if int(candles[index]["open_time"]) >= cutoff and int(candles[index]["open_time"]) - int(candles[index - 1]["open_time"]) == step
                       and closes[index - 1] > 0 and closes[index] > 0}
    return {"atr_ratio": atr_ratio, "trend_strength": trend_strength, "bb_mid_crosses": crosses, "volume_stability": volume_stability, "returns_by_time": returns_by_time}


def return_correlation(left: dict, right: dict) -> float:
    common = sorted(set(left) & set(right))
    if len(common) < 20:
        raise ValueError("insufficient_aligned_returns")
    return abs(pearson([left[t] for t in common], [right[t] for t in common]))


@app.post("/api/market/universe/pick")
async def pick_market_universe(request: UniversePickRequest):
    """Propose, but never silently apply, a diversified OKX spot universe."""
    if request.quote_ccy.upper() != "USDT":
        raise HTTPException(422, "Automatický návrh zatiaľ podporuje iba USDT.")
    now = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            instrument_response, ticker_response = await asyncio.gather(
                client.get(OKX_INSTRUMENTS_URL, params={"instType": "SPOT"}),
                client.get(OKX_TICKERS_URL, params={"instType": "SPOT"}),
            )
            instrument_response.raise_for_status(); ticker_response.raise_for_status()
            instrument_payload, ticker_payload = instrument_response.json(), ticker_response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, f"OKX universe nie je dostupný: {error}")
    if instrument_payload.get("code") != "0" or ticker_payload.get("code") != "0":
        raise HTTPException(502, "OKX vrátil neplatné údaje o trhoch.")

    instruments = {item.get("instId"): item for item in instrument_payload.get("data", [])}
    rejected: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for ticker in ticker_payload.get("data", []):
        inst_id = str(ticker.get("instId", ""))
        instrument = instruments.get(inst_id, {})
        if not inst_id.endswith("-USDT") or instrument.get("state") != "live":
            continue
        base = str(instrument.get("baseCcy") or inst_id.split("-")[0])
        reasons = []
        if re.search(r"(?:3|5)[LS]$", base): reasons.append("leveraged_token")
        try:
            listed_at = datetime.fromtimestamp(int(instrument.get("listTime") or 0) / 1000, UTC)
            age_days = (now - listed_at).days
            last, bid, ask, opened = (float(ticker.get(key) or 0) for key in ("last", "bidPx", "askPx", "open24h"))
            volume = float(ticker.get("volCcy24h") or 0)
            timestamp = int(ticker.get("ts") or 0)
            spread = (ask - bid) / ((ask + bid) / 2) if bid > 0 and ask > 0 else 1.0
            change = last / opened - 1 if opened > 0 else 9.0
        except (TypeError, ValueError, OverflowError):
            reasons.append("invalid_ticker"); age_days = 0; last = bid = ask = volume = timestamp = 0; spread = 1; change = 9
        if age_days < request.min_listing_age_days: reasons.append("listing_too_new")
        if volume < request.min_quote_volume_24h: reasons.append("low_volume")
        if spread > request.max_spread_ratio: reasons.append("wide_spread")
        if abs(change) > request.max_abs_change_24h_ratio: reasons.append("explosive_move")
        if timestamp <= 0 or (now.timestamp() * 1000 - timestamp) > request.max_data_age_seconds * 1000: reasons.append("stale_ticker")
        if last <= 0 or last and request.trade_notional_usdt / last < float(instrument.get("minSz") or 0): reasons.append("minimum_size")
        row = {"pair": inst_id.replace("-", "/"), "instrument_id": inst_id, "base_ccy": base, "last": last, "volume_24h": volume, "spread_ratio": spread, "change_24h_ratio": change, "listing_age_days": age_days, "min_size": instrument.get("minSz"), "tick_size": instrument.get("tickSz")}
        if reasons:
            rejected.append({**row, "reasons": reasons})
        else:
            candidates.append(row)

    candidates.sort(key=lambda item: item["volume_24h"], reverse=True)
    # Books and candles are expensive and rate-limited. The liquid top 12 form
    # the actual detailed candidate pool; no missing member may be ignored.
    candidate_pool = candidates[:12]
    candle_count = math.ceil(request.lookback_hours * 3_600_000 / TIMEFRAME_MILLISECONDS[request.timeframe]) + 2
    end_time = int(now.timestamp() * 1000)
    start_time = end_time - request.lookback_hours * 3_600_000

    async def enrich(row: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            book_response = await client.get(OKX_BOOKS_URL, params={"instId": row["instrument_id"], "sz": 400})
            book_response.raise_for_status()
            book_payload = book_response.json()
        candles = await load_okx_candles(row["pair"], request.timeframe, candle_count, start_time, end_time)
        if book_payload.get("code") != "0" or not book_payload.get("data") or len(candles) < candle_count * .9:
            raise ValueError("incomplete_candidate_data")
        book_age = int(datetime.now(UTC).timestamp() * 1000) - int(book_payload["data"][0].get("ts", 0))
        if not -5000 <= book_age <= request.max_data_age_seconds * 1000:
            raise ValueError("stale_order_book")
        cost = book_costs(book_payload["data"][0], request.trade_notional_usdt)
        return {**row, "book_impact_ratio": cost["book_impact_ratio"], "thin_L1": cost["thin_L1"],
                "cost_components": cost, **candle_metrics(candles, request)}

    async def enrich_with_retry(row: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await enrich(row)
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt == 0:
                    await asyncio.sleep(.8)
        raise last_error or ValueError("incomplete_candidate_data")

    detailed: list[dict[str, Any]] = []
    data_errors: list[str] = []
    results = await asyncio.gather(*(enrich_with_retry(row) for row in candidate_pool), return_exceptions=True)
    for row, result in zip(candidate_pool, results):
        if isinstance(result, Exception):
            data_errors.append(row["pair"])
        elif result["book_impact_ratio"] > request.max_book_impact_ratio:
            rejected.append({**row, "reasons": ["book_impact"]})
        elif not request.min_atr_ratio <= result["atr_ratio"] <= request.max_atr_ratio:
            rejected.append({**row, "reasons": ["atr_outside_range"]})
        else:
            detailed.append(result)

    proposal_id = str(uuid4())
    base_response = {"proposal_id": proposal_id, "generated_at": now.isoformat(), "source": "OKX public spot API", "status": "ok", "picks": [], "rejected": rejected, "data_quality": {"candidate_pool": len(candidate_pool), "complete_candidates": len(detailed), "incomplete_pairs": data_errors}, "lock": {"hours": request.lock_hours, "expires_at": (now + timedelta(hours=request.lock_hours)).isoformat()}, "method": {"weights": {"volume": .30, "spread": .25, "range": .20, "atr_fit": .15, "bb_crosses": .10}, "max_correlation": request.max_pairwise_correlation}}
    if data_errors or len(detailed) < request.max_picks:
        return {**base_response, "status": "degraded_no_pick" if data_errors else "no_pick", "message": "Návrh sa nevytvoril, pretože údaje kandidátov nie sú úplné." if data_errors else "Tvrdé filtre prešlo príliš málo trhov."}

    volumes = [item["volume_24h"] for item in detailed]; spreads = [item["spread_ratio"] for item in detailed]
    crosses = [item["bb_mid_crosses"] for item in detailed]; ranges = [1 - item["trend_strength"] for item in detailed]
    atr_mid = (request.min_atr_ratio + request.max_atr_ratio) / 2
    for item in detailed:
        atr_fit = max(0.0, 1 - abs(item["atr_ratio"] - atr_mid) / max(atr_mid - request.min_atr_ratio, .000001))
        raw = .30 * percentile_rank(volumes, item["volume_24h"]) + .25 * percentile_rank(spreads, item["spread_ratio"], True) + .20 * percentile_rank(ranges, 1 - item["trend_strength"]) + .15 * atr_fit + .10 * percentile_rank(crosses, item["bb_mid_crosses"])
        item["score"] = round(raw * 100, 1)
    detailed.sort(key=lambda item: item["score"], reverse=True)
    picks: list[dict[str, Any]] = []
    remaining = list(detailed)
    while remaining and len(picks) < request.max_picks:
        scored = []
        for item in remaining:
            try:
                correlation = max((return_correlation(item["returns_by_time"], picked["returns_by_time"]) for picked in picks), default=0.)
            except ValueError:
                return {**base_response, "status": "degraded_no_pick", "message": "Korelácie nemajú dostatok časovo zhodných dát.", "picks": []}
            if correlation > request.max_pairwise_correlation:
                rejected.append({"pair": item["pair"], "reasons": ["correlation"], "max_correlation": round(correlation, 3)})
                continue
            scored.append({**item, "correlation_penalty": round(10 * correlation, 4),
                           "adjusted_score": item["score"] - 10 * correlation})
        if not scored:
            break
        chosen = max(scored, key=lambda item: item["adjusted_score"])
        picks.append(chosen)
        remaining = [item for item in scored if item["pair"] != chosen["pair"]]
    base_response["method"]["correlation_penalty_points"] = 10
    public_picks = [{key: value for key, value in item.items() if key != "returns_by_time"} for item in picks]
    return {**base_response, "status": "ok" if len(public_picks) >= 3 else "no_pick", "message": "Návrh je pripravený na potvrdenie." if len(public_picks) >= 3 else "Po korelačnom filtri ostali menej než tri trhy.", "picks": public_picks}


@app.post("/api/simulations/run")
async def run_standalone_simulation(request: SimulationRequest):
    """Run a complete backtest from public OKX candles without Freqtrade."""
    settings = request.settings.model_dump()
    pairs = settings["selected_pairs"]
    if not pairs:
        raise HTTPException(422, "Vyber aspoň jeden coin.")
    if request.start_time and request.end_time and request.start_time >= request.end_time:
        raise HTTPException(422, "Začiatok testu musí byť pred koncom testu.")
    try:
        candle_sets = await asyncio.gather(*(load_okx_candles(pair, settings["timeframe"], request.candle_limit, request.start_time, request.end_time) for pair in pairs))
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Simuláciu nebolo možné načítať z OKX: {error}")
    expected_count = request.candle_limit
    if request.start_time is not None and request.end_time is not None:
        expected_count = min(request.candle_limit, max(1, (request.end_time - request.start_time + TIMEFRAME_MILLISECONDS[settings["timeframe"]] - 1) // TIMEFRAME_MILLISECONDS[settings["timeframe"]]))
    data_coverage = {}
    for pair, candles in zip(pairs, candle_sets):
        data_coverage[pair] = {
            "candles": len(candles),
            "expected_candles": expected_count,
            "complete": len(candles) >= max(1, expected_count - 1),
            "first_open_time": candles[0]["open_time"] if candles else None,
            "last_close_time": candles[-1]["close_time"] if candles else None,
        }
    result = simulate(dict(zip(pairs, candle_sets)), settings, force_close_at_end=request.force_close_at_end)
    if result["equity_curve"] and request.start_time:
        start_iso = datetime.fromtimestamp(request.start_time / 1000, UTC).isoformat()
        result["equity_curve"][0]["time"] = start_iso
        for curve in result["per_pair_equity_curves"].values():
            if curve:
                curve[0]["time"] = start_iso
    if result["trades"]:
        await supabase_upsert_many("simulated_trades", result["trades"])
    now = datetime.now(UTC).isoformat()
    record = {"id": str(uuid4()), "status": "completed", "started_at": now, "finished_at": now, "test_start": request.start_time, "test_end": request.end_time, "timeframe": settings["timeframe"], "pairs": pairs, "summary": result["metrics"], "progress": {"equity_curve": result["equity_curve"], "per_pair_metrics": result["per_pair_metrics"], "per_pair_equity_curves": result["per_pair_equity_curves"], "data_coverage": data_coverage, "rejections": result["rejections"]}}
    await supabase_upsert("simulation_runs", record)
    return {"source": "OKX public spot API", "timeframe": settings["timeframe"], "mode": "simulation", "live_trading": False, "data_coverage": data_coverage, "run": record, **result}


def universe_version(versions: list[dict]) -> dict | None:
    # A newer universe supersedes an older one, even after its lock expires.
    return next((version for version in versions if (version.get("settings") or {}).get("_universe", {}).get("pairs")), None)


def active_universe(version: dict | None) -> dict | None:
    metadata = ((version or {}).get("settings") or {}).get("_universe") or {}
    expires_at = (metadata.get("lock") or {}).get("expires_at")
    try:
        active = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")) > datetime.now(UTC)
    except (TypeError, ValueError):
        active = False
    if not active or not metadata.get("pairs"):
        return None
    return {"version_id": version.get("id"), "pairs": list(metadata["pairs"]), "expires_at": expires_at}


async def strategy_context() -> dict:
    versions = await supabase_get("strategy_versions", "order=created_at.desc&limit=50")
    settings = await supabase_get("strategy_settings", "order=updated_at.desc&limit=1")
    version = universe_version(versions)
    saved = settings[0] if settings else {}
    strategy = saved.get("settings") or DEFAULT.model_dump()
    lock = active_universe(version)
    if version:
        snapshot = version["settings"]
        pairs = snapshot["_universe"]["pairs"]
        # The version is the authoritative activation record. A stale settings
        # row must never overwrite it; newer parameter edits may still apply.
        if (not saved or saved.get("updated_at", "") < version.get("created_at", "")
                or (lock and set(strategy.get("selected_pairs", [])) != set(pairs))):
            strategy = {**snapshot, "selected_pairs": list(pairs)}
        if lock:
            strategy = {**strategy, "selected_pairs": list(pairs)}
    return {"strategy": StrategySettings(**strategy).model_dump(), "active_universe": lock, "persistence": persistence_status()}


@app.get("/api/strategy-context")
async def get_strategy_context():
    return await strategy_context()


async def run_optimizer_job(job_id: str, request: OptimizerRequest) -> None:
    job = optimizer_jobs[job_id]
    try:
        job_pairs = list(request.pairs)
        versions = await supabase_get("strategy_versions", "order=created_at.desc&limit=50")
        lock = active_universe(universe_version(versions))
        if not lock:
            raise ValueError("Chýba aktívny zamknutý zoznam coinov. Najprv použi návrh z OKX.")
        if request.version_id is not None and request.version_id != lock["version_id"]:
            raise ValueError("Zamknutá verzia sa zmenila. Obnov stránku pred ďalšou optimalizáciou.")
        locked_pairs = lock["pairs"]
        if any(pair not in locked_pairs for pair in job_pairs):
            raise ValueError("Karty nie sú locknutý universe. Požadovaný pár nie je súčasťou zamknutej verzie.")
        require_durable_production_store()
        job.update({"version_id": lock["version_id"], "locked_pairs": list(locked_pairs), "pairs": job_pairs,
                    "fee_schedule": FEE_SCHEDULE})
        cost_models, cost_errors = {}, []
        for pair in job_pairs:
            try:
                cost_models[pair] = await load_pair_cost_model(pair, float(request.settings.stake_amount))
            except (httpx.HTTPError, ValueError, KeyError) as error:
                cost_errors.append({"pair": pair, "timeframe": "book", "reason": f"cost_model_unavailable: {error}"})
        job_timeframes = list(dict.fromkeys(tf for tf in request.timeframes if tf in {"15m", "5m"}))
        if not job_timeframes:
            raise ValueError("Optimalizácia podporuje iba intervaly 15m a 5m.")
        # Stable candle boundary makes an immediate repeat use the same cache key.
        smallest_step = min(TIMEFRAME_MILLISECONDS[item] for item in job_timeframes)
        end_time = int(datetime.now(UTC).timestamp() * 1000) // smallest_step * smallest_step - 1
        start_time = end_time - request.history_days * 86_400_000
        candle_sets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        timeframe_order = {"15m": 0, "5m": 1, "3m": 2, "1m": 3}
        markets = sorted(((pair, timeframe) for pair in job_pairs if pair in cost_models for timeframe in job_timeframes), key=lambda item: (timeframe_order.get(item[1], 9), item[0]))
        download_errors: list[dict[str, str]] = list(cost_errors)
        coverage_by_pair: dict[str, dict[str, Any]] = {}
        download_semaphore = asyncio.Semaphore(OKX_HISTORY_CONCURRENCY)
        completed_downloads = 0

        async def fetch_optimizer_market(pair: str, timeframe: str) -> tuple[str, str, list[dict[str, Any]] | None, dict[str, Any], str | None]:
            nonlocal completed_downloads
            count = min(45000, max(100, request.history_days * 86_400_000 // TIMEFRAME_MILLISECONDS[timeframe]))
            cache_state: dict[str, Any] = {}

            def download_progress(page: int, total: int, current_pair: str = pair, current_timeframe: str = timeframe) -> None:
                job.update({
                    "phase": "fetching_candles",
                    "bar": current_timeframe,
                    "inst_id": current_pair.replace("/", "-"),
                    "page": page,
                    "pages_est": total,
                    "message": f"Sťahujem {current_pair} · {current_timeframe}: {page}/{total} strán",
                    "cached": bool(cache_state.get("cached")),
                    "retries_429": int(cache_state.get("retries_429", 0)),
                })

            candles: list[dict[str, Any]] | None = None
            error_reason: str | None = None
            coverage_row: dict[str, Any]
            try:
                async with download_semaphore:
                    job.update({
                        "phase": "fetching_candles",
                        "bar": timeframe,
                        "inst_id": pair.replace("/", "-"),
                        "message": f"Načítavam {pair} · {timeframe}",
                        "cached": False,
                        "retries_429": 0,
                    })
                    candles = await load_okx_candles(
                        pair, timeframe, int(count), start_time, end_time, download_progress, cache_state
                    )
                coverage = len(candles) / max(1, int(count))
                coverage_row = {
                    "candles": len(candles),
                    "expected": int(count),
                    "coverage": round(coverage, 4),
                    "cached": bool(cache_state.get("cached")),
                    "retries_429": int(cache_state.get("retries_429", 0)),
                }
                if len(candles) < 100 or coverage < .95:
                    error_reason = f"neúplné dáta ({coverage:.1%})"
                    candles = None
            except Exception as error:
                coverage_row = {
                    "candles": 0,
                    "expected": int(count),
                    "coverage": 0,
                    "cached": bool(cache_state.get("cached")),
                    "retries_429": int(cache_state.get("retries_429", 0)),
                }
                error_reason = str(error)[:180]
                candles = None
            finally:
                completed_downloads += 1
                job["progress"] = round(completed_downloads / max(1, len(markets)) * 30)

            return pair, timeframe, candles, coverage_row, error_reason

        market_results = await asyncio.gather(
            *(fetch_optimizer_market(pair, timeframe) for pair, timeframe in markets)
        )
        for pair, timeframe, candles, coverage_row, error_reason in market_results:
            coverage_by_pair.setdefault(pair, {})[timeframe] = coverage_row
            if candles is not None:
                candle_sets[(pair, timeframe)] = candles
            if error_reason is not None:
                download_errors.append({"pair": pair, "timeframe": timeframe, "reason": error_reason})
        required_timeframes = set(job_timeframes)
        pairs_ready = [pair for pair in job_pairs if required_timeframes.issubset({timeframe for candidate_pair, timeframe in candle_sets if candidate_pair == pair})]
        pairs_dropped = []
        for pair in job_pairs:
            if pair not in pairs_ready:
                reasons = [item["reason"] for item in download_errors if item["pair"] == pair]
                pairs_dropped.append({"inst_id": pair.replace("/", "-"), "reason": reasons[0] if reasons else "data_incomplete"})
        candle_sets = {key: value for key, value in candle_sets.items() if key[0] in pairs_ready}
        job.update({"phase": "scoring", "pairs_ready": [pair.replace("/", "-") for pair in pairs_ready], "pairs_dropped": pairs_dropped})
        if not pairs_ready:
            raise ValueError("Pre optimalizáciu sa nepodarilo načítať aspoň 95 % sviečok pre žiadny trh.")

        loop = asyncio.get_running_loop()
        def update_progress(done: int, total: int, market: str) -> None:
            loop.call_soon_threadsafe(job.update, {"phase": "testing", "message": f"Testujem {market}", "progress": 30 + round(done / total * 68), "tested": done, "total": total})
        result = await asyncio.to_thread(optimize, candle_sets, request.settings.model_dump(), request.trials_per_market, update_progress, locked_pairs=locked_pairs, cost_models=cost_models)
        result.update({"source": "okx", "source_job_id": job_id, "version_id": lock["version_id"], "pairs_ready": [pair.replace("/", "-") for pair in pairs_ready], "pairs_dropped": pairs_dropped, "candle_coverage": coverage_by_pair, "data_errors": download_errors})
        now = datetime.now(UTC).isoformat()
        record = {"id": job_id, "status": "completed", "created_at": job["created_at"], "finished_at": now, "request": request.model_dump(), "result": result}
        await supabase_upsert("optimizer_runs", record)
        job.update({"status": "completed", "phase": "completed", "progress": 100, "message": result["job_verdict"], "result": result, "finished_at": now})
    except Exception as error:
        job.update({"status": "failed", "phase": "failed", "message": str(error), "finished_at": datetime.now(UTC).isoformat()})


@app.post("/api/optimizer/run", status_code=202)
async def start_optimizer(request: OptimizerRequest):
    job_id = str(uuid4())
    optimizer_jobs[job_id] = {"id": job_id, "status": "running", "phase": "queued", "progress": 0, "message": "Agent čaká na spustenie", "created_at": datetime.now(UTC).isoformat()}
    # A Vercel function can be frozen as soon as its HTTP response is sent, so
    # its optimization must finish inside the request.  Local Docker keeps the
    # responsive background-job behavior.
    if os.getenv("VERCEL"):
        await run_optimizer_job(job_id, request)
    else:
        asyncio.create_task(run_optimizer_job(job_id, request))
    return optimizer_jobs[job_id]


@app.get("/api/optimizer/validation-replay")
async def validation_replay_availability():
    """Read-only preflight for the user's two historical rows, never starts a job."""
    records = await supabase_get("optimizer_runs", "order=finished_at.desc&limit=100")
    originals = [r for r in records if any(matches_target(row) for row in r.get("result", {}).get("variant_results", []))]
    try:
        prepared = prepare_replay(originals)
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "replay_unavailable", "reason": str(error), "source_job_ids": [r["id"] for r in originals],
                "qualified": False, "verdict": "NEPREŠIEL"}
    return {"status": "ready", "source_job_ids": sorted({r["id"] for r, _, _ in prepared}),
            "qualified": False, "verdict": "NEPREŠIEL"}


@app.post("/api/optimizer/validation-replay")
async def replay_original_validation(request: ValidationReplayRequest):
    """Use archived snapshots only. Preserve original lock, parameters and verdict."""
    key = tuple(sorted(set(request.source_job_ids)))
    async with validation_replay_lock:
        if key in validation_replay_attempts:
            return validation_replay_attempts[key]
        records = []
        for job_id in key:
            stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=100")
            # LocalStore query support is limited, so enforce exact identity here.
            record = next((row for row in stored if row.get("id") == job_id), None)
            if record is None:
                raise HTTPException(404, "Pôvodný záznam behu nie je dostupný. Replay sa nespustil.")
            records.append(record)
        for record in records:
            previous = record.get("result", {}).get("validation_replay")
            if previous and tuple(previous.get("source_job_ids", [])) == key:
                return previous
        try:
            prepare_replay(records)
        except (ValueError, KeyError, TypeError) as error:
            return {"status": "replay_unavailable", "reason": str(error), "trades": [], "qualified": False, "verdict": "NEPREŠIEL"}
        # Mark the attempt before computation. A failed/mismatched attempt is not
        # automatically repeated. This endpoint never calls load_okx_candles.
        validation_replay_attempts[key] = {"status": "attempt_failed", "trades": [], "qualified": False, "verdict": "NEPREŠIEL"}
        outcome = await asyncio.to_thread(replay_validation, records)
        outcome["source_job_ids"] = list(key)
        validation_replay_attempts[key] = outcome
        original = records[0]
        await supabase_upsert("optimizer_runs", {**original, "result": {**original["result"], "validation_replay": outcome}})
        return outcome


def buy_hold_risk_metrics(candles: list[dict[str, Any]], cost_profile: dict[str, Any], capital: float = 100.0) -> dict[str, Any]:
    """Same-close benchmark using the stored cost model and mark-to-market liquidation value."""
    if len(candles) < 2:
        return {"return_percent": 0.0, "max_drawdown_percent": 0.0, "start_close": None, "end_close": None, "bars": len(candles)}
    first = float(candles[0]["close"])
    peak = capital
    max_drawdown = 0.0
    last_equity = capital
    for candle in candles:
        ratio = float(candle["close"]) / first
        equity = capital * (1 + net_return(ratio, cost_profile))
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        last_equity = equity
    return {
        "return_percent": round((last_equity / capital - 1) * 100, 4),
        "max_drawdown_percent": round(max_drawdown, 4),
        "start_close": first,
        "end_close": float(candles[-1]["close"]),
        "bars": len(candles),
    }


def _same_numeric_metrics(expected: dict[str, Any], actual: dict[str, Any], tolerance: float = 1e-7) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        if value is None:
            if actual[key] is not None:
                return False
        elif isinstance(value, (int, float)) and isinstance(actual[key], (int, float)):
            if not math.isfinite(float(value)) or not math.isfinite(float(actual[key])) or abs(float(actual[key]) - float(value)) >= tolerance:
                return False
    return True


def _baseline_economic_metrics(
    tape: list[dict[str, Any]],
    block_minutes: float,
    capital: float,
) -> dict[str, Any]:
    wins = [t for t in tape if float(t["pnl_net"]) > 0]
    losses = [t for t in tape if float(t["pnl_net"]) < 0]
    trail_hit = [t for t in tape if t.get("trailing_activated") is True]
    trail_never = [t for t in tape if t.get("trailing_activated") is not True]
    winning_pnl = sum(float(t["pnl_net"]) for t in wins)
    losing_pnl = -sum(float(t["pnl_net"]) for t in losses)
    net_pnl = winning_pnl - losing_pnl
    hold_minutes = sum(float(t.get("hold_minutes") or 0) for t in tape)
    return {
        "trades": len(tape),
        "trades_per_30d_month": round(len(tape) / (block_minutes / (30 * 24 * 60)), 4) if block_minutes else None,
        "net_pnl_usdt": round(net_pnl, 6),
        "net_return_percent_on_initial_capital": round(net_pnl / capital * 100, 6) if capital else None,
        "net_expectancy_usdt_per_trade": round(net_pnl / len(tape), 8) if tape else None,
        "profit_factor_net": round(winning_pnl / losing_pnl, 8) if losing_pnl > 0 else None,
        "winning_pnl_usdt": round(winning_pnl, 6),
        "losing_pnl_usdt": round(losing_pnl, 6),
        "trail_hit": {
            "trades": len(trail_hit),
            "share_percent": round(100 * len(trail_hit) / len(tape), 4) if tape else None,
            "net_pnl_usdt": round(sum(float(t["pnl_net"]) for t in trail_hit), 6),
            "net_expectancy_usdt_per_trade": round(
                sum(float(t["pnl_net"]) for t in trail_hit) / len(trail_hit), 8
            ) if trail_hit else None,
        },
        "trail_never_reached": {
            "trades": len(trail_never),
            "share_percent": round(100 * len(trail_never) / len(tape), 4) if tape else None,
            "net_pnl_usdt": round(sum(float(t["pnl_net"]) for t in trail_never), 6),
            "net_expectancy_usdt_per_trade": round(
                sum(float(t["pnl_net"]) for t in trail_never) / len(trail_never), 8
            ) if trail_never else None,
        },
        "time_in_market_percent": round(100 * hold_minutes / block_minutes, 4) if block_minutes else None,
    }


async def _strategy_a_scorecard_context() -> dict[str, Any]:
    source_job_id = "c4279991-66ae-44a2-9840-073c96bd8251"
    source_rows = await supabase_get("optimizer_runs", f"id=eq.{source_job_id}&limit=1")
    source = next((row for row in source_rows if row.get("id") == source_job_id), None)
    if source is None or source.get("status") != "completed":
        raise HTTPException(404, "Zmrazený Strategy A baseline run nebol nájdený.")
    source_result = source.get("result") or {}
    variants = source_result.get("variant_results") or []
    selected_id = source_result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_id), None)
    if row is None:
        raise HTTPException(422, "Baseline run nemá auditovateľný variant.")
    settings = dict(row.get("settings") or {})
    expected_locked = {
        "pair": "ZEC/USDT",
        "timeframe": "5m",
        "stop_loss_percent": 6.0,
        "trailing_start_percent": 1.6,
        "trailing_distance_percent": 0.3,
        "max_no_trail_hours": 4.0,
        "max_open_trades": 1,
    }
    actual = {
        "pair": row.get("pair"),
        "timeframe": settings.get("timeframe"),
        "stop_loss_percent": float(settings.get("stop_loss_percent") or 0),
        "trailing_start_percent": float(settings.get("trailing_start_percent") or 0),
        "trailing_distance_percent": float(settings.get("trailing_distance_percent") or 0),
        "max_no_trail_hours": float(settings.get("max_no_trail_hours") or 0),
        "max_open_trades": int(settings.get("max_open_trades") or 0),
    }
    if actual != expected_locked:
        raise HTTPException(409, f"Strategy A baseline lock mismatch: {actual}")
    cost_profile = row.get("cost_components")
    if not cost_profile:
        raise HTTPException(422, "Baseline nemá uložený cost snapshot.")
    return {
        "source_job_id": source_job_id,
        "row": row,
        "settings": settings,
        "expected_locked": expected_locked,
        "cost_profile": cost_profile,
    }


_STRATEGY_A_SCORECARD_BLOCKS = {
    "A": datetime(2025, 12, 27, 17, 15, tzinfo=UTC),
    "B": datetime(2026, 3, 27, 17, 15, tzinfo=UTC),
    "C": datetime(2026, 6, 25, 17, 15, tzinfo=UTC),
}


@app.post("/api/research/baseline-economic-scorecard/block/{label}")
async def baseline_economic_scorecard_block(label: str):
    """Run exactly one fixed 90d Strategy A economic block, idempotently."""
    require_durable_production_store()
    label = label.upper()
    if label not in _STRATEGY_A_SCORECARD_BLOCKS:
        raise HTTPException(422, "Block musí byť A, B alebo C.")

    scorecard_key = "strategy_a_zec_5m_3x90d_v1"
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            item for item in existing
            if (item.get("request") or {}).get("kind") == "baseline_economic_scorecard_block"
            and (item.get("request") or {}).get("scorecard_key") == scorecard_key
            and (item.get("request") or {}).get("block") == label
        ),
        None,
    )
    if prior:
        return prior

    context = await _strategy_a_scorecard_context()
    row = context["row"]
    settings = context["settings"]
    cost_profile = context["cost_profile"]
    pair, timeframe = "ZEC/USDT", "5m"
    step = TIMEFRAME_MILLISECONDS[timeframe]
    warmup = max(
        int(settings["bb_period"]),
        int(settings["rsi_period"]),
        int(settings["atr_period"]),
        20,
    ) + 2
    block_days = 90
    block_ms = block_days * 86_400_000
    block_minutes = float(block_days * 24 * 60)
    start_ms = int(_STRATEGY_A_SCORECARD_BLOCKS[label].timestamp() * 1000)
    end_ms = start_ms + block_ms - 1
    fetch_start = start_ms - warmup * step
    expected_total = int(block_ms // step + warmup)

    candles = await load_okx_candles(
        pair, timeframe, expected_total, fetch_start, end_ms
    )
    if len(candles) != expected_total:
        raise HTTPException(
            409, f"Block {label} dataset incomplete: {len(candles)}/{expected_total}."
        )
    if int(candles[0]["open_time"]) != fetch_start or int(candles[-1]["close_time"]) != end_ms:
        raise HTTPException(409, f"Block {label} does not match frozen boundaries.")
    if any(
        int(right["open_time"]) - int(left["open_time"]) != step
        for left, right in zip(candles, candles[1:])
    ):
        raise HTTPException(409, f"Block {label} contains a candle gap.")

    run = simulate(
        {pair: candles},
        settings,
        fee=float(row["cost_per_side"]),
        force_close_at_end=True,
        trading_start_time=start_ms,
        cost_models={pair: cost_profile},
    )
    tape = trade_tape(
        run, pair, timeframe, f"strategy_a_{label}", label, float(row["cost_per_side"])
    )
    econ = _baseline_economic_metrics(
        tape, block_minutes, float(settings["initial_capital"])
    )
    econ["max_drawdown_percent"] = float(run["metrics"]["max_drawdown_percent"])
    econ["buy_hold"] = buy_hold_risk_metrics(
        candles[warmup:], cost_profile, float(settings["initial_capital"])
    )
    econ["start_ms"] = start_ms
    econ["end_ms"] = end_ms
    econ["cost_book_ts"] = cost_profile.get("book_ts")
    econ["cost_model_basis"] = cost_profile.get("estimate_basis")

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "baseline_economic_scorecard_block",
            "scorecard_key": scorecard_key,
            "source_job_id": context["source_job_id"],
            "block": label,
            "pair": pair,
            "timeframe": timeframe,
            "start_time": start_ms,
            "end_time": end_ms,
            "grid": False,
            "optimizer": False,
        },
        "result": {
            "strategy": context["expected_locked"],
            "warmup_bars": warmup,
            "block": label,
            "economics": econ,
            "note": "Net of stored fee + spread + impact cost snapshot.",
        },
    }
    return await supabase_upsert("optimizer_runs", record)


@app.post("/api/research/baseline-economic-scorecard/finalize")
async def finalize_baseline_economic_scorecard():
    """Combine the three immutable block results; never fetches candles or reruns trades."""
    require_durable_production_store()
    scorecard_key = "strategy_a_zec_5m_3x90d_v1"
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    final = next(
        (
            item for item in existing
            if (item.get("request") or {}).get("kind") == "baseline_economic_scorecard"
            and (item.get("request") or {}).get("scorecard_key") == scorecard_key
        ),
        None,
    )
    if final:
        return final

    blocks_by_label = {}
    for item in existing:
        req = item.get("request") or {}
        if req.get("kind") != "baseline_economic_scorecard_block" or req.get("scorecard_key") != scorecard_key:
            continue
        label = req.get("block")
        if label in _STRATEGY_A_SCORECARD_BLOCKS and label not in blocks_by_label:
            blocks_by_label[label] = item
    missing = [label for label in ("A", "B", "C") if label not in blocks_by_label]
    if missing:
        raise HTTPException(409, f"Chýbajú scorecard bloky: {', '.join(missing)}.")

    block_results = [
        {"block": label, **blocks_by_label[label]["result"]["economics"]}
        for label in ("A", "B", "C")
    ]
    total_trades = sum(int(item["trades"]) for item in block_results)
    total_net = sum(float(item["net_pnl_usdt"]) for item in block_results)
    total_winning = sum(float(item["winning_pnl_usdt"]) for item in block_results)
    total_losing = sum(float(item["losing_pnl_usdt"]) for item in block_results)
    trail_trades = sum(int(item["trail_hit"]["trades"]) for item in block_results)
    trail_pnl = sum(float(item["trail_hit"]["net_pnl_usdt"]) for item in block_results)
    never_trades = sum(int(item["trail_never_reached"]["trades"]) for item in block_results)
    never_pnl = sum(float(item["trail_never_reached"]["net_pnl_usdt"]) for item in block_results)
    combined = {
        "trades": total_trades,
        "trades_per_30d_month": round(total_trades / 9, 4),
        "net_pnl_usdt": round(total_net, 6),
        "net_expectancy_usdt_per_trade": round(total_net / total_trades, 8) if total_trades else None,
        "profit_factor_net": round(total_winning / total_losing, 8) if total_losing > 0 else None,
        "winning_pnl_usdt": round(total_winning, 6),
        "losing_pnl_usdt": round(total_losing, 6),
        "trail_hit": {
            "trades": trail_trades,
            "share_percent": round(100 * trail_trades / total_trades, 4) if total_trades else None,
            "net_pnl_usdt": round(trail_pnl, 6),
            "net_expectancy_usdt_per_trade": round(trail_pnl / trail_trades, 8) if trail_trades else None,
        },
        "trail_never_reached": {
            "trades": never_trades,
            "share_percent": round(100 * never_trades / total_trades, 4) if total_trades else None,
            "net_pnl_usdt": round(never_pnl, 6),
            "net_expectancy_usdt_per_trade": round(never_pnl / never_trades, 8) if never_trades else None,
        },
        "time_in_market_percent": round(
            sum(float(item["time_in_market_percent"]) for item in block_results) / 3, 4
        ),
        "max_single_block_drawdown_percent": round(
            max(float(item["max_drawdown_percent"]) for item in block_results), 4
        ),
        "blocks_positive": sum(float(item["net_pnl_usdt"]) > 0 for item in block_results),
        "blocks_negative": sum(float(item["net_pnl_usdt"]) < 0 for item in block_results),
    }
    combined["economic_status"] = (
        "positive_across_majority"
        if total_net > 0 and combined["blocks_positive"] >= 2
        else "positive_but_concentrated"
        if total_net > 0
        else "negative"
    )

    context = await _strategy_a_scorecard_context()
    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "baseline_economic_scorecard",
            "scorecard_key": scorecard_key,
            "source_job_id": context["source_job_id"],
            "block_run_ids": [blocks_by_label[label]["id"] for label in ("A", "B", "C")],
            "grid": False,
            "optimizer": False,
        },
        "result": {
            "strategy": context["expected_locked"],
            "cost_profile": context["cost_profile"],
            "continuous_block_backtests": True,
            "blocks": block_results,
            "combined": combined,
            "note": "Each 90d block is continuous and net of the same stored fee + spread + impact cost snapshot. Finalization performs no market fetch and no strategy rerun.",
        },
    }
    return await supabase_upsert("optimizer_runs", record)


@app.post("/api/research/baseline-economic-scorecard")
async def baseline_economic_scorecard():
    """Continuous 3x90d economic scorecard for the frozen ZEC 5m Strategy A baseline."""
    require_durable_production_store()
    source_job_id = "c4279991-66ae-44a2-9840-073c96bd8251"
    scorecard_key = "strategy_a_zec_5m_3x90d_v1"

    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "baseline_economic_scorecard"
            and (row.get("request") or {}).get("scorecard_key") == scorecard_key
        ),
        None,
    )
    if prior:
        return prior

    source_rows = await supabase_get("optimizer_runs", f"id=eq.{source_job_id}&limit=1")
    source = next((row for row in source_rows if row.get("id") == source_job_id), None)
    if source is None or source.get("status") != "completed":
        raise HTTPException(404, "Zmrazený Strategy A baseline run nebol nájdený.")

    source_result = source.get("result") or {}
    variants = source_result.get("variant_results") or []
    selected_id = source_result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_id), None)
    if row is None:
        raise HTTPException(422, "Baseline run nemá auditovateľný variant.")

    settings = dict(row.get("settings") or {})
    locked = {
        "pair": row.get("pair"),
        "timeframe": settings.get("timeframe"),
        "stop_loss_percent": float(settings.get("stop_loss_percent") or 0),
        "trailing_start_percent": float(settings.get("trailing_start_percent") or 0),
        "trailing_distance_percent": float(settings.get("trailing_distance_percent") or 0),
        "max_no_trail_hours": float(settings.get("max_no_trail_hours") or 0),
        "max_open_trades": int(settings.get("max_open_trades") or 0),
    }
    expected_locked = {
        "pair": "ZEC/USDT",
        "timeframe": "5m",
        "stop_loss_percent": 6.0,
        "trailing_start_percent": 1.6,
        "trailing_distance_percent": 0.3,
        "max_no_trail_hours": 4.0,
        "max_open_trades": 1,
    }
    if locked != expected_locked:
        raise HTTPException(409, f"Strategy A baseline lock mismatch: {locked}")

    cost_profile = row.get("cost_components")
    if not cost_profile:
        raise HTTPException(422, "Baseline nemá uložený cost snapshot.")

    pair, timeframe = "ZEC/USDT", "5m"
    step = TIMEFRAME_MILLISECONDS[timeframe]
    warmup = max(
        int(settings["bb_period"]),
        int(settings["rsi_period"]),
        int(settings["atr_period"]),
        20,
    ) + 2
    block_days = 90
    block_ms = block_days * 86_400_000
    block_minutes = block_days * 24 * 60
    blocks = [
        ("A", datetime(2025, 12, 27, 17, 15, tzinfo=UTC)),
        ("B", datetime(2026, 3, 27, 17, 15, tzinfo=UTC)),
        ("C", datetime(2026, 6, 25, 17, 15, tzinfo=UTC)),
    ]

    block_results = []
    combined_tape: list[dict[str, Any]] = []
    max_dd = 0.0
    for label, start_dt in blocks:
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = start_ms + block_ms - 1
        fetch_start = start_ms - warmup * step
        expected_trade_bars = block_ms // step
        expected_total = int(expected_trade_bars + warmup)
        candles = await load_okx_candles(
            pair, timeframe, expected_total, fetch_start, end_ms
        )
        if len(candles) != expected_total:
            raise HTTPException(
                409,
                f"Block {label} dataset incomplete: {len(candles)}/{expected_total}.",
            )
        if int(candles[0]["open_time"]) != fetch_start or int(candles[-1]["close_time"]) != end_ms:
            raise HTTPException(409, f"Block {label} does not match frozen boundaries.")
        if any(
            int(right["open_time"]) - int(left["open_time"]) != step
            for left, right in zip(candles, candles[1:])
        ):
            raise HTTPException(409, f"Block {label} contains a candle gap.")

        run = simulate(
            {pair: candles},
            settings,
            fee=float(row["cost_per_side"]),
            force_close_at_end=True,
            trading_start_time=start_ms,
            cost_models={pair: cost_profile},
        )
        tape = trade_tape(
            run, pair, timeframe, f"strategy_a_{label}", label, float(row["cost_per_side"])
        )
        econ = _baseline_economic_metrics(
            tape, float(block_minutes), float(settings["initial_capital"])
        )
        econ["max_drawdown_percent"] = float(run["metrics"]["max_drawdown_percent"])
        econ["buy_hold"] = buy_hold_risk_metrics(
            candles[warmup:], cost_profile, float(settings["initial_capital"])
        )
        econ["start_ms"] = start_ms
        econ["end_ms"] = end_ms
        econ["cost_book_ts"] = cost_profile.get("book_ts")
        econ["cost_model_basis"] = cost_profile.get("estimate_basis")
        block_results.append({"block": label, **econ})
        combined_tape.extend(tape)
        max_dd = max(max_dd, float(run["metrics"]["max_drawdown_percent"]))

    combined_minutes = float(block_minutes * len(blocks))
    combined = _baseline_economic_metrics(
        combined_tape, combined_minutes, float(settings["initial_capital"])
    )
    combined["max_single_block_drawdown_percent"] = round(max_dd, 4)
    combined["blocks_positive"] = sum(float(item["net_pnl_usdt"]) > 0 for item in block_results)
    combined["blocks_negative"] = sum(float(item["net_pnl_usdt"]) < 0 for item in block_results)
    combined["all_blocks_net_pnl_usdt"] = round(
        sum(float(item["net_pnl_usdt"]) for item in block_results), 6
    )
    combined["economic_status"] = (
        "positive_across_majority"
        if combined["net_pnl_usdt"] > 0 and combined["blocks_positive"] >= 2
        else "positive_but_concentrated"
        if combined["net_pnl_usdt"] > 0
        else "negative"
    )

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "baseline_economic_scorecard",
            "scorecard_key": scorecard_key,
            "source_job_id": source_job_id,
            "pair": pair,
            "timeframe": timeframe,
            "blocks": [
                {"block": item["block"], "start_ms": item["start_ms"], "end_ms": item["end_ms"]}
                for item in block_results
            ],
            "grid": False,
            "optimizer": False,
        },
        "result": {
            "strategy": expected_locked,
            "cost_profile": cost_profile,
            "warmup_bars": warmup,
            "continuous_block_backtests": True,
            "blocks": block_results,
            "combined": combined,
            "note": "PnL and expectancy are net of the stored fee + spread + impact cost model.",
        },
    }
    return await supabase_upsert("optimizer_runs", record)


_MOMENTUM_PRESCREEN_COMMIT = "0dcd9bab922438f805053af5b54974091f86a2f7"
_MOMENTUM_PRESCREEN_BLOCKS = {
    "A": datetime(2025, 12, 27, 17, 15, tzinfo=UTC),
    "B": datetime(2026, 3, 27, 17, 15, tzinfo=UTC),
    "C": datetime(2026, 6, 25, 17, 15, tzinfo=UTC),
}
_MOMENTUM_PRESCREEN_SYMBOLS = {
    "ZEC": "ZEC/USDT",
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
}


@app.post("/api/research/momentum-prescreen/data/{block}/{symbol}")
async def momentum_prescreen_data(block: str, symbol: str):
    """Persist one immutable 90d 5m market snapshot for the preregistered pre-screen."""
    require_durable_production_store()
    block = block.upper()
    symbol = symbol.upper()
    if block not in _MOMENTUM_PRESCREEN_BLOCKS:
        raise HTTPException(422, "Block musí byť A, B alebo C.")
    if symbol not in _MOMENTUM_PRESCREEN_SYMBOLS:
        raise HTTPException(422, "Symbol musí byť ZEC, BTC alebo ETH.")

    snapshot_key = f"momentum_prescreen:{_MOMENTUM_PRESCREEN_COMMIT}:{block}:{symbol}"
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "momentum_prescreen_data"
            and (row.get("request") or {}).get("snapshot_key") == snapshot_key
        ),
        None,
    )
    if prior:
        return prior

    pair = _MOMENTUM_PRESCREEN_SYMBOLS[symbol]
    start_ms = int(_MOMENTUM_PRESCREEN_BLOCKS[block].timestamp() * 1000)
    end_ms = start_ms + 90 * 86_400_000 - 1
    step = TIMEFRAME_MILLISECONDS["5m"]
    expected = int((end_ms - start_ms + 1) // step)
    candles = await load_okx_candles(pair, "5m", expected, start_ms, end_ms)
    if len(candles) != expected:
        raise HTTPException(409, f"{block}/{symbol} incomplete: {len(candles)}/{expected}.")
    if int(candles[0]["open_time"]) != start_ms or int(candles[-1]["close_time"]) != end_ms:
        raise HTTPException(409, f"{block}/{symbol} boundary mismatch.")
    if any(int(b["open_time"]) - int(a["open_time"]) != step for a, b in zip(candles, candles[1:])):
        raise HTTPException(409, f"{block}/{symbol} candle gap.")

    snapshot = pack_market_series(candles)
    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "momentum_prescreen_data",
            "snapshot_key": snapshot_key,
            "preregistration_commit": _MOMENTUM_PRESCREEN_COMMIT,
            "block": block,
            "symbol": symbol,
            "pair": pair,
            "timeframe": "5m",
            "start_time": start_ms,
            "end_time": end_ms,
        },
        "result": {
            "snapshot": snapshot,
            "bars": expected,
            "source": "okx_public_spot",
        },
    }
    return await supabase_upsert("optimizer_runs", record)


async def _load_momentum_prescreen_snapshots() -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    rows = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        req = row.get("request") or {}
        if req.get("kind") != "momentum_prescreen_data":
            continue
        if req.get("preregistration_commit") != _MOMENTUM_PRESCREEN_COMMIT:
            continue
        key = (req.get("block"), req.get("symbol"))
        if key[0] in _MOMENTUM_PRESCREEN_BLOCKS and key[1] in _MOMENTUM_PRESCREEN_SYMBOLS and key not in found:
            found[key] = row
    missing = [
        f"{block}/{symbol}"
        for block in ("A", "B", "C")
        for symbol in ("ZEC", "BTC", "ETH")
        if (block, symbol) not in found
    ]
    if missing:
        raise HTTPException(409, f"Chýbajú momentum pre-screen snapshoty: {', '.join(missing)}.")
    return rows, found


@app.post("/api/research/momentum-prescreen/finalize")
async def finalize_momentum_prescreen():
    """Compute the locked pre-screen from immutable snapshots; no market fetch and no Strategy B."""
    require_durable_production_store()
    rows, found = await _load_momentum_prescreen_snapshots()
    prior = next(
        (
            row for row in rows
            if (row.get("request") or {}).get("kind") == "momentum_prescreen_result"
            and (row.get("request") or {}).get("preregistration_commit") == _MOMENTUM_PRESCREEN_COMMIT
        ),
        None,
    )
    if prior:
        return prior

    blocks = []
    snapshot_ids = []
    for block in ("A", "B", "C"):
        series = {}
        for symbol in ("ZEC", "BTC", "ETH"):
            row = found[(block, symbol)]
            snapshot_ids.append(row["id"])
            series[symbol] = unpack_market_series(row["result"]["snapshot"])
        blocks.append(series)

    analysis = pooled_analysis(blocks)
    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "momentum_prescreen_result",
            "preregistration_commit": _MOMENTUM_PRESCREEN_COMMIT,
            "snapshot_ids": snapshot_ids,
            "symbols": ["ZEC/USDT", "BTC/USDT", "ETH/USDT"],
            "timeframes": ["5m", "1h"],
            "diagnostic_breakout_lookback": 20,
            "grid": False,
            "strategy_b_backtest": False,
        },
        "result": {
            **analysis,
            "preregistration_commit": _MOMENTUM_PRESCREEN_COMMIT,
            "diagnostic_only": True,
            "strategy_b_defined": False,
            "pnl_computed": False,
        },
    }
    return await supabase_upsert("optimizer_runs", record)


_TSMOM_B_V1_SPEC = "TEST-SPEC-002"
_TSMOM_B_V1_SPEC_BASE_COMMIT = "264a15ef39ad176827d806419c0c13f671257dc1"
_TSMOM_B_V1_COST_SOURCE_RUN = "c4279991-66ae-44a2-9840-073c96bd8251"
_TSMOM_B_V1_COST_BOOK_TS = "1790183884652"
_TSMOM_B_V1_ENTRY_COST_RATE = 0.001086487119183424
_TSMOM_B_V1_EXIT_COST_RATE = 0.0010838075452430937
_TSMOM_B_V1_EVAL_START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
_TSMOM_B_V1_EVAL_END = datetime(2026, 12, 23, 11, 59, 59, 999000, tzinfo=UTC)
_TSMOM_B_V1_DATA = {
    "ZEC": "ZEC/USDT",
    "BTC": "BTC/USDT",
}


@app.get("/api/research/tsmom-b-v1/smoke")
async def tsmom_b_v1_smoke():
    """Synthetic-only smoke test. Never touches the official evaluation fold."""
    checks = tsmom_b_v1_smoke_cases()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "official_data_touched": False,
        "test_spec": _TSMOM_B_V1_SPEC,
        "base_spec_commit": _TSMOM_B_V1_SPEC_BASE_COMMIT,
    }


@app.post("/api/research/tsmom-b-v1/data/{symbol}")
async def tsmom_b_v1_data(symbol: str):
    """Persist one immutable official-fold OHLC snapshot for B v1."""
    require_durable_production_store()
    symbol = symbol.upper()
    if symbol not in _TSMOM_B_V1_DATA:
        raise HTTPException(422, "Symbol musí byť ZEC alebo BTC.")

    if datetime.now(UTC) <= _TSMOM_B_V1_EVAL_END:
        raise HTTPException(409, "Official TEST-SPEC-002 fold ešte neskončil; snapshot sa nesmie vytvoriť.")
    snapshot_key = f"tsmom_b_v1:{_TSMOM_B_V1_SPEC}:{symbol}:20260924_20261223"
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "tsmom_b_v1_data"
            and (row.get("request") or {}).get("snapshot_key") == snapshot_key
        ),
        None,
    )
    if prior:
        return prior

    pair = _TSMOM_B_V1_DATA[symbol]
    eval_start_ms = int(_TSMOM_B_V1_EVAL_START.timestamp() * 1000)
    eval_end_ms = int(_TSMOM_B_V1_EVAL_END.timestamp() * 1000)
    warmup_hours = 24 if symbol == "ZEC" else 25
    warmup_ms = warmup_hours * 60 * 60 * 1000
    fetch_start = eval_start_ms - warmup_ms
    step = TIMEFRAME_MILLISECONDS["5m"]
    expected = int((eval_end_ms - fetch_start + 1) // step)

    candles = await load_okx_candles(pair, "5m", expected, fetch_start, eval_end_ms)
    if len(candles) != expected:
        raise HTTPException(409, f"{symbol} B-v1 dataset incomplete: {len(candles)}/{expected}.")
    if int(candles[0]["open_time"]) != fetch_start or int(candles[-1]["close_time"]) != eval_end_ms:
        raise HTTPException(409, f"{symbol} B-v1 boundary mismatch.")
    if any(int(b["open_time"]) - int(a["open_time"]) != step for a, b in zip(candles, candles[1:])):
        raise HTTPException(409, f"{symbol} B-v1 candle gap.")
    if not math.isfinite(float(candles[-1]["close"])) or float(candles[-1]["close"]) <= 0:
        raise HTTPException(409, f"{symbol} final 5m close is invalid.")

    snapshot = pack_ohlc_snapshot(candles)
    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "tsmom_b_v1_data",
            "snapshot_key": snapshot_key,
            "test_spec": _TSMOM_B_V1_SPEC,
            "base_spec_commit": _TSMOM_B_V1_SPEC_BASE_COMMIT,
            "symbol": symbol,
            "pair": pair,
            "timeframe": "5m",
            "warmup_start": fetch_start,
            "evaluation_start": eval_start_ms,
            "evaluation_end": eval_end_ms,
        },
        "result": {
            "snapshot": snapshot,
            "bars": expected,
            "source": "okx_public_spot",
            "warmup_hours": warmup_hours,
        },
    }
    return await supabase_upsert("optimizer_runs", record)


async def _load_tsmom_b_v1_data() -> dict[str, dict[str, Any]]:
    rows = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        req = row.get("request") or {}
        if req.get("kind") != "tsmom_b_v1_data" or req.get("test_spec") != _TSMOM_B_V1_SPEC:
            continue
        symbol = req.get("symbol")
        if symbol in _TSMOM_B_V1_DATA and symbol not in found:
            found[symbol] = row
    missing = [symbol for symbol in ("ZEC", "BTC") if symbol not in found]
    if missing:
        raise HTTPException(409, f"Chýbajú B-v1 snapshoty: {', '.join(missing)}.")
    return found


@app.post("/api/research/tsmom-b-v1/evaluate")
async def evaluate_tsmom_b_v1():
    """Run the single official TEST-SPEC-002 evaluation. Idempotent."""
    require_durable_production_store()
    if datetime.now(UTC) <= _TSMOM_B_V1_EVAL_END:
        raise HTTPException(409, "Official TEST-SPEC-002 fold ešte neskončil; PASS/FAIL sa nesmie počítať.")
    checks = tsmom_b_v1_smoke_cases()
    if not all(checks.values()):
        raise HTTPException(409, "B-v1 synthetic smoke test neprešiel. Official run sa nespustil.")

    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "tsmom_b_v1_official_evaluation"
            and (row.get("request") or {}).get("test_spec") == _TSMOM_B_V1_SPEC
        ),
        None,
    )
    if prior:
        return prior

    data = await _load_tsmom_b_v1_data()
    zec = unpack_ohlc_snapshot(data["ZEC"]["result"]["snapshot"])
    btc = unpack_ohlc_snapshot(data["BTC"]["result"]["snapshot"])

    source_rows = await supabase_get("optimizer_runs", f"id=eq.{_TSMOM_B_V1_COST_SOURCE_RUN}&limit=1")
    source = next((row for row in source_rows if row.get("id") == _TSMOM_B_V1_COST_SOURCE_RUN), None)
    if source is None:
        raise HTTPException(404, "Strategy A source cost snapshot nebol nájdený.")
    source_result = source.get("result") or {}
    variants = source_result.get("variant_results") or []
    selected_id = source_result.get("variant_id")
    selected = next((item for item in variants if item.get("variant_id") == selected_id), None)
    if selected is None or not selected.get("cost_components"):
        raise HTTPException(422, "Strategy A source nemá cost snapshot.")
    cost_profile = selected["cost_components"]
    cost_lock_ok = (
        str(cost_profile.get("book_ts")) == _TSMOM_B_V1_COST_BOOK_TS
        and math.isclose(
            float(cost_profile.get("entry_cost_rate")),
            _TSMOM_B_V1_ENTRY_COST_RATE,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
        and math.isclose(
            float(cost_profile.get("exit_cost_rate")),
            _TSMOM_B_V1_EXIT_COST_RATE,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
    )
    if not cost_lock_ok:
        raise HTTPException(
            409,
            "TEST-SPEC-002 frozen Strategy A cost snapshot mismatch; official run is invalid.",
        )

    eval_start_ms = int(_TSMOM_B_V1_EVAL_START.timestamp() * 1000)
    eval_end_ms = int(_TSMOM_B_V1_EVAL_END.timestamp() * 1000)
    outcome = await asyncio.to_thread(
        simulate_b_v1,
        zec,
        btc,
        evaluation_start_ms=eval_start_ms,
        evaluation_end_ms=eval_end_ms,
        stake_amount=50.0,
        initial_capital=100.0,
        cost_profile=cost_profile,
    )

    eval_zec = [
        c for c in zec
        if int(c["open_time"]) >= eval_start_ms and int(c["close_time"]) <= eval_end_ms
    ]
    benchmark = buy_hold_risk_metrics(eval_zec, cost_profile, 100.0)

    result = {
        **outcome,
        "test_spec": _TSMOM_B_V1_SPEC,
        "base_spec_commit": _TSMOM_B_V1_SPEC_BASE_COMMIT,
        "evaluation_window": {"start_ms": eval_start_ms, "end_ms": eval_end_ms},
        "cost_profile": cost_profile,
        "cost_snapshot_lock": {
            "source_run_id": _TSMOM_B_V1_COST_SOURCE_RUN,
            "book_ts": _TSMOM_B_V1_COST_BOOK_TS,
            "entry_cost_rate": _TSMOM_B_V1_ENTRY_COST_RATE,
            "exit_cost_rate": _TSMOM_B_V1_EXIT_COST_RATE,
            "validated": True,
        },
        "short_execution_interpretation": "research_marks_on_okx_spot_prints_not_borrow_free_spot_execution",
        "official_replay_mode": "single_batch_replay_after_fold_end",
        "buy_hold": benchmark,
        "data_snapshot_ids": {
            "ZEC": data["ZEC"]["id"],
            "BTC": data["BTC"]["id"],
        },
        "smoke_checks": checks,
        "official_run_number": 1,
        "grid_started": False,
        "strategy_a_logic_reused": False,
    }

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "tsmom_b_v1_official_evaluation",
            "test_spec": _TSMOM_B_V1_SPEC,
            "base_spec_commit": _TSMOM_B_V1_SPEC_BASE_COMMIT,
            "pair": "ZEC/USDT",
            "execution_timeframe": "5m",
            "signal_timeframe": "1h",
            "evaluation_start": eval_start_ms,
            "evaluation_end": eval_end_ms,
            "breakout_n": 24,
            "atr_period": 24,
            "atr_multiple": 2.0,
            "stake_amount": 50.0,
            "initial_capital": 100.0,
            "cost_source_run_id": _TSMOM_B_V1_COST_SOURCE_RUN,
            "cost_book_ts": _TSMOM_B_V1_COST_BOOK_TS,
            "official_replay_mode": "single_batch_replay_after_fold_end",
            "grid": False,
        },
        "result": result,
    }
    return await supabase_upsert("optimizer_runs", record)


_TSMOM_C_V1_SPEC = "TEST-SPEC-003"
_TSMOM_C_V1_SPEC_COMMIT = "75e4ab2d90c061ae63d45b0739f132c6400cf94e"
_TSMOM_C_V1_EVAL_START = datetime(2025, 9, 2, 0, 0, tzinfo=UTC)
_TSMOM_C_V1_EVAL_END = datetime(2025, 11, 30, 23, 59, 59, 999000, tzinfo=UTC)
_TSMOM_C_V1_DATA = {
    "BTC": {"pair": "BTC/USDT", "warmup_hours": 24},
    "ETH": {"pair": "ETH/USDT", "warmup_hours": 25},
}
_TSMOM_C_V1_COST_PROFILE = {
    "fee_schedule": "okx_global_regular",
    "fee_taker": 0.001,
    "fee_maker": 0.0008,
    "role": "taker",
    "fee_rate": 0.001,
    "source": "okx",
    "book_ts": "1790275677755",
    "estimate_basis": "current_book_snapshot_not_historical_l2",
    "notional_usdt": 50.0,
    "half_spread": 5.926681029335338e-07,
    "buy_impact": 0.0,
    "sell_impact": 0.0,
    "entry_cost_rate": 0.0010005926681029335,
    "exit_cost_rate": 0.0010005926681029335,
    "book_impact_ratio": 0.0,
    "thin_L1": False,
}


@app.get("/api/research/tsmom-c-v1/smoke")
async def tsmom_c_v1_smoke():
    """Synthetic-only Strategy C checks. Never touches official fold data."""
    checks = tsmom_c_v1_smoke_cases()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "official_data_touched": False,
        "test_spec": _TSMOM_C_V1_SPEC,
        "spec_commit": _TSMOM_C_V1_SPEC_COMMIT,
        "strategy_b_modified": False,
    }


@app.post("/api/research/tsmom-c-v1/data/{symbol}")
async def tsmom_c_v1_data(symbol: str):
    """Persist immutable raw 5m snapshot for TEST-SPEC-003 without performance metrics."""
    require_durable_production_store()
    symbol = symbol.upper()
    if symbol not in _TSMOM_C_V1_DATA:
        raise HTTPException(422, "Symbol musí byť BTC alebo ETH.")

    cfg = _TSMOM_C_V1_DATA[symbol]
    snapshot_key = f"tsmom_c_v1:{_TSMOM_C_V1_SPEC}:{symbol}:20250902_20251130"
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "tsmom_c_v1_data"
            and (row.get("request") or {}).get("snapshot_key") == snapshot_key
        ),
        None,
    )
    if prior:
        return prior

    eval_start_ms = int(_TSMOM_C_V1_EVAL_START.timestamp() * 1000)
    eval_end_ms = int(_TSMOM_C_V1_EVAL_END.timestamp() * 1000)
    fetch_start = eval_start_ms - int(cfg["warmup_hours"]) * 3_600_000
    expected = int((eval_end_ms - fetch_start + 1) // TIMEFRAME_MILLISECONDS["5m"])
    candles = await load_okx_candles(
        str(cfg["pair"]),
        "5m",
        expected,
        fetch_start,
        eval_end_ms,
    )
    if not candles:
        raise HTTPException(409, f"{symbol} TEST-SPEC-003 dataset je prázdny.")

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "tsmom_c_v1_data",
            "snapshot_key": snapshot_key,
            "test_spec": _TSMOM_C_V1_SPEC,
            "spec_commit": _TSMOM_C_V1_SPEC_COMMIT,
            "symbol": symbol,
            "pair": cfg["pair"],
            "timeframe": "5m",
            "warmup_hours": cfg["warmup_hours"],
            "warmup_start": fetch_start,
            "evaluation_start": eval_start_ms,
            "evaluation_end": eval_end_ms,
            "expected_5m_bars": expected,
        },
        "result": {
            "snapshot": pack_c_ohlc_snapshot(candles),
            "actual_5m_bars": len(candles),
            "expected_5m_bars": expected,
            "first_open_time": int(candles[0]["open_time"]),
            "last_close_time": int(candles[-1]["close_time"]),
            "source": "okx_public_spot",
            "strategy_metrics_computed": False,
        },
    }
    return await supabase_upsert("optimizer_runs", record)


async def _load_tsmom_c_v1_data() -> dict[str, dict[str, Any]]:
    rows = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        req = row.get("request") or {}
        if req.get("kind") != "tsmom_c_v1_data":
            continue
        if req.get("test_spec") != _TSMOM_C_V1_SPEC:
            continue
        symbol = req.get("symbol")
        if symbol in _TSMOM_C_V1_DATA and symbol not in found:
            found[symbol] = row
    missing = [symbol for symbol in ("BTC", "ETH") if symbol not in found]
    if missing:
        raise HTTPException(409, f"Chýbajú TEST-SPEC-003 snapshoty: {', '.join(missing)}.")
    return found


@app.post("/api/research/tsmom-c-v1/continuity-cert")
async def tsmom_c_v1_continuity_cert():
    """Certify raw data/exact-entry availability only; no Strategy C performance metrics."""
    require_durable_production_store()
    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "tsmom_c_v1_continuity_certificate"
            and (row.get("request") or {}).get("test_spec") == _TSMOM_C_V1_SPEC
        ),
        None,
    )
    if prior:
        return prior

    data = await _load_tsmom_c_v1_data()
    btc = unpack_c_ohlc_snapshot(data["BTC"]["result"]["snapshot"])
    eth = unpack_c_ohlc_snapshot(data["ETH"]["result"]["snapshot"])

    eval_start_ms = int(_TSMOM_C_V1_EVAL_START.timestamp() * 1000)
    eval_end_ms = int(_TSMOM_C_V1_EVAL_END.timestamp() * 1000)
    btc_start = eval_start_ms - 24 * 3_600_000
    eth_start = eval_start_ms - 25 * 3_600_000
    cert = await asyncio.to_thread(
        tsmom_c_v1_continuity_certificate,
        btc,
        eth,
        evaluation_start_ms=eval_start_ms,
        evaluation_end_ms=eval_end_ms,
        btc_warmup_start_ms=btc_start,
        eth_warmup_start_ms=eth_start,
    )

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "tsmom_c_v1_continuity_certificate",
            "test_spec": _TSMOM_C_V1_SPEC,
            "spec_commit": _TSMOM_C_V1_SPEC_COMMIT,
            "data_snapshot_ids": {
                "BTC": data["BTC"]["id"],
                "ETH": data["ETH"]["id"],
            },
        },
        "result": {
            **cert,
            "official_performance_metrics_computed": False,
            "pnl_computed": False,
            "profit_factor_computed": False,
            "pass_fail_computed": False,
        },
    }
    return await supabase_upsert("optimizer_runs", record)


@app.post("/api/research/tsmom-c-v1/evaluate")
async def evaluate_tsmom_c_v1():
    """Run the single official TEST-SPEC-003 evaluation after a passed continuity cert."""
    require_durable_production_store()
    checks = tsmom_c_v1_smoke_cases()
    if not all(checks.values()):
        raise HTTPException(409, "TEST-SPEC-003 synthetic smoke neprešiel.")

    rows = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in rows
            if (row.get("request") or {}).get("kind") == "tsmom_c_v1_official_evaluation"
            and (row.get("request") or {}).get("test_spec") == _TSMOM_C_V1_SPEC
        ),
        None,
    )
    if prior:
        return prior

    cert_row = next(
        (
            row for row in rows
            if (row.get("request") or {}).get("kind") == "tsmom_c_v1_continuity_certificate"
            and (row.get("request") or {}).get("test_spec") == _TSMOM_C_V1_SPEC
        ),
        None,
    )
    if cert_row is None:
        raise HTTPException(409, "Chýba TEST-SPEC-003 continuity cert.")
    cert_result = cert_row.get("result") or {}
    if cert_result.get("passed") is not True:
        raise HTTPException(409, "TEST-SPEC-003 continuity cert neprešiel; official run je zakázaný.")
    if cert_result.get("official_performance_metrics_computed") is not False:
        raise HTTPException(409, "Continuity cert nie je čistý data-only cert.")

    data = await _load_tsmom_c_v1_data()
    btc = unpack_c_ohlc_snapshot(data["BTC"]["result"]["snapshot"])
    eth = unpack_c_ohlc_snapshot(data["ETH"]["result"]["snapshot"])

    eval_start_ms = int(_TSMOM_C_V1_EVAL_START.timestamp() * 1000)
    eval_end_ms = int(_TSMOM_C_V1_EVAL_END.timestamp() * 1000)
    outcome = await asyncio.to_thread(
        simulate_c_v1,
        btc,
        eth,
        evaluation_start_ms=eval_start_ms,
        evaluation_end_ms=eval_end_ms,
        stake_amount=50.0,
        initial_capital=100.0,
        cost_profile=dict(_TSMOM_C_V1_COST_PROFILE),
    )
    eval_btc = [
        row for row in btc
        if int(row["open_time"]) >= eval_start_ms
        and int(row["close_time"]) <= eval_end_ms
    ]
    benchmark = buy_hold_risk_metrics(
        eval_btc,
        dict(_TSMOM_C_V1_COST_PROFILE),
        100.0,
    )

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "tsmom_c_v1_official_evaluation",
            "test_spec": _TSMOM_C_V1_SPEC,
            "spec_commit": _TSMOM_C_V1_SPEC_COMMIT,
            "pair": "BTC/USDT",
            "execution_timeframe": "5m",
            "signal_timeframe": "1h",
            "evaluation_start": eval_start_ms,
            "evaluation_end": eval_end_ms,
            "breakout_n": 24,
            "atr_period": 24,
            "atr_multiple": 2.0,
            "stake_amount": 50.0,
            "initial_capital": 100.0,
            "grid": False,
            "continuity_certificate_id": cert_row["id"],
            "data_snapshot_ids": {
                "BTC": data["BTC"]["id"],
                "ETH": data["ETH"]["id"],
            },
        },
        "result": {
            **outcome,
            "test_spec": _TSMOM_C_V1_SPEC,
            "spec_commit": _TSMOM_C_V1_SPEC_COMMIT,
            "evaluation_window": {
                "start_ms": eval_start_ms,
                "end_ms": eval_end_ms,
            },
            "cost_profile": dict(_TSMOM_C_V1_COST_PROFILE),
            "short_execution_interpretation": "research_marks_on_okx_spot_prints_not_borrow_free_spot_execution",
            "eth_overlap_role": "diagnostic_only_not_pass_gate",
            "buy_hold": benchmark,
            "continuity_certificate_id": cert_row["id"],
            "official_run_number": 1,
            "grid_started": False,
            "strategy_b_modified": False,
        },
    }
    return await supabase_upsert("optimizer_runs", record)


@app.get("/api/research/rebound-confirmation/smoke")
async def rebound_confirmation_smoke():
    """Synthetic-only preregistration smoke test. Never touches evaluation data."""
    checks = rebound_smoke_cases()
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "evaluation_data_touched": False,
        "preregistration_commit": "4bd956d077bbaa24bac60adc12380aa4e2609817",
    }


@app.post("/api/research/rebound-confirmation/evaluate")
async def evaluate_rebound_confirmation():
    """Run the single preregistered baseline-vs-Filter-B paired evaluation."""
    require_durable_production_store()
    prereg_commit = "4bd956d077bbaa24bac60adc12380aa4e2609817"
    source_job_id = "c4279991-66ae-44a2-9840-073c96bd8251"

    checks = rebound_smoke_cases()
    if not all(checks.values()):
        raise HTTPException(409, "Synthetic smoke test neprešiel. Evaluation sa nespustila.")

    existing = await supabase_get("optimizer_runs", "order=created_at.desc&limit=100")
    prior = next(
        (
            row for row in existing
            if (row.get("request") or {}).get("kind") == "preregistered_rebound_confirmation"
            and (row.get("request") or {}).get("preregistration_commit") == prereg_commit
        ),
        None,
    )
    if prior:
        return prior

    source_rows = await supabase_get("optimizer_runs", f"id=eq.{source_job_id}&limit=1")
    source = next((row for row in source_rows if row.get("id") == source_job_id), None)
    if source is None or source.get("status") != "completed":
        raise HTTPException(404, "Zmrazený 4h research baseline run nebol nájdený.")
    source_result = source.get("result") or {}
    variants = source_result.get("variant_results") or []
    selected_id = source_result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_id), None)
    if row is None:
        raise HTTPException(422, "Baseline run nemá auditovateľný variant.")

    settings = dict(row.get("settings") or {})
    locked = {
        "timeframe": settings.get("timeframe"),
        "max_no_trail_hours": float(settings.get("max_no_trail_hours") or 0),
        "stop_loss_percent": float(settings.get("stop_loss_percent") or 0),
        "trailing_start_percent": float(settings.get("trailing_start_percent") or 0),
        "trailing_distance_percent": float(settings.get("trailing_distance_percent") or 0),
    }
    if locked != {
        "timeframe": "5m",
        "max_no_trail_hours": 4.0,
        "stop_loss_percent": 6.0,
        "trailing_start_percent": 1.6,
        "trailing_distance_percent": 0.3,
    }:
        raise HTTPException(409, f"Baseline sa nezhoduje s preregistráciou: {locked}")

    cost_profile = row.get("cost_components")
    if not cost_profile:
        raise HTTPException(422, "Baseline nemá fixný cost snapshot.")

    pair = "ZEC/USDT"
    timeframe = "5m"
    start_ms = int(datetime(2025, 12, 27, 17, 15, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime(2026, 3, 27, 17, 15, tzinfo=UTC).timestamp() * 1000) - 1
    step = TIMEFRAME_MILLISECONDS[timeframe]
    expected = int((end_ms - start_ms + 1) // step)
    candles = await load_okx_candles(pair, timeframe, expected, start_ms, end_ms)
    if len(candles) != expected:
        raise HTTPException(409, f"Evaluation dataset nie je kompletný: {len(candles)}/{expected}.")
    if int(candles[0]["open_time"]) != start_ms or int(candles[-1]["close_time"]) != end_ms:
        raise HTTPException(409, "Evaluation dataset nesedí na preregistrované hranice.")
    if any(int(right["open_time"]) - int(left["open_time"]) != step for left, right in zip(candles, candles[1:])):
        raise HTTPException(409, "Evaluation dataset obsahuje medzeru.")

    warmup = max(40, int(settings["bb_period"]) + 2, int(settings["rsi_period"]) + 2, int(settings["atr_period"]) + 2)
    wf, holdout = walk_forward_windows(candles, warmup)
    boundary = max(120, int(len(candles) * .8))
    segments: list[tuple[str, list[dict[str, Any]], int]] = []
    for number, (train, validation) in enumerate(wf, 1):
        segments.append((f"wf{number}", validation, int(candles[len(train)]["open_time"])))
    segments.append(("holdout", holdout, int(candles[boundary]["open_time"])))

    global_index_by_close = {int(candle["close_time"]): index for index, candle in enumerate(candles)}
    trail_start = float(settings["trailing_start_percent"])
    cost_options = {"cost_models": {pair: cost_profile}}
    baseline_all: list[dict[str, Any]] = []
    candidate_all: list[dict[str, Any]] = []
    baseline_logs: list[dict[str, Any]] = []
    candidate_logs: list[dict[str, Any]] = []
    baseline_segment_metrics = []
    candidate_segment_metrics = []

    def iso_ms(value: str) -> int:
        return int(round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000))

    def decorate(trade: dict[str, Any]) -> dict[str, Any]:
        path = enrich_trade_path(candles, trade, trail_start)
        return {
            **trade,
            "trail_hit": path["time_to_trail_min"] is not None,
            "mae_pct": trade.get("mae"),
            **path,
        }

    for label, segment, trading_start in segments:
        baseline_run = simulate(
            {pair: segment},
            settings,
            fee=float(row["cost_per_side"]),
            force_close_at_end=True,
            trading_start_time=trading_start,
            **cost_options,
        )
        baseline_segment_metrics.append(baseline_run["metrics"])
        baseline_tape = trade_tape(
            baseline_run, pair, timeframe, "baseline", label, float(row["cost_per_side"])
        )
        baseline_tape = [decorate(trade) for trade in baseline_tape]
        baseline_all.extend(baseline_tape)
        allowed_times = {iso_ms(trade["entry_ts"]) for trade in baseline_tape}

        def gate(_pair: str, _segment: list[dict[str, Any]], segment_index: int) -> bool:
            close_time = int(_segment[segment_index]["close_time"])
            global_index = global_index_by_close.get(close_time)
            if global_index is None:
                raise ValueError("candidate_signal_not_in_evaluation_feed")
            return rebound_filter_b(candles, global_index)

        candidate_run = simulate(
            {pair: segment},
            settings,
            fee=float(row["cost_per_side"]),
            force_close_at_end=True,
            trading_start_time=trading_start,
            allowed_entry_times=allowed_times,
            entry_gate=gate,
            **cost_options,
        )
        candidate_segment_metrics.append(candidate_run["metrics"])
        candidate_tape = trade_tape(
            candidate_run, pair, timeframe, "candidate_filter_b", label, float(row["cost_per_side"])
        )
        candidate_tape = [decorate(trade) for trade in candidate_tape]
        candidate_all.extend(candidate_tape)

        baseline_by_entry = {trade["entry_ts"]: trade for trade in baseline_tape}
        candidate_by_entry = {trade["entry_ts"]: trade for trade in candidate_tape}
        if not set(candidate_by_entry).issubset(set(baseline_by_entry)):
            raise HTTPException(500, "Candidate vytvoril entry mimo baseline signal universe.")

        for entry_ts, baseline_trade in baseline_by_entry.items():
            entry_ms = iso_ms(entry_ts)
            context = rebound_confirmation_context(
                candles, entry_ms, float(baseline_trade["entry_px"])
            )
            baseline_logs.append({
                "timestamp": entry_ts,
                "window": label,
                "taken": 1,
                "skip_reason": "none",
                "entry_price": baseline_trade["entry_px"],
                "confirm_4h_ts": None,
                "trail_hit": baseline_trade["trail_hit"],
                "outcome": "win" if float(baseline_trade["pnl_net"]) > 0 else "loss",
                "mae_pct": baseline_trade["mae_pct"],
                "mae_before_trail_pct": baseline_trade["mae_before_trail_pct"],
                "hit_minus_1pct": baseline_trade["hit_minus_1pct"],
                "time_to_trail_min": baseline_trade["time_to_trail_min"],
                "time_to_minus_1pct_min": baseline_trade["time_to_minus_1pct_min"],
                "pre_entry_1h_ret": baseline_trade["pre_entry_1h_ret"],
                "pre_entry_4h_ret": baseline_trade["pre_entry_4h_ret"],
                "rv_24bars": baseline_trade["rv_24bars"],
                "range_pos_24h": context["range_pos_24h"],
                "pct_above_24h_low": context["pct_above_24h_low"],
                "bars_since_24h_low": context["bars_since_24h_low"],
            })
            taken = candidate_by_entry.get(entry_ts)
            if taken is not None:
                candidate_logs.append({
                    "timestamp": entry_ts,
                    "window": label,
                    "taken": 1,
                    "skip_reason": "none",
                    "entry_price": taken["entry_px"],
                    "confirm_4h_ts": context["confirm_4h_ts"],
                    "trail_hit": taken["trail_hit"],
                    "outcome": "win" if float(taken["pnl_net"]) > 0 else "loss",
                    "mae_pct": taken["mae_pct"],
                    "mae_before_trail_pct": taken["mae_before_trail_pct"],
                    "hit_minus_1pct": taken["hit_minus_1pct"],
                    "time_to_trail_min": taken["time_to_trail_min"],
                    "time_to_minus_1pct_min": taken["time_to_minus_1pct_min"],
                    "pre_entry_1h_ret": taken["pre_entry_1h_ret"],
                    "pre_entry_4h_ret": taken["pre_entry_4h_ret"],
                    "rv_24bars": taken["rv_24bars"],
                    "range_pos_24h": context["range_pos_24h"],
                    "pct_above_24h_low": context["pct_above_24h_low"],
                    "bars_since_24h_low": context["bars_since_24h_low"],
                })
            else:
                candidate_logs.append({
                    "timestamp": entry_ts,
                    "window": label,
                    "taken": 0,
                    "skip_reason": "no_rebound_confirm",
                    "entry_price": baseline_trade["entry_px"],
                    "confirm_4h_ts": None,
                    "trail_hit": None,
                    "outcome": None,
                    "mae_pct": None,
                    "mae_before_trail_pct": None,
                    "hit_minus_1pct": None,
                    "time_to_trail_min": None,
                    "time_to_minus_1pct_min": None,
                    "pre_entry_1h_ret": baseline_trade["pre_entry_1h_ret"],
                    "pre_entry_4h_ret": baseline_trade["pre_entry_4h_ret"],
                    "rv_24bars": baseline_trade["rv_24bars"],
                    "range_pos_24h": context["range_pos_24h"],
                    "pct_above_24h_low": context["pct_above_24h_low"],
                    "bars_since_24h_low": context["bars_since_24h_low"],
                    "baseline_counterfactual_trail_hit": baseline_trade["trail_hit"],
                    "baseline_counterfactual_outcome": "win" if float(baseline_trade["pnl_net"]) > 0 else "loss",
                })

    if len({item["timestamp"] for item in baseline_logs}) != len(baseline_logs):
        raise HTTPException(500, "Baseline signal universe obsahuje duplicitný timestamp.")
    if {item["timestamp"] for item in candidate_logs} != {item["timestamp"] for item in baseline_logs}:
        raise HTTPException(500, "Baseline a candidate nemajú rovnaký signal universe.")

    baseline_primary = paired_metrics(baseline_all)
    candidate_primary = paired_metrics(candidate_all)
    baseline_summary = aggregate_metrics(baseline_segment_metrics, float(settings["initial_capital"]))
    candidate_summary = aggregate_metrics(candidate_segment_metrics, float(settings["initial_capital"]))

    if baseline_primary["trail_not_reached_n"] < 20:
        verdict = "INSUFFICIENT_SAMPLE"
        conditions = None
    else:
        wr_delta = (
            float(candidate_primary["wr_trail_not_reached"]) - float(baseline_primary["wr_trail_not_reached"])
            if candidate_primary["wr_trail_not_reached"] is not None and baseline_primary["wr_trail_not_reached"] is not None
            else float("-inf")
        )
        trail_share_delta = (
            float(candidate_primary["trail_reached_share"]) - float(baseline_primary["trail_reached_share"])
            if candidate_primary["trail_reached_share"] is not None and baseline_primary["trail_reached_share"] is not None
            else float("-inf")
        )
        trade_reduction = 1 - (int(candidate_primary["trades"]) / int(baseline_primary["trades"]))
        conditions = {
            "trail_not_reached_wr_plus_15pp": wr_delta >= 15,
            "trail_reached_share_drop_max_10pp": trail_share_delta >= -10,
            "trail_reached_wr_at_least_90": (
                candidate_primary["wr_trail_reached"] is not None
                and float(candidate_primary["wr_trail_reached"]) >= 90
            ),
            "trade_count_drop_max_35pct": trade_reduction <= .35,
        }
        verdict = "PASS" if all(conditions.values()) else "FAIL"

    skipped = [row for row in candidate_logs if row["taken"] == 0]
    skipped_no_trail = sum(row.get("baseline_counterfactual_trail_hit") is False for row in skipped)
    skipped_no_trail_share = 100 * skipped_no_trail / len(skipped) if skipped else 0.0
    trail_delta = (
        float(candidate_primary["trail_reached_share"]) - float(baseline_primary["trail_reached_share"])
        if candidate_primary["trail_reached_share"] is not None and baseline_primary["trail_reached_share"] is not None
        else None
    )

    benchmark = buy_hold_risk_metrics(candles, cost_profile, float(settings["initial_capital"]))
    report = {
        "preregistration_commit": prereg_commit,
        "evaluation_window": {"start_ms": start_ms, "end_ms": end_ms},
        "signal_universe": "baseline_opened_5m_entries_only",
        "baseline": {
            "primary": baseline_primary,
            "strategy_metrics": baseline_summary,
            "signals": baseline_logs,
        },
        "candidate": {
            "primary": candidate_primary,
            "strategy_metrics": candidate_summary,
            "signals": candidate_logs,
            "skip_rate": 100 * len(skipped) / len(candidate_logs) if candidate_logs else 0.0,
        },
        "benchmark": benchmark,
        "conditions": conditions,
        "verdict": verdict,
        "interpretation": [
            f"Filter B skipped {len(skipped)} baseline entries; {skipped_no_trail_share:.1f}% of skipped entries were baseline trail-not-reached trades.",
            (
                f"Trail-reached share changed by {trail_delta:+.1f} percentage points."
                if trail_delta is not None else
                "Trail-reached share delta is unavailable."
            ),
        ],
        "smoke_checks": checks,
        "grid_started": False,
        "strategy_parameters_changed": False,
    }

    now = datetime.now(UTC).isoformat()
    record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "preregistered_rebound_confirmation",
            "preregistration_commit": prereg_commit,
            "source_job_id": source_job_id,
            "pair": pair,
            "timeframe": timeframe,
            "start_time": start_ms,
            "end_time": end_ms,
            "modes": ["baseline", "candidate_filter_b"],
            "grid": False,
        },
        "result": report,
    }
    return await supabase_upsert("optimizer_runs", record)


@app.get("/api/optimizer/{job_id}/entry-path-audit")
async def optimizer_entry_path_audit(job_id: str):
    """Read-only per-entry regime/path diagnostics from the stored validation snapshot."""
    stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=1")
    record = next((row for row in stored if row.get("id") == job_id), None)
    if record is None or record.get("status") != "completed":
        raise HTTPException(404, "Completed optimizer run nebol nájdený.")

    result = record.get("result") or {}
    variants = result.get("variant_results") or []
    selected_variant_id = result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_variant_id), None)
    if row is None:
        raise HTTPException(422, "Run nemá auditovateľný vybraný variant.")
    if row.get("timeframe") != "5m":
        raise HTTPException(422, "Entry path audit je zatiaľ definovaný pre 5m tape.")

    snapshot = (result.get("replay_snapshots") or {}).get(row.get("snapshot_id"))
    if not snapshot:
        raise HTTPException(422, "Run nemá validačný snapshot.")
    candles = unpack_snapshot(snapshot)
    trades = [
        trade for trade in (row.get("trades") or result.get("trades") or [])
        if trade.get("window") in {"wf1", "wf2", "wf3"}
    ]
    expected = int((row.get("walk_forward_metrics") or {}).get("closed_trades") or 0)
    if len(trades) != expected:
        raise HTTPException(409, "Uložený WF tape nie je kompletný.")

    trail_start = float((row.get("settings") or {}).get("trailing_start_percent") or 0)
    if trail_start <= 0:
        raise HTTPException(422, "Run nemá platný trailing start.")

    audit = entry_path_audit(candles, trades, trail_start)
    return {
        "source_job_id": job_id,
        "pair": row.get("pair"),
        "timeframe": row.get("timeframe"),
        "variant_id": row.get("variant_id"),
        "trail_start_percent": trail_start,
        "read_only": True,
        "grid_started": False,
        "definitions": {
            "pre_return_1h": "entry close / close 12 bars earlier - 1",
            "pre_return_4h": "entry close / close 48 bars earlier - 1",
            "realized_vol": "population stdev of close-to-close log returns; not annualized",
            "local_swing": "rolling 24h high/low over 288 five-minute bars ending at entry",
            "path_thresholds": "post-entry candle high/low; entry candle excluded",
        },
        **audit,
    }


@app.get("/api/optimizer/{job_id}/benchmark-risk")
async def benchmark_risk(job_id: str):
    """Read-only B&H risk audit on the exact WF and holdout windows of a stored optimizer run."""
    stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=1")
    record = next((row for row in stored if row.get("id") == job_id), None)
    if record is None or record.get("status") != "completed":
        raise HTTPException(404, "Completed optimizer run nebol nájdený.")

    result = record.get("result") or {}
    variants = result.get("variant_results") or []
    selected_variant_id = result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_variant_id), None)
    if row is None:
        raise HTTPException(422, "Run nemá auditovateľný vybraný variant.")
    profile = row.get("cost_components")
    if not profile:
        raise HTTPException(422, "Run nemá uložený cost snapshot.")
    snapshot = (result.get("replay_snapshots") or {}).get(row.get("snapshot_id"))
    if not snapshot:
        raise HTTPException(422, "Run nemá validačný snapshot.")

    development = unpack_snapshot(snapshot)
    windows = []
    for boundary in row.get("validation_boundaries") or []:
        start = int(boundary["trading_start_index"])
        stop = int(boundary["stop_index"])
        candles = development[start:stop]
        metrics = buy_hold_risk_metrics(candles, profile, float(row["settings"]["initial_capital"]))
        windows.append({"window": boundary["window"], **metrics})

    wf_return_sum = round(sum(float(item["return_percent"]) for item in windows), 4)
    wf_max_dd = round(max((float(item["max_drawdown_percent"]) for item in windows), default=0.0), 4)
    wf_profit_dd = round(wf_return_sum / wf_max_dd, 4) if wf_max_dd > 0 else None

    holdout_from = result.get("holdout_from_ms")
    holdout_to = result.get("holdout_to_ms")
    if holdout_from is None or holdout_to is None:
        raise HTTPException(422, "Run nemá presné hranice holdoutu.")
    timeframe = row["timeframe"]
    step = TIMEFRAME_MILLISECONDS[timeframe]
    count = min(45000, max(100, math.ceil((int(holdout_to) - int(holdout_from) + 1) / step)))
    holdout_candles = await load_okx_candles(row["pair"], timeframe, count, int(holdout_from), int(holdout_to))
    holdout = buy_hold_risk_metrics(holdout_candles, profile, float(row["settings"]["initial_capital"]))
    stored_holdout_return = result.get("buy_hold_percent")
    return {
        "source_job_id": job_id,
        "pair": row["pair"],
        "timeframe": timeframe,
        "read_only": True,
        "grid_started": False,
        "cost_book_ts": profile.get("book_ts"),
        "wf_windows": windows,
        "wf": {
            "return_percent_sum_of_windows": wf_return_sum,
            "max_drawdown_percent": wf_max_dd,
            "profit_to_drawdown": wf_profit_dd,
        },
        "holdout": {
            **holdout,
            "profit_to_drawdown": round(float(holdout["return_percent"]) / float(holdout["max_drawdown_percent"]), 4)
                if float(holdout["max_drawdown_percent"]) > 0 else None,
            "stored_buy_hold_percent": stored_holdout_return,
            "return_matches_stored": stored_holdout_return is not None
                and abs(float(stored_holdout_return) - float(holdout["return_percent"])) < 1e-4,
        },
    }


@app.get("/api/optimizer/{job_id}/reaudit")
async def reaudit_optimizer_run(job_id: str):
    """Read-only deterministic audit of one stored optimizer run; never starts a grid."""
    stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=1")
    if not stored or stored[0].get("id") != job_id:
        raise HTTPException(404, "Optimizer run nebol nájdený.")
    record = stored[0]
    if record.get("status") != "completed":
        raise HTTPException(422, "Re-audit vyžaduje dokončený optimizer run.")
    result = record.get("result") or {}
    variants = result.get("variant_results") or []
    selected_variant_id = result.get("variant_id")
    row = next((item for item in variants if item.get("variant_id") == selected_variant_id), None)
    if row is None:
        raise HTTPException(422, "Uložený run nemá auditovateľný vybraný variant.")
    snapshot = (result.get("replay_snapshots") or {}).get(row.get("snapshot_id"))
    if not snapshot:
        raise HTTPException(422, "Uložený run nemá validačný snapshot.")

    data = unpack_snapshot(snapshot)
    settings = row["settings"]
    pair = row["pair"]
    timeframe = row["timeframe"]
    fee = float(row["cost_per_side"])
    cost_options = {"cost_models": {pair: row["cost_components"]}} if row.get("cost_components") else {}

    validation_windows, validation_tape = [], []
    for boundary in row.get("validation_boundaries") or []:
        run = simulate(
            {pair: data[boundary["warmup_start_index"]:boundary["stop_index"]]},
            settings,
            fee=fee,
            force_close_at_end=True,
            trading_start_time=boundary["trading_start_ms"],
            **cost_options,
        )
        validation_windows.append(run["metrics"])
        validation_tape.extend(trade_tape(run, pair, timeframe, row["variant_id"], boundary["window"], fee))

    validation_metrics = aggregate_metrics(validation_windows, float(settings["initial_capital"]))
    validation_matched = (
        _same_numeric_metrics(row.get("walk_forward_metrics") or {}, validation_metrics)
        and len(validation_windows) == len(row.get("validation_windows") or [])
        and all(
            _same_numeric_metrics(old, new)
            for old, new in zip(row.get("validation_windows") or [], validation_windows)
        )
        and len(validation_tape) == int(validation_metrics.get("closed_trades") or 0)
    )

    holdout_metrics = None
    holdout_tape: list[dict[str, Any]] = []
    holdout_matched = not bool(row.get("holdout_evaluated"))
    if row.get("holdout_evaluated"):
        holdout_from = result.get("holdout_from_ms")
        holdout_to = result.get("holdout_to_ms")
        if holdout_from is None or holdout_to is None:
            raise HTTPException(422, "Uložený finalist nemá presné hranice holdoutu.")
        step = TIMEFRAME_MILLISECONDS[timeframe]
        count = min(45000, max(100, math.ceil((int(holdout_to) - int(holdout_from) + 1) / step)))
        fetched = await load_okx_candles(pair, timeframe, count, int(holdout_from), int(holdout_to))
        warmup = max(40, int(settings["bb_period"]) + 2, int(settings["rsi_period"]) + 2, int(settings["atr_period"]) + 2)
        holdout_data = data[-warmup:] + fetched
        holdout_run = simulate(
            {pair: holdout_data},
            settings,
            fee=fee,
            force_close_at_end=True,
            trading_start_time=int(holdout_from),
            **cost_options,
        )
        holdout_metrics = holdout_run["metrics"]
        holdout_tape = trade_tape(holdout_run, pair, timeframe, row["variant_id"], "holdout", fee)
        holdout_matched = (
            _same_numeric_metrics(row.get("holdout_metrics") or {}, holdout_metrics)
            and len(holdout_tape) == int(holdout_metrics.get("closed_trades") or 0)
        )

    all_tape = validation_tape + holdout_tape
    matched = validation_matched and holdout_matched
    return {
        "status": "matched" if matched else "mismatch",
        "source_job_id": job_id,
        "pair": pair,
        "timeframe": timeframe,
        "variant_id": row["variant_id"],
        "validation_matched": validation_matched,
        "holdout_matched": holdout_matched,
        "validation_metrics": validation_metrics,
        "holdout_metrics": holdout_metrics,
        "validation_summary": tape_summary(validation_tape),
        "holdout_summary": tape_summary(holdout_tape) if holdout_tape else None,
        "summary": tape_summary(all_tape),
        "trades": all_tape if matched else [],
        "read_only": True,
        "grid_started": False,
    }


@app.post("/api/optimizer/previous-90d-oos")
async def run_previous_90d_oos(request: Previous90dOOSRequest):
    """One frozen 4h ZEC OOS run on the immediately preceding non-overlapping 90-day block."""
    require_durable_production_store()

    source_rows = await supabase_get("optimizer_runs", f"id=eq.{request.source_job_id}&limit=1")
    source = next((row for row in source_rows if row.get("id") == request.source_job_id), None)
    if source is None or source.get("status") != "completed":
        raise HTTPException(404, "Zdrojový completed run nebol nájdený.")

    source_result = source.get("result") or {}
    if not source_result.get("fixed_window"):
        raise HTTPException(422, "Zdrojový run nemá zmrazené historické okno.")

    variants = source_result.get("variant_results") or []
    selected_variant_id = source_result.get("variant_id")
    source_variant = next((item for item in variants if item.get("variant_id") == selected_variant_id), None)
    if source_variant is None:
        raise HTTPException(422, "Zdrojový run nemá auditovateľný vybraný variant.")
    if source_variant.get("pair") != "ZEC/USDT" or source_variant.get("timeframe") != "5m":
        raise HTTPException(422, "OOS protokol je zamknutý na ZEC/USDT · 5m.")

    source_settings = dict(source_variant.get("settings") or {})
    if float(source_settings.get("max_no_trail_hours") or 0) != 4:
        raise HTTPException(422, "OOS protokol vyžaduje zmrazený 4h baseline.")

    versions = await supabase_get("strategy_versions", f"id=eq.{request.version_id}&limit=10")
    version = next((row for row in versions if row.get("id") == request.version_id), None)
    if version is None:
        raise HTTPException(404, "OOS strategy version nebola nájdená.")
    version_settings = dict(version.get("settings") or {})
    version_settings.pop("_universe", None)
    if version_settings != source_settings:
        raise HTTPException(409, "OOS strategy version sa líši od zmrazeného 4h baseline.")

    source_from = int(source_result["fixed_history_from_ms"])
    source_to = int(source_result["fixed_history_to_ms"])
    window_ms = 90 * 86_400_000
    oos_from = source_from - window_ms
    oos_to = source_from - 1
    if oos_to >= source_from or oos_from >= oos_to:
        raise HTTPException(409, "Neplatné odvodené OOS okno.")

    pair, timeframe = "ZEC/USDT", "5m"
    step = TIMEFRAME_MILLISECONDS[timeframe]
    expected = window_ms // step
    candles = await load_okx_candles(pair, timeframe, int(expected), oos_from, oos_to)
    coverage = len(candles) / max(1, int(expected))
    if len(candles) < 200 or coverage < .995:
        raise HTTPException(409, f"OOS dáta nie sú úplné: {coverage:.2%}.")
    if int(candles[0]["open_time"]) != oos_from or int(candles[-1]["close_time"]) != oos_to:
        raise HTTPException(409, "OOS dataset nesedí na zmrazené hranice.")

    cost_profile = source_variant.get("cost_components")
    if not cost_profile:
        raise HTTPException(422, "Zdrojový run nemá fixný cost snapshot.")

    settings = StrategySettings(**version_settings).model_dump()
    result = await asyncio.to_thread(
        optimize,
        {(pair, timeframe): candles},
        settings,
        1,
        None,
        locked_pairs=[pair],
        cost_models={pair: cost_profile},
    )

    selected_id = result.get("variant_id")
    selected = next((item for item in (result.get("variant_results") or []) if item.get("variant_id") == selected_id), None)
    if selected is None:
        raise HTTPException(500, "OOS výsledok nemá auditovateľný variant.")

    development = unpack_snapshot(result["replay_snapshots"][selected["snapshot_id"]])
    bh_wf = []
    for boundary in selected.get("validation_boundaries") or []:
        validation = development[int(boundary["trading_start_index"]):int(boundary["stop_index"])]
        bh_wf.append({"window": boundary["window"], **buy_hold_risk_metrics(validation, cost_profile, float(settings["initial_capital"]))})

    holdout_boundary = max(120, int(len(candles) * .8))
    bh_holdout = buy_hold_risk_metrics(candles[holdout_boundary:], cost_profile, float(settings["initial_capital"]))
    stored_bh = result.get("buy_hold_percent")
    bh_holdout["stored_buy_hold_percent"] = stored_bh
    bh_holdout["return_matches_stored"] = stored_bh is not None and abs(float(stored_bh) - float(bh_holdout["return_percent"])) < 1e-4

    strategy_holdout_return = result.get("holdout_profit_percent")
    strategy_holdout_dd = (result.get("holdout_metrics") or {}).get("max_drawdown_percent")
    result.update({
        "source": "okx_fixed_previous_90d",
        "source_job_id": None,
        "study_source_job_id": request.source_job_id,
        "version_id": request.version_id,
        "pairs_ready": ["ZEC-USDT"],
        "pairs_dropped": [],
        "fixed_window": True,
        "fixed_history_from_ms": oos_from,
        "fixed_history_to_ms": oos_to,
        "fixed_cost_book_ts": cost_profile.get("book_ts"),
        "oos_previous_90d": True,
        "benchmark_risk": {
            "wf_windows": bh_wf,
            "holdout": bh_holdout,
            "strategy_holdout": {
                "return_percent": strategy_holdout_return,
                "max_drawdown_percent": strategy_holdout_dd,
                "profit_to_drawdown": round(float(strategy_holdout_return) / float(strategy_holdout_dd), 4)
                    if strategy_holdout_return is not None and strategy_holdout_dd not in (None, 0) else None,
            },
        },
    })

    now = datetime.now(UTC).isoformat()
    job_id = str(uuid4())
    result["source_job_id"] = job_id
    record = {
        "id": job_id,
        "status": "completed",
        "created_at": now,
        "finished_at": datetime.now(UTC).isoformat(),
        "request": {
            "kind": "previous_90d_oos_single",
            "source_job_id": request.source_job_id,
            "version_id": request.version_id,
            "pairs": [pair],
            "timeframes": [timeframe],
            "history_days": 90,
            "trials_per_market": 1,
            "settings": settings,
            "fixed_window": True,
            "start_time": oos_from,
            "end_time": oos_to,
        },
        "result": result,
    }
    saved = await supabase_upsert("optimizer_runs", record)
    return present_optimizer_record(saved)


@app.post("/api/optimizer/timeout-study")
async def run_timeout_study(request: TimeoutStudyRequest):
    """Run exactly one fixed-window timeout variant from an archived source run."""
    require_durable_production_store()

    source_rows = await supabase_get("optimizer_runs", f"id=eq.{request.source_job_id}&limit=1")
    source = next((row for row in source_rows if row.get("id") == request.source_job_id), None)
    if source is None or source.get("status") != "completed":
        raise HTTPException(404, "Zdrojový completed optimizer run nebol nájdený.")

    versions = await supabase_get("strategy_versions", f"id=eq.{request.version_id}&limit=10")
    version = next((row for row in versions if row.get("id") == request.version_id), None)
    if version is None:
        raise HTTPException(404, "Strategy version pre timeout study nebola nájdená.")

    source_result = source.get("result") or {}
    variants = source_result.get("variant_results") or []
    selected_variant_id = source_result.get("variant_id")
    source_variant = next((item for item in variants if item.get("variant_id") == selected_variant_id), None)
    if source_variant is None:
        raise HTTPException(422, "Zdrojový run nemá auditovateľný vybraný variant.")

    pair = source_variant.get("pair")
    timeframe = source_variant.get("timeframe")
    if pair != "ZEC/USDT" or timeframe != "5m":
        raise HTTPException(422, "Timeout study je uzamknutý na ZEC/USDT · 5m.")

    source_settings = dict(source_variant.get("settings") or {})
    version_settings = dict(version.get("settings") or {})
    version_settings.pop("_universe", None)
    expected_settings = {**source_settings, "max_no_trail_hours": request.max_no_trail_hours}
    if version_settings != expected_settings:
        raise HTTPException(409, "Strategy version sa líši od source runu aj v inom parametri než max_no_trail_hours.")

    snapshot = (source_result.get("replay_snapshots") or {}).get(source_variant.get("snapshot_id"))
    if not snapshot:
        raise HTTPException(422, "Zdrojový run nemá validačný snapshot.")
    development = unpack_snapshot(snapshot)

    holdout_from = source_result.get("holdout_from_ms")
    holdout_to = source_result.get("holdout_to_ms")
    if holdout_from is None or holdout_to is None:
        raise HTTPException(422, "Zdrojový run nemá presné hranice holdoutu.")

    step = TIMEFRAME_MILLISECONDS[timeframe]
    count = min(45000, max(100, math.ceil((int(holdout_to) - int(holdout_from) + 1) / step)))
    holdout = await load_okx_candles(pair, timeframe, count, int(holdout_from), int(holdout_to))
    full_data = development + holdout
    if not full_data or len({int(item["open_time"]) for item in full_data}) != len(full_data):
        raise HTTPException(409, "Fixed-window dataset nie je jednoznačný.")

    cost_profile = source_variant.get("cost_components")
    if not cost_profile:
        raise HTTPException(422, "Zdrojový run nemá fixný cost snapshot.")

    now = datetime.now(UTC).isoformat()
    job_id = str(uuid4())
    settings = StrategySettings(**version_settings).model_dump()
    result = await asyncio.to_thread(
        optimize,
        {(pair, timeframe): full_data},
        settings,
        1,
        None,
        locked_pairs=[pair],
        cost_models={pair: cost_profile},
    )
    result.update({
        "source": "okx_archived_window",
        "source_job_id": job_id,
        "study_source_job_id": request.source_job_id,
        "version_id": request.version_id,
        "pairs_ready": [pair.replace("/", "-")],
        "pairs_dropped": [],
        "fixed_window": True,
        "fixed_history_from_ms": int(full_data[0]["open_time"]),
        "fixed_history_to_ms": int(full_data[-1]["close_time"]),
        "fixed_cost_book_ts": cost_profile.get("book_ts"),
        "timeout_study": True,
    })
    record = {
        "id": job_id,
        "status": "completed",
        "created_at": now,
        "finished_at": datetime.now(UTC).isoformat(),
        "request": {
            "kind": "timeout_study_single",
            "source_job_id": request.source_job_id,
            "version_id": request.version_id,
            "pairs": [pair],
            "timeframes": [timeframe],
            "history_days": 90,
            "trials_per_market": 1,
            "max_no_trail_hours": request.max_no_trail_hours,
            "settings": settings,
            "fixed_window": True,
        },
        "result": result,
    }
    saved = await supabase_upsert("optimizer_runs", record)
    return present_optimizer_record(saved)


@app.get("/api/optimizer/{job_id}")
async def optimizer_status(job_id: str):
    if job_id in optimizer_jobs:
        return present_optimizer_record(optimizer_jobs[job_id])
    stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=1")
    if not stored:
        raise HTTPException(404, "Optimalizácia nebola nájdená.")
    return present_optimizer_record(stored[0])


def combine_optimizer_lock_results(
    results: list[dict[str, Any]],
    locked_pairs: list[str],
    version_id: str,
) -> dict[str, Any]:
    """Combine one completed optimizer result per locked coin into one durable lock result."""
    if not results:
        raise ValueError("Chýbajú výsledky optimalizácie.")

    def rank(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
        metrics = row.get("walk_forward_metrics") or {}
        closed = int(metrics.get("closed_trades") or 0)
        expectancy = metrics.get("expectancy")
        if expectancy is None:
            expectancy = (
                float(metrics.get("realized_profit") or 0) / closed
                if closed > 0
                else float("-inf")
            )
        payoff = metrics.get("payoff")
        drawdown = metrics.get("max_drawdown_percent")
        return (
            1.0 if row.get("qualified") else 0.0,
            1.0 if row.get("validation_passed") and closed >= 20 else 0.0,
            float(expectancy),
            float(payoff) if payoff is not None else float("-inf"),
            -float(drawdown) if drawdown is not None else float("-inf"),
        )

    rows = sorted(results, key=rank, reverse=True)
    selected = dict(rows[0])
    variant_results = [
        variant
        for row in rows
        for variant in (row.get("variant_results") or [])
    ]

    combined = {
        **selected,
        "version_id": version_id,
        "locked_pairs": list(locked_pairs),
        "tested_combinations": sum(
            int(row.get("tested_combinations") or 0) for row in rows
        ),
        "max_validation_trades": max(
            int(
                row.get("max_validation_trades")
                or (row.get("walk_forward_metrics") or {}).get("closed_trades")
                or 0
            )
            for row in rows
        ),
        "per_coin_results": [
            item
            for row in rows
            for item in (
                row.get("per_coin_results")
                or [{
                    "pair": row.get("pair"),
                    "timeframe": row.get("timeframe"),
                    "verdict": row.get("verdict"),
                    "walk_forward_metrics": row.get("walk_forward_metrics"),
                    "holdout_metrics": row.get("holdout_metrics"),
                }]
            )
        ],
        "variant_results": variant_results,
        "pairs_ready": list(dict.fromkeys(
            pair
            for row in rows
            for pair in (row.get("pairs_ready") or [])
        )),
        "pairs_dropped": [
            item
            for row in rows
            for item in (row.get("pairs_dropped") or [])
        ],
        "replay_snapshots": {
            key: value
            for row in rows
            for key, value in (row.get("replay_snapshots") or {}).items()
        },
        "cost_profiles": {
            key: value
            for row in rows
            for key, value in (row.get("cost_profiles") or {}).items()
        },
        "source_job_ids": list(dict.fromkeys(
            row["source_job_id"]
            for row in rows
            if row.get("source_job_id")
        )),
        "source_job_id": None,
        "aggregate_lock_result": True,
        "all_variants_insufficient_trades": bool(variant_results) and all(
            "malo_obchodov" in (row.get("rejection_reasons") or [])
            for row in variant_results
        ),
    }

    qualified = bool(selected.get("qualified"))
    combined["qualified"] = qualified
    combined["winner"] = selected.get("winner") if qualified else None
    combined["strategy_code"] = selected.get("strategy_code") if qualified else None
    combined["job_verdict"] = "KANDIDÁT" if qualified else "ŽIADNY PLATNÝ VARIANT"
    return combined


@app.post("/api/optimizer/finalize")
async def finalize_optimizer(request: OptimizerFinalizeRequest):
    """Persist exactly one aggregate result for the currently locked universe."""
    require_durable_production_store()

    versions = await supabase_get(
        "strategy_versions",
        "order=created_at.desc&limit=50",
    )
    lock = active_universe(universe_version(versions))
    if not lock or request.version_id != lock["version_id"]:
        raise HTTPException(
            409,
            "Zamknutá verzia sa zmenila. Finálny výsledok sa neuložil.",
        )

    source_job_ids = list(dict.fromkeys(request.source_job_ids))
    if len(source_job_ids) != len(lock["pairs"]):
        raise HTTPException(
            422,
            "Finálny výsledok musí obsahovať presne jeden beh pre každý coin locku.",
        )

    records: list[dict[str, Any]] = []
    for job_id in source_job_ids:
        stored = await supabase_get(
            "optimizer_runs",
            f"id=eq.{job_id}&limit=1",
        )
        if not stored:
            raise HTTPException(404, f"Optimizer run {job_id} nebol nájdený.")

        record = stored[0]
        if record.get("status") != "completed":
            raise HTTPException(422, "Nie všetky optimizer runy sú dokončené.")

        result = record.get("result") or {}
        if result.get("version_id") != request.version_id:
            raise HTTPException(409, "Optimizer run patrí inej verzii locku.")

        pairs = (record.get("request") or {}).get("pairs") or []
        if len(pairs) != 1:
            raise HTTPException(
                422,
                "Každý zdrojový optimizer run musí patriť presne jednému coinu.",
            )
        records.append(record)

    covered_pairs = [
        (record.get("request") or {}).get("pairs", [None])[0]
        for record in records
    ]
    if (
        len(set(covered_pairs)) != len(lock["pairs"])
        or set(covered_pairs) != set(lock["pairs"])
    ):
        raise HTTPException(
            422,
            "Zdrojové optimizer runy nepokrývajú presne aktuálny lock.",
        )

    recent = await supabase_get(
        "optimizer_runs",
        "order=finished_at.desc&limit=100",
    )
    source_key = sorted(source_job_ids)
    existing = next(
        (
            row
            for row in recent
            if (row.get("request") or {}).get("kind") == "aggregate"
            and (row.get("request") or {}).get("version_id") == request.version_id
            and sorted(
                (row.get("request") or {}).get("source_job_ids") or []
            ) == source_key
        ),
        None,
    )
    if existing:
        return present_optimizer_record(existing)

    combined = combine_optimizer_lock_results(
        [record["result"] for record in records],
        lock["pairs"],
        request.version_id,
    )
    combined["source_job_ids"] = source_job_ids

    now = datetime.now(UTC).isoformat()
    aggregate_record = {
        "id": str(uuid4()),
        "status": "completed",
        "created_at": now,
        "finished_at": now,
        "request": {
            "kind": "aggregate",
            "version_id": request.version_id,
            "source_job_ids": source_job_ids,
        },
        "result": combined,
    }
    saved = await supabase_upsert("optimizer_runs", aggregate_record)
    return present_optimizer_record(saved)


@app.get("/api/optimizer-latest")
async def optimizer_latest():
    context = await strategy_context()
    lock = context.get("active_universe")
    if not lock:
        return None

    stored = await supabase_get(
        "optimizer_runs",
        "order=finished_at.desc&limit=100",
    )
    aggregate = next(
        (
            row
            for row in stored
            if (row.get("request") or {}).get("kind") == "aggregate"
            and (row.get("result") or {}).get("version_id") == lock["version_id"]
        ),
        None,
    )
    return present_optimizer_record(aggregate) if aggregate else None


@app.get("/api/dashboard")
async def dashboard():
    try:
        context = await strategy_context()
        trades = await supabase_get("simulated_trades", "order=opened_at.desc&limit=30")
        sync = await supabase_get("sync_events", "order=received_at.desc&limit=1")
        diagnostics = await supabase_get("signal_diagnostics", "order=occurred_at.desc&limit=500")
        simulation_runs = await supabase_get("simulation_runs", "order=finished_at.desc&limit=1")
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Supabase is unavailable: {error}")
    total = sum(float(t.get("profit_usdt") or 0) for t in trades if t.get("status") == "closed")
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = sum(1 for t in closed if float(t.get("profit_usdt") or 0) > 0)
    initial_capital = context["strategy"]["initial_capital"]
    equity = float(initial_capital)
    peak = equity
    max_drawdown_percent = 0.0
    equity_curve = [{"time": "start", "value": round(equity, 4)}]
    for trade in sorted(closed, key=lambda item: item.get("closed_at") or item.get("opened_at") or ""):
        equity += float(trade.get("profit_usdt") or 0)
        peak = max(peak, equity)
        max_drawdown_percent = max(max_drawdown_percent, ((peak - equity) / peak * 100) if peak else 0)
        equity_curve.append({"time": trade.get("closed_at") or trade.get("opened_at"), "value": round(equity, 4)})
    rejections: dict[str, int] = {}
    for diagnostic in diagnostics:
        if diagnostic.get("decision") == "rejected":
            reason = diagnostic.get("reason", "Neznámy dôvod")
            rejections[reason] = rejections.get(reason, 0) + 1
    return {"mode": "simulation", **context, "trades": trades, "last_sync": sync[0] if sync else None, "last_simulation": simulation_runs[0] if simulation_runs else None, "analytics": {"equity_curve": equity_curve, "max_drawdown_percent": round(max_drawdown_percent, 2), "rejections": rejections}, "metrics": {"initial_capital": initial_capital, "realized_profit": round(total, 4), "closed_trades": len(closed), "win_rate": round((wins / len(closed) * 100) if closed else 0, 1), "open_trades": sum(1 for t in trades if t.get("status") == "open")}}


@app.put("/api/strategy")
async def save_strategy(settings: StrategySettings):
    require_durable_production_store()
    versions = await supabase_get("strategy_versions", "order=created_at.desc&limit=50")
    lock = active_universe(universe_version(versions))
    if lock and set(settings.selected_pairs) != set(lock["pairs"]):
        raise HTTPException(409, f"Zoznam coinov je uzamknutý do {lock['expires_at']}. Parametre stratégie môžeš meniť, universe zatiaľ nie.")
    record = {"id": "default", "settings": settings.model_dump(), "updated_at": datetime.now(UTC).isoformat()}
    try:
        await supabase_upsert("strategy_settings", record)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Nastavenie sa nepodarilo uložiť: {error}")
    return {"saved": True, "strategy": settings}


@app.get("/api/strategy-versions")
async def list_strategy_versions():
    try:
        return await supabase_get("strategy_versions", "order=created_at.desc&limit=50")
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Verzie sa nepodarilo načítať: {error}")


@app.post("/api/strategy-versions")
async def create_strategy_version(version: StrategyVersionCreate):
    require_durable_production_store()
    record = {
        "id": str(uuid4()), "name": version.name, "note": version.note,
        "settings": version.settings.model_dump() | ({"_universe": version.universe} if version.universe else {}), "created_at": datetime.now(UTC).isoformat(),
    }
    if version.universe:
        lock = active_universe(record)
        if not lock or set(lock["pairs"]) != set(version.settings.selected_pairs):
            raise HTTPException(422, "Návrh nemá platný zámok alebo sa jeho coiny nezhodujú s verziou.")
    try:
        saved = await supabase_upsert("strategy_versions", record)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Verziu sa nepodarilo uložiť: {error}")
    # Saving this single version also activates its universe. No second write
    # or separate HTTP request is needed to make the selection authoritative.
    return {"saved": True, "version": saved, "strategy": version.settings.model_dump(), "active_universe": active_universe(saved)}


@app.get("/api/backtests")
async def list_backtests():
    try:
        return await supabase_get("backtest_runs", "order=created_at.desc&limit=100")
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Backtesty sa nepodarilo načítať: {error}")


@app.post("/api/backtests")
async def record_backtest(result: BacktestResultCreate):
    """Store a completed backtest. The caller supplies results; this endpoint never executes trades."""
    record = {
        "id": str(uuid4()), "strategy_version_id": result.strategy_version_id,
        "timerange": result.timerange, "data_source": result.data_source,
        "metrics": result.metrics, "report": result.report,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        saved = await supabase_upsert("backtest_runs", record)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Backtest sa nepodarilo uložiť: {error}")
    return {"saved": True, "backtest": saved}


@app.get("/api/backtests/compare")
async def compare_backtests(left: str, right: str):
    """Compare two archived simulations; values are never used to trade."""
    try:
        results = await supabase_get("backtest_runs", f"id=in.({left},{right})")
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Backtesty sa nepodarilo načítať: {error}")
    indexed = {item["id"]: item for item in results}
    if left not in indexed or right not in indexed:
        raise HTTPException(404, "Jedna alebo obe verzie backtestu neexistujú.")
    baseline, candidate = indexed[left], indexed[right]
    baseline_metrics, candidate_metrics = baseline.get("metrics", {}), candidate.get("metrics", {})
    keys = ("profit_total_usdt", "profit_total_percent", "total_trades", "win_rate_percent", "max_drawdown_percent")
    difference = {}
    for key in keys:
        first, second = baseline_metrics.get(key), candidate_metrics.get(key)
        if isinstance(first, (float, int)) and isinstance(second, (float, int)):
            difference[key] = round(second - first, 4)
        else:
            difference[key] = None
    return {"baseline": baseline, "candidate": candidate, "difference": difference}


@app.post("/api/export/freqtrade")
async def export_freqtrade(settings: StrategySettings):
    """Export a research configuration only from a verified stored candidate."""
    records = await supabase_get("optimizer_runs", "order=finished_at.desc&limit=100")
    context = await strategy_context()
    lock = context["active_universe"]
    candidate = None
    for record in records:
        row = (present_optimizer_record(record) or {}).get("result") or {}
        if lock and row.get("version_id") == lock["version_id"] and row.get("selection_policy_version") == 3 and row.get("qualified") and row.get("winner") and row.get("holdout_evaluated") and assess_candidate(row, row.get("locked_pairs", []))["qualified"]:
            candidate = row
            break
    if candidate is None:
        raise HTTPException(409, "Freqtrade export je vypnutý: žiadny KANDIDÁT. Tento lock ostáva NEPREŠIEL.")
    settings = StrategySettings(**candidate["settings"])
    return {
        "strategy": "RevoltisAIOptimized_v1", "timeframe": settings.timeframe,
        "dry_run": True, "dry_run_wallet": settings.initial_capital,
        "initial_state": "stopped", "trading_mode": "spot", "fee": FEE_TAKER,
        "stake_currency": "USDT", "stake_amount": settings.stake_amount,
        "max_open_trades": settings.max_open_trades,
        "exchange": {"name": "okx", "pair_whitelist": settings.selected_pairs, "pair_blacklist": []},
        "revoltis_parameters": settings.model_dump(exclude={"selected_pairs", "timeframe", "initial_capital", "stake_amount", "max_open_trades"}),
        "safety": {"live_trading": False, "requires_backtest_before_dry_run": True, "purpose": "download-data_and_backtesting_export_trades",
                   "fee_note": "fee 0.001 je taker 0,10 % za stranu. Spread a impact nie sú zahrnuté vo Freqtrade fee."},
    }


@app.post("/api/ingest/freqtrade")
async def ingest_freqtrade(batch: SyncBatch, x_revoltis_sync_token: str | None = Header(default=None)):
    """Receive dry-run results. This endpoint never accepts an exchange key or order instruction."""
    configured_token = os.getenv("REVOLTIS_SYNC_TOKEN")
    if configured_token and x_revoltis_sync_token != configured_token:
        raise HTTPException(401, "Neplatný synchronizačný token.")
    try:
        records = [trade.model_dump() for trade in batch.trades]
        await supabase_upsert_many("simulated_trades", records)
        await supabase_upsert("sync_events", {
            "id": batch.source,
            "source": batch.source,
            "received_at": datetime.now(UTC).isoformat(),
            "trade_count": len(records),
        })
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Import sa nepodaril: {error}")
    return {"mode": "simulation", "imported": len(batch.trades)}


@app.post("/api/signal-diagnostics")
async def record_signal_diagnostic(diagnostic: SignalDiagnostic):
    """Record a simulation decision for later analysis; no order is created here."""
    record = diagnostic.model_dump()
    record["id"] = record["id"] or str(uuid4())
    try:
        saved = await supabase_upsert("signal_diagnostics", record)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Diagnostiku sa nepodarilo uložiť: {error}")
    return {"saved": True, "diagnostic": saved}
