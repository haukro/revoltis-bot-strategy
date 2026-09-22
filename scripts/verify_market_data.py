"""Read-only verification of Binance candle availability and range calculations."""
from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import StrategySettings, load_binance_candles  # noqa: E402
from app.simulation import simulate  # noqa: E402


PAIRS = [
    "PEPE/USDT", "DOGE/USDT", "WIF/USDT", "BONK/USDT", "SUI/USDT",
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "AVAX/USDT", "LINK/USDT", "XEC/USDT", "ZEC/USDT",
]
TIMEFRAMES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}
RANGES = {"1 h": 1, "6 h": 6, "24 h": 24, "7 dní": 168, "14 dní": 336, "30 dní": 720}
MAX_CANDLES = 45_000
URL = "https://data-api.binance.vision/api/v3/klines"


def validate_rows(rows: list, expected_gap_ms: int) -> None:
    if len(rows) < 3:
        raise AssertionError("Binance nevrátil aspoň tri sviečky.")
    previous_open = None
    for row in rows:
        open_time, open_price, high, low, close = int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise AssertionError("Neplatné OHLC hodnoty.")
        if previous_open is not None and open_time - previous_open != expected_gap_ms:
            raise AssertionError("Sviečky nie sú časovo súvislé.")
        previous_open = open_time


async def check_pair(client: httpx.AsyncClient, pair: str, timeframe: str, minutes: int, semaphore: asyncio.Semaphore) -> tuple[str, str]:
    async with semaphore:
        response = await client.get(URL, params={"symbol": pair.replace("/", ""), "interval": timeframe, "limit": 3})
        response.raise_for_status()
        validate_rows(response.json(), minutes * 60_000)
        return pair, timeframe


async def main() -> None:
    for label, hours in RANGES.items():
        for timeframe, minutes in TIMEFRAMES.items():
            count = (hours * 60 + minutes - 1) // minutes
            if count > MAX_CANDLES:
                raise AssertionError(f"{label}/{timeframe}: {count} presahuje limit {MAX_CANDLES}.")

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=20) as client:
        checks = [check_pair(client, pair, timeframe, minutes, semaphore) for pair in PAIRS for timeframe, minutes in TIMEFRAMES.items()]
        verified = await asyncio.gather(*checks)

    # This exercises pagination over Binance's 1,000-row response limit.
    end_ms = 1_757_808_000_000  # fixed UTC minute for reproducible boundaries
    start_ms = end_ms - 1_440 * 60_000
    paged = await load_binance_candles("BTC/USDT", "1m", 1_440, start_ms, end_ms)
    if len(paged) not in (1_440, 1_441):
        raise AssertionError(f"24 h stránkovanie vrátilo {len(paged)} sviečok namiesto približne 1440.")

    def synthetic_candles(count: int, offset: int = 0) -> list[dict]:
        rows = []
        for index in range(count):
            base = 100 + math.sin(index / 8) * 3 + math.sin(index / 41) * 2
            close = base + math.sin(index / 3) * .4
            rows.append({"open_time": (offset + index) * 60_000, "open": base, "high": max(base, close) + .35, "low": min(base, close) - .35, "close": close, "volume": 1_000_000, "close_time": (offset + index + 1) * 60_000 - 1})
        return rows

    settings = StrategySettings(selected_pairs=PAIRS, timeframe="1m", min_quote_volume_usdt=0, min_volume_ratio=.1).model_dump()
    small_result = simulate({pair: synthetic_candles(500, pair_index * 10_000) for pair_index, pair in enumerate(PAIRS)}, settings)
    if set(small_result["per_pair_metrics"]) != set(PAIRS):
        raise AssertionError("Výsledok neobsahuje samostatné metriky pre všetky coiny.")
    pair_trade_count = sum(int(metrics["closed_trades"]) for metrics in small_result["per_pair_metrics"].values())
    if pair_trade_count != int(small_result["metrics"]["closed_trades"]):
        raise AssertionError("Súčet obchodov jednotlivých coinov nesedí s celkovým výsledkom.")
    pair_profit = round(sum(float(metrics["realized_profit"]) for metrics in small_result["per_pair_metrics"].values()), 3)
    global_profit = round(float(small_result["metrics"]["realized_profit"]), 3)
    if pair_profit != global_profit:
        raise AssertionError("Súčet zisku jednotlivých coinov nesedí s celkovým výsledkom.")

    benchmark_settings = StrategySettings(selected_pairs=["BTC/USDT"], timeframe="1m", min_quote_volume_usdt=0, min_volume_ratio=.1).model_dump()
    benchmark_start = time.perf_counter()
    simulate({"BTC/USDT": synthetic_candles(43_200)}, benchmark_settings)
    benchmark_seconds = time.perf_counter() - benchmark_start

    print(f"OK: {len(verified)} kombinácií coin/interval, {len(RANGES) * len(TIMEFRAMES)} výpočtov rozsahu, stránkovanie 24 h = {len(paged)} sviečok, metriky {len(PAIRS)} coinov sú konzistentné, 30 d/1m prepočet jedného coinu = {benchmark_seconds:.2f} s.")


if __name__ == "__main__":
    asyncio.run(main())
