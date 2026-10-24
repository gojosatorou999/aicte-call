"""Create the per-call state for a routed call and decide its first step."""

from __future__ import annotations

from app.agent.personas import OutboundJob
from app.agent.policy import GatedToolRunner, SessionPolicy, Verification
from app.agent.tools import ToolRunner
from app.agent.verification import Verifier
from app.config import Settings
from app.core_client import CoreClient
from app.graph.engine import MENU_NODE, MSG_TECHNICAL, Step
from app.graph.render import render
from app.graph.state import CallState, StateStore
from app.session import SessionClaims

GREETING = "Hello, this is the AICTE assistant."


async def create_call(
    *,
    claims: SessionClaims,
    token: str,
    core: CoreClient,
    store: StateStore,
    settings: Settings,
    job: OutboundJob | None,
    greeting: str | None = GREETING,
) -> tuple[CallState, Step]:
    """Build the call state and its first Step.

    `greeting` is spoken before the first prompt; pass None when something else
    (Twilio's welcomeGreeting on the live path) has already greeted the caller."""
    policy = SessionPolicy(claims, settings)
    call = CallState(
        call_sid=claims.call_sid,
        claims=claims,
        token=token,
        runner=GatedToolRunner(ToolRunner(core, token, claims.persona), policy),
        job=job,
        direction=claims.direction,
        phone_masked=claims.phone_masked,
        hybrid_available=settings.conversation_mode == "hybrid" and settings.llm_enabled,
    )

    say = [greeting] if greeting and not job else []
    kind = policy.verification_needed()
    if kind != Verification.NONE:
        call.verifier = Verifier(
            kind=kind,
            core=core,
            session_jwt=token,
            max_attempts=(
                settings.pin_max_attempts if kind == Verification.PIN else settings.otp_max_attempts
            ),
        )
        if not await call.verifier.begin():  # could not send the one-time code
            return call, Step(say=[*say, MSG_TECHNICAL], end=True)
        first = "verify_pin" if kind == Verification.PIN else "verify_otp"
    elif job is not None:
        first = "out_open"
    elif claims.persona.value == "INSTITUTION" and policy.is_restricted():
        first = "inst_limited"  # identified by a typed number only: nothing but a callback
    else:
        first = MENU_NODE[claims.persona.value]

    store.put(call)
    return call, Step(say=say, goto=first)


async def start_call(
    *,
    claims: SessionClaims,
    token: str,
    core: CoreClient,
    store: StateStore,
    settings: Settings,
    job: OutboundJob | None,
    recording_callback: str | None,
    greeting: str | None = GREETING,
) -> str:
    """Gather path: build the state and return the TwiML for the first prompt."""
    call, step = await create_call(
        claims=claims,
        token=token,
        core=core,
        store=store,
        settings=settings,
        job=job,
        greeting=greeting,
    )
    return render(call, step, settings, recording_callback=recording_callback)
