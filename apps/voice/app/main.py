"""ACP voice service entrypoint: `uvicorn app.main:app`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, dial, relay_ws
from app.agent.llm import OpenAIChat
from app.agent.tools import load_tool_specs
from app.api import routes_dial, routes_graph, routes_health, routes_voice
from app.config import get_settings
from app.core_client import CoreClient
from app.graph import live
from app.graph.state import StateStore
from app.obs import configure_logging, get_logger
from app.ratelimit import DailyLimiter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, log_file=settings.log_file or None)
    settings.require_production_secrets()
    log = get_logger(__name__)
    if (
        settings.twilio_signature_validation_enabled
        and not settings.twilio_auth_token.get_secret_value()
    ):
        log.warning(
            "twilio_auth_token_missing",
            effect="every Twilio webhook will be rejected with 403",
            fix="set TWILIO_AUTH_TOKEN in apps/voice/.env (or, for local testing only, "
            "TWILIO_SIGNATURE_VALIDATION_ENABLED=false)",
        )
    if settings.public_voice_url.startswith("http://localhost") and not settings.is_production():
        log.info(
            "public_voice_url_is_local",
            note="set PUBLIC_VOICE_URL to your ngrok URL for real calls",
        )
    tools = load_tool_specs()  # contract drift must fail at boot, not mid-call
    core = CoreClient(settings)
    llm = OpenAIChat(settings) if settings.llm_enabled else None
    app.state.core, app.state.llm = core, llm
    app.state.limiter = DailyLimiter(settings.public_calls_per_day)
    app.state.dials = dial.DialStore()
    app.state.dial_guard = dial.DialGuard(settings)
    app.state.dialer = dial.Dialer(settings)
    app.state.calls = StateStore()  # per-call Gather-graph state
    app.state.jobs = {}  # job_id -> OutboundJob; filled by the dialer (V4)
    log.info(
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
    # The dial pad is a web page on another origin (GitHub Pages) calling this API.
    # `ngrok-skip-browser-warning` lets it get past ngrok's free-tier interstitial.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().dialpad_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "ngrok-skip-browser-warning"],
        max_age=600,
    )
    app.include_router(routes_health.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_graph.router)
    app.include_router(routes_dial.router)
    app.include_router(live.router)
    app.include_router(relay_ws.router)
    return app


app = create_app()
