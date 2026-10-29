"""Disposable P4 HTTP fixture: real worker/engine, stable synthetic market."""
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI
import httpx

from app.costs import book_costs
from app.paper_measurement import STEP_MS
from app.paper_routes import PaperConfig, make_paper_router
from app.paper_worker import PaperRPC, PaperWorker


class StableMarket:
    async def latest(self, pair):
        boundary = time.time_ns() // 1_000_000 // STEP_MS * STEP_MS
        return boundary - (2 if pair.startswith('P4LAG') else 1) * STEP_MS

    async def candles(self, pair, window):
        # Indices are anchored to the immutable first open, never to the newest
        # candle. Extending the horizon cannot rewrite previously archived data.
        rows = []
        for i in range(window.expected_count):
            price = 100.
            if pair.startswith('P4SIGNAL') and i < 1200:
                price = {25: 90., 26: 91., 27: 80.}.get(i % 30, 100.)
            rows.append({'open_time_ms': window.first_open_ms + i * STEP_MS,
                'close_time_raw_ms': window.first_open_ms + (i + 1) * STEP_MS - 1,
                'open': price * .999, 'high': price * 1.002, 'low': price * .998,
                'close': price, 'volume': 1_000_000., 'quote_volume': price * 1_000_000.,
                'confirm': 1})
        return rows[:-1] if pair.startswith('P4GAP') else rows

    async def cost(self, pair, notional):
        fetched = time.time_ns() // 1_000_000
        return book_costs({'asks': [['100.1', '100']], 'bids': [['99.9', '100']],
                           'ts': str(fetched)}, notional), fetched


@asynccontextmanager
async def factory(config):
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        yield PaperWorker(PaperRPC(client, config.url, config.key), StableMarket(),
                          code_commit=config.code_commit)


app = FastAPI()
app.include_router(make_paper_router(PaperConfig.environment, factory))


@app.get('/health')
def health():
    return {'status': 'ci-only'}
