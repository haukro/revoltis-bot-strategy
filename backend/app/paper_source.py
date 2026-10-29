"""Confirmed 5m source data for the separate paper measurement path.

The legacy loader drops confirm and deduplicates rows, so it cannot certify
this path. Cost arithmetic is the unchanged costs.book_costs implementation.
"""
import asyncio
import time

import httpx

from .costs import book_costs
from .paper_measurement import MeasurementError, STEP_MS


class PaperMarket:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _data(self, path, **params):
        response = await self.client.get("https://www.okx.com/api/v5/market/" + path, params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
            raise MeasurementError("market_data_unavailable")
        return payload["data"]

    @staticmethod
    def _candle(row):
        if not isinstance(row, list) or len(row) != 9 or row[8] not in ("0", "1"):
            raise MeasurementError("missing_candle_confirmation")
        opened = int(row[0])
        return {"open_time_ms": opened, "close_time_raw_ms": opened + STEP_MS - 1,
                "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                "close": float(row[4]), "volume": float(row[5]), "quote_volume": float(row[7]),
                "confirm": int(row[8])}

    async def latest(self, pair):
        rows = [self._candle(row) for row in await self._data(
            "candles", instId=pair.replace("/", "-"), bar="5m", limit=3)]
        closed = [row for row in rows if row["confirm"] == 1]
        if not closed:
            raise MeasurementError("no_closed_candle")
        return max(row["open_time_ms"] for row in closed)

    async def candles(self, pair, window):
        rows, cursor = [], window.last_open_ms + STEP_MS
        # Bounded work. Exhaustion is a coverage error, never a truncated success.
        for _ in range(450):
            batch = [self._candle(row) for row in await self._data(
                "history-candles", instId=pair.replace("/", "-"), bar="5m", after=cursor, limit=100)]
            if not batch:
                return rows
            # Keep duplicates for closed_only to reject, rather than hiding them.
            rows.extend(row for row in batch if row["open_time_ms"] >= window.first_open_ms)
            next_cursor = min(row["open_time_ms"] for row in batch)
            if next_cursor >= cursor:
                raise MeasurementError("incomplete_candles")
            if next_cursor <= window.first_open_ms:
                return rows
            cursor = next_cursor
            await asyncio.sleep(.16)
        raise MeasurementError("incomplete_candles")

    async def cost(self, pair, notional):
        rows = await self._data("books", instId=pair.replace("/", "-"), sz=400)
        fetched = time.time_ns() // 1_000_000
        if len(rows) != 1:
            raise MeasurementError("cost_profile_unavailable")
        return {"pair": pair, **book_costs(rows[0], notional)}, fetched
