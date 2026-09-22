from __future__ import annotations

import os
import re
import asyncio
from uuid import uuid4
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .local_store import LocalStore
from .simulation import simulate
from .optimizer import optimize


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


class OptimizerRequest(BaseModel):
    settings: StrategySettings
    pairs: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    timeframes: list[Literal["1m", "3m", "5m", "15m"]] = Field(default_factory=lambda: ["1m", "3m", "5m", "15m"])
    history_days: Literal[1, 7, 14, 30] = 7
    trials_per_market: int = Field(default=3, ge=1, le=8)


DEFAULT = StrategySettings()
local_store = LocalStore()
app = FastAPI(title="Revoltis Bot Strategy API", version="1.0.0")
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
# Binance's dedicated market-data host serves public candles without API keys
# and is suitable for cloud regions where the trading API returns HTTP 451.
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}
TIMEFRAME_MILLISECONDS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
optimizer_jobs: dict[str, dict[str, Any]] = {}


def supabase_headers() -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}


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


@app.get("/api/health")
async def health():
    configured = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    return {"status": "ok", "mode": "supabase" if configured else "demo", "live_trading": False}


@app.get("/api/market/candles")
async def market_candles(pair: str, timeframe: str = "3m", limit: int = 480):
    """Read public Binance spot candles. This endpoint cannot place orders."""
    symbol = pair.replace("/", "").upper()
    if not re.fullmatch(r"[A-Z0-9]{5,20}", symbol):
        raise HTTPException(422, "Neplatný obchodný pár.")
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(422, "Nepodporovaný interval sviečok.")
    if not 10 <= limit <= 1500:
        raise HTTPException(422, "Počet sviečok musí byť 10 až 1500.")
    try:
        # Ask by timestamp, not only by count, to guarantee a complete last 24 h
        # view for 1-minute candles (which needs more than Binance's 1,000-row page).
        minutes = int(timeframe.removesuffix("m")) if timeframe.endswith("m") else 60
        end_time = int(datetime.now(UTC).timestamp() * 1000)
        start_time = end_time - (limit * minutes * 60 * 1000)
        candles = await load_binance_candles(pair, timeframe, limit, start_time, end_time)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Dáta z Binance nie sú dostupné: {error}")
    return {"source": "Binance public spot API", "pair": pair.upper(), "timeframe": timeframe, "candles": candles, "live_trading": False}


