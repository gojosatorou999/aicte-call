"""ACP voice service entrypoint: `uvicorn app.main:app`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__, relay_ws
from app.agent.llm import OpenAIChat
from app.agent.tools import load_tool_specs
from app.api import routes_health, routes_voice
from app.config import get_settings
from app.core_client import CoreClient
from app.obs import configure_logging, get_logger
from app.ratelimit import DailyLimiter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, log_file=settings.log_file or None)
    settings.require_production_secrets()
    tools = load_tool_specs()  # contract drift must fail at boot, not mid-call
    core = CoreClient(settings)
    llm = OpenAIChat(settings) if settings.llm_enabled else None
    app.state.core, app.state.llm = core, llm
    app.state.limiter = DailyLimiter(settings.public_calls_per_day)
    app.state.jobs = {}  # job_id -> OutboundJob; filled by the dialer (V4)
    get_logger(__name__).info(
        "voice_started", tools=len(tools), env=settings.app_env.value, llm=settings.llm_enabled
    )
    try:
        yield
    finally:
        await core.aclose()
        if llm is not None:
            await llm.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="ACP Voice", version=__version__, lifespan=lifespan)
    app.include_router(routes_health.router)
    app.include_router(routes_voice.router)
    app.include_router(relay_ws.router)
    return app


app = create_app()
