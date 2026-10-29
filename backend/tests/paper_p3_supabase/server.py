"""CI-only Uvicorn bootstrap. Market I/O is synthetic; engine and RPCs are real."""
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI
import httpx

from app.costs import book_costs
from app.paper_measurement import MeasurementError, STEP_MS
from app.paper_routes import PaperConfig, make_paper_router
from app.paper_worker import PaperRPC, PaperWorker


class SyntheticMarket:
    async def latest(self, pair):
        if pair.startswith('ERROR'):
            raise MeasurementError('market_data_unavailable')
        return (time.time_ns() // 1_000_000 // STEP_MS - 1) * STEP_MS

    async def candles(self, pair, window):
        prices = [100.] * window.expected_count
        if pair.startswith('OPEN') and len(prices) >= 29:
            prices[-4:] = [90., 91., 91., 91.]
        rows = [{'open_time_ms': window.first_open_ms + i*STEP_MS,
                 'close_time_raw_ms': window.first_open_ms + (i+1)*STEP_MS - 1,
                 'open': price*.999, 'high': price*1.002, 'low': price*.998,
                 'close': price, 'volume': 1000000., 'quote_volume': price*1000000., 'confirm': 1}
                for i, price in enumerate(prices)]
        return rows[:-1] if pair.startswith('GAP') else rows

    async def cost(self, pair, notional):
        if pair.startswith('NOCOST'):
            raise MeasurementError('cost_profile_unavailable')
        fetched = time.time_ns() // 1_000_000
        profile = book_costs({'asks': [['100.1','100']], 'bids': [['99.9','100']], 'ts': str(fetched)}, notional)
        return profile, fetched


@asynccontextmanager
async def factory(config):
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        yield PaperWorker(PaperRPC(client, config.url, config.key), SyntheticMarket(), code_commit=config.code_commit)


app = FastAPI()
app.include_router(make_paper_router(PaperConfig.environment, factory))


@app.get('/health')
def health():
    return {'status': 'ci-only'}
