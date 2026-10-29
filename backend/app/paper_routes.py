"""Separate, disabled-by-default P3 endpoints; current activation is CI-only."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .paper_source import PaperMarket
from .paper_worker import PaperRPC, PaperWorker, RpcError


@dataclass(frozen=True)
class PaperConfig:
    cron_secret: str
    diagnostic_secret: str
    url: str
    key: str
    code_commit: str | None

    @classmethod
    def environment(cls):
        if os.environ.get("PAPER_MEASUREMENT_ENABLED") != "1":
            raise HTTPException(503, "paper_measurement_disabled")
        url = os.environ.get("PAPER_SUPABASE_URL", "")
        parsed = urlsplit(url)
        # P3 approval is CI-only. No fallback to legacy/hosted Supabase settings.
        if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("VERCEL_ENV")
                or parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.port != 54321 or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise HTTPException(503, "paper_environment_not_authorized")
        values = [os.environ.get(name, "") for name in
                  ("CRON_SECRET", "PAPER_DIAGNOSTIC_SECRET", "PAPER_SUPABASE_SERVICE_ROLE_KEY")]
        if not all(values) or values[0] == values[1]:
            raise HTTPException(503, "paper_configuration_missing")
        return cls(values[0], values[1], url, values[2], os.environ.get("VERCEL_GIT_COMMIT_SHA"))


@asynccontextmanager
async def worker_factory(config):
    async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
        yield PaperWorker(PaperRPC(client, config.url, config.key), PaperMarket(client),
                          code_commit=config.code_commit)


class TickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tick_id: UUID


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_id: UUID


def make_paper_router(config_provider=PaperConfig.environment, factory=worker_factory):
    router = APIRouter(prefix="/api/paper", tags=["paper measurement"])

    def authorized(kind):
        async def check(authorization: str | None = Header(default=None)):
            config = config_provider()
            secret = config.cron_secret if kind == "tick" else config.diagnostic_secret
            if not authorization or not secrets.compare_digest(authorization, "Bearer " + secret):
                raise HTTPException(401, "unauthorized")
            return config
        return check

    async def execute(config, method, identity):
        try:
            async with factory(config) as worker:
                return await getattr(worker, method)(str(identity))
        except TimeoutError:
            raise HTTPException(504, "paper_worker_timeout") from None
        except (RpcError, httpx.HTTPError):
            raise HTTPException(502, "paper_dependency_unavailable") from None

    @router.post("/measure/tick")
    async def tick(body: TickRequest, config=Depends(authorized("tick"))):
        return await execute(config, "tick", body.tick_id)

    @router.post("/diagnostic/run")
    async def diagnostic(body: DiagnosticRequest, config=Depends(authorized("diagnostic"))):
        return await execute(config, "diagnostic", body.test_id)

    return router
