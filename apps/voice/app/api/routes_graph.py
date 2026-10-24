"""Gather-graph webhooks: every caller answer lands on /voice/g/turn."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

import app.graph.nodes  # noqa: F401  (registers every node)
from app import twiml
from app.api.twilio_auth import require_twilio_signature
from app.config import get_settings
from app.graph.engine import MSG_TECHNICAL, REGISTRY, Input, Step, menu_for, run_turn
from app.graph.render import render
from app.graph.state import StateStore
from app.obs import get_logger
from app.speech.languages import LANGUAGES

router = APIRouter(prefix="/voice/g", tags=["graph"])
log = get_logger(__name__)
Form = dict[str, str]
ENGLISH = LANGUAGES["en"]


def _store(request: Request) -> StateStore:
    return request.app.state.calls


def _xml(body: str) -> Response:
    return Response(body, media_type="application/xml")


def _technical_problem() -> Response:
    return _xml(twiml.say_and_hangup(MSG_TECHNICAL, ENGLISH))


@router.post("/turn")
async def turn(
    request: Request,
    n: str,
    noinput: str = "",
    form: Form = Depends(require_twilio_signature),
) -> Response:
    settings = get_settings()
    sid = form.get("CallSid", "")
    call = _store(request).get(sid)
    node = REGISTRY.get(n)
    if call is None or node is None:
        # Server restarted mid-call, or a stale URL: say so rather than go silent.
        log.warning("graph_state_missing", call_sid=sid, node=n)
        return _technical_problem()

    # Speech and keypad text are never logged: they may hold a PIN, OTP or personal details.
    inp = Input(text=form.get("SpeechResult", "").strip(), digits=form.get("Digits", "").strip())
    step = await run_turn(call, node, inp, noinput=bool(noinput), settings=settings)
    xml = render(call, step, settings)
    if step.end:
        log.info("graph_call_ended", call_sid=sid, lines=len(call.transcript))
        _store(request).drop(sid)
    return _xml(xml)


@router.post("/resume")
async def resume(request: Request, form: Form = Depends(require_twilio_signature)) -> Response:
    """The LLM relay (hybrid mode) ended: carry on from the main menu."""
    call = _store(request).get(form.get("CallSid", ""))
    if call is None:
        return _technical_problem()
    call.slots["served"] = True
    step = Step(say=["Back at the main menu."], goto=menu_for(call))
    return _xml(render(call, step, get_settings()))