async def load_binance_candles(pair: str, timeframe: str, limit: int, start_time: int | None = None, end_time: int | None = None) -> list[dict[str, Any]]:
    symbol = pair.replace("/", "").upper()
    # Binance sends at most 1,000 candles per response, so collect pages.
    remaining, cursor, rows = limit, start_time, []
    async with httpx.AsyncClient(timeout=12) as client:
        while remaining > 0:
            parameters: dict[str, Any] = {"symbol": symbol, "interval": timeframe, "limit": min(1000, remaining)}
            if cursor is not None:
                parameters["startTime"] = cursor
            if end_time is not None:
                parameters["endTime"] = end_time
            response = await client.get(BINANCE_KLINES_URL, params=parameters)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            remaining -= len(batch)
            if len(batch) < parameters["limit"] or cursor is None:
                break
            cursor = int(batch[-1][6]) + 1
            if end_time is not None and cursor >= end_time:
                break
    return [{"open_time": row[0], "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "close_time": row[6]} for row in rows]


@app.post("/api/simulations/run")
async def run_standalone_simulation(request: SimulationRequest):
    """Run a complete backtest from public Binance candles without Freqtrade."""
    settings = request.settings.model_dump()
    pairs = settings["selected_pairs"]
    if not pairs:
        raise HTTPException(422, "Vyber aspoň jeden coin.")
    if request.start_time and request.end_time and request.start_time >= request.end_time:
        raise HTTPException(422, "Začiatok testu musí byť pred koncom testu.")
    try:
        candle_sets = await asyncio.gather(*(load_binance_candles(pair, settings["timeframe"], request.candle_limit, request.start_time, request.end_time) for pair in pairs))
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Simuláciu nebolo možné načítať z Binance: {error}")
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
    result = simulate(dict(zip(pairs, candle_sets)), settings)
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
    return {"source": "Binance public spot API", "timeframe": settings["timeframe"], "mode": "simulation", "live_trading": False, "data_coverage": data_coverage, "run": record, **result}


async def run_optimizer_job(job_id: str, request: OptimizerRequest) -> None:
    job = optimizer_jobs[job_id]
    try:
        end_time = int(datetime.now(UTC).timestamp() * 1000)
        start_time = end_time - request.history_days * 86_400_000
        candle_sets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        markets = [(pair, timeframe) for pair in request.pairs for timeframe in request.timeframes]
        for number, (pair, timeframe) in enumerate(markets, 1):
            count = min(45000, max(100, request.history_days * 86_400_000 // TIMEFRAME_MILLISECONDS[timeframe]))
            job.update({"phase": "downloading", "message": f"Načítavam {pair} · {timeframe}", "progress": round(number / max(1, len(markets)) * 30)})
            candles = await load_binance_candles(pair, timeframe, int(count), start_time, end_time)
            if len(candles) >= 100:
                candle_sets[(pair, timeframe)] = candles
        if not candle_sets:
            raise ValueError("Pre optimalizáciu sa nepodarilo načítať dostatok sviečok.")

        loop = asyncio.get_running_loop()
        def update_progress(done: int, total: int, market: str) -> None:
            loop.call_soon_threadsafe(job.update, {"phase": "testing", "message": f"Testujem {market}", "progress": 30 + round(done / total * 68), "tested": done, "total": total})
        result = await asyncio.to_thread(optimize, candle_sets, request.settings.model_dump(), request.trials_per_market, update_progress)
        now = datetime.now(UTC).isoformat()
        record = {"id": job_id, "status": "completed", "created_at": job["created_at"], "finished_at": now, "request": request.model_dump(), "result": result}
        await supabase_upsert("optimizer_runs", record)
        job.update({"status": "completed", "phase": "completed", "progress": 100, "message": "Optimalizácia dokončená", "result": result, "finished_at": now})
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


@app.get("/api/optimizer/{job_id}")
async def optimizer_status(job_id: str):
    if job_id in optimizer_jobs:
        return optimizer_jobs[job_id]
    stored = await supabase_get("optimizer_runs", f"id=eq.{job_id}&limit=1")
    if not stored:
        raise HTTPException(404, "Optimalizácia nebola nájdená.")
    return stored[0]


@app.get("/api/optimizer-latest")
async def optimizer_latest():
    stored = await supabase_get("optimizer_runs", "order=finished_at.desc&limit=1")
    return stored[0] if stored else None


@app.get("/api/dashboard")
async def dashboard():
    try:
        settings = await supabase_get("strategy_settings", "order=updated_at.desc&limit=1")
        trades = await supabase_get("simulated_trades", "order=opened_at.desc&limit=30")
        sync = await supabase_get("sync_events", "order=received_at.desc&limit=1")
        diagnostics = await supabase_get("signal_diagnostics", "order=occurred_at.desc&limit=500")
        simulation_runs = await supabase_get("simulation_runs", "order=finished_at.desc&limit=1")
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Supabase is unavailable: {error}")
    total = sum(float(t.get("profit_usdt") or 0) for t in trades if t.get("status") == "closed")
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = sum(1 for t in closed if float(t.get("profit_usdt") or 0) > 0)
    initial_capital = settings[0].get("settings", {}).get("initial_capital", 100) if settings else 100
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
    return {"mode": "simulation", "strategy": settings[0].get("settings", DEFAULT.model_dump()) if settings else DEFAULT.model_dump(), "trades": trades, "last_sync": sync[0] if sync else None, "last_simulation": simulation_runs[0] if simulation_runs else None, "analytics": {"equity_curve": equity_curve, "max_drawdown_percent": round(max_drawdown_percent, 2), "rejections": rejections}, "metrics": {"initial_capital": initial_capital, "realized_profit": round(total, 4), "closed_trades": len(closed), "win_rate": round((wins / len(closed) * 100) if closed else 0, 1), "open_trades": sum(1 for t in trades if t.get("status") == "open")}}


@app.put("/api/strategy")
async def save_strategy(settings: StrategySettings):
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
    record = {
        "id": str(uuid4()), "name": version.name, "note": version.note,
        "settings": version.settings.model_dump(), "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        saved = await supabase_upsert("strategy_versions", record)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Verziu sa nepodarilo uložiť: {error}")
    return {"saved": True, "version": saved}


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
    """Export dry-run-only settings for the existing Revoltis strategy."""
    return {
        "strategy": "RevoltisVolatility_vNext", "timeframe": settings.timeframe,
        "dry_run": True, "dry_run_wallet": settings.initial_capital,
        "stake_currency": "USDT", "stake_amount": settings.stake_amount,
        "max_open_trades": settings.max_open_trades,
        "exchange": {"name": "binance", "pair_whitelist": settings.selected_pairs, "pair_blacklist": []},
        "revoltis_parameters": settings.model_dump(exclude={"selected_pairs", "timeframe", "initial_capital", "stake_amount", "max_open_trades"}),
        "safety": {"live_trading": False, "requires_backtest_before_dry_run": True},
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
