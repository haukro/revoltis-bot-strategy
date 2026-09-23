from __future__ import annotations

import os
import re
import asyncio
import math
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
from .optimizer import optimize, present_optimizer_record, assess_candidate
from .trade_audit import matches_target, prepare_replay, replay_validation
from .costs import book_costs, FEE_SCHEDULE, FEE_TAKER, FEE_MAKER


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
okx_history_semaphore = asyncio.Semaphore(1)
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
                    async with okx_history_rate_lock:
                        elapsed = asyncio.get_running_loop().time() - okx_history_last_request
                        if elapsed < OKX_HISTORY_MIN_INTERVAL:
                            await asyncio.sleep(OKX_HISTORY_MIN_INTERVAL - elapsed)
                        response = await client.get(url, params=params)
                        okx_history_last_request = asyncio.get_running_loop().time()
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

            # The current-candles endpoint reduces the number of historical pages.
            recent = await request_page(OKX_CANDLES_URL, {"instId": instrument, "bar": timeframe, "limit": min(300, int(expected))})
            batches.append(recent)
            cursor = min((int(row[0]) for row in recent), default=end + step)
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
        for number, (pair, timeframe) in enumerate(markets, 1):
            count = min(45000, max(100, request.history_days * 86_400_000 // TIMEFRAME_MILLISECONDS[timeframe]))
            cache_state: dict[str, Any] = {}
            job.update({"phase": "fetching_candles", "bar": timeframe, "inst_id": pair.replace("/", "-"), "message": f"Načítavam {pair} · {timeframe}", "progress": round(number / max(1, len(markets)) * 30), "cached": False, "retries_429": 0})
            def download_progress(page: int, total: int, current_pair: str = pair, current_timeframe: str = timeframe) -> None:
                job.update({"phase": "fetching_candles", "bar": current_timeframe, "inst_id": current_pair.replace("/", "-"), "page": page, "pages_est": total, "message": f"Sťahujem {current_pair} · {current_timeframe}: {page}/{total} strán", "cached": bool(cache_state.get("cached")), "retries_429": int(cache_state.get("retries_429", 0))})
            try:
                candles = await load_okx_candles(pair, timeframe, int(count), start_time, end_time, download_progress, cache_state)
                coverage = len(candles) / max(1, int(count))
                coverage_by_pair.setdefault(pair, {})[timeframe] = {"candles": len(candles), "expected": int(count), "coverage": round(coverage, 4), "cached": bool(cache_state.get("cached")), "retries_429": int(cache_state.get("retries_429", 0))}
                if len(candles) >= 100 and coverage >= .95:
                    candle_sets[(pair, timeframe)] = candles
                else:
                    download_errors.append({"pair": pair, "timeframe": timeframe, "reason": f"neúplné dáta ({coverage:.1%})"})
            except Exception as error:
                coverage_by_pair.setdefault(pair, {})[timeframe] = {"candles": 0, "expected": int(count), "coverage": 0, "cached": bool(cache_state.get("cached")), "retries_429": int(cache_state.get("retries_429", 0))}
                download_errors.append({"pair": pair, "timeframe": timeframe, "reason": str(error)[:180]})
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
