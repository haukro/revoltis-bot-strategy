"""CI-only Uvicorn bootstrap: the same synthetic-market worker as server.py, but the
tick endpoint authenticates exactly like Preview (A4 rev2 tick-bound HMAC token).

Only the auth mode is switched; configuration gating stays PaperConfig.environment()
(CI, loopback local Supabase). Production code has no switch for this.
"""
from dataclasses import replace

from fastapi import FastAPI

from app.paper_routes import PaperConfig, make_paper_router
from server import factory


def tick_token_config():
    return replace(PaperConfig.environment(), tick_auth="tick_hmac")


app = FastAPI()
app.include_router(make_paper_router(tick_token_config, factory))


@app.get('/health')
def health():
    return {'status': 'ci-only-tick-token'}
