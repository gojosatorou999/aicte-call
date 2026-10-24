"""Gather-graph webhooks: every caller answer lands on /voice/g/turn."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import app.graph.nodes  # noqa: F401  (registers every node)
from app import twiml
from app.api.twilio_auth import TwilioRequest, twilio_request
from app.config import Settings, get_settings
from app.graph.engine import MSG_TECHNICAL, REGISTRY, Input, Step, menu_for, run_turn
from app.graph.render import render
from app.graph.state import CallState, StateStore
from app.obs import get_logger
from app.speech.languages import LANGUAGES

router = APIRouter(prefix="/voice/g", tags=["graph"])
log = get_logger(__name__)
ENGLISH = LANGUAGES["en"]


def _store(request: Request) -> StateStore:
    return request.app.state.calls


def _xml(body: str) -> Response:
    return Response(body, media_type="application/xml")


def _technical_problem() -> Response:
    return _xml(twiml.say_and_hangup(MSG_TECHNICAL, ENGLISH))


def _check_unsigned(
    tw: TwilioRequest, call: CallState | None, key: str, settings: Settings
) -> None:
    """Twilio's restricted trial tier calls these URLs WITHOUT a signature. Such a request is
    accepted only if it carries the secret key we put in this call's action URLs, comes from our
    own Twilio account, and refers to a call we are really handling. (A signature that is present
    but wrong was already refused by `twilio_request`.)"""
    if tw.trust != "unsigned":
        return
    problem = None
    if not settings.dial_accept_unsigned_twiml:
        problem = "unsigned requests are switched off"
    elif call is None:
        problem = "no such call"
    elif not hmac.compare_digest(key.encode(), call.key.encode()):
        problem = "wrong call key"
    elif not hmac.compare_digest(
        tw.form.get("AccountSid", "").encode(), settings.twilio_account_sid.encode()
    ):
        problem = "account does not match"
    if problem:
        log.warning("unsigned_turn_refused", reason=problem)
        raise HTTPException(status_code=403, detail="not authorised")


@router.post("/turn")
async def turn(
    request: Request,
    n: str,
    noinput: str = "",
    k: str = "",
    tw: TwilioRequest = Depends(twilio_request),
) -> Response:
    settings = get_settings()
    form = tw.form
    sid = form.get("CallSid", "")
    call = _store(request).get(sid)
    _check_unsigned(tw, call, k, settings)
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
async def resume(
    request: Request, k: str = "", tw: TwilioRequest = Depends(twilio_request)
) -> Response:
    """The LLM relay (hybrid mode) ended: carry on from the main menu."""
    call = _store(request).get(tw.form.get("CallSid", ""))
    _check_unsigned(tw, call, k, get_settings())
    if call is None:
        return _technical_problem()
    call.slots["served"] = True
    step = Step(say=["Back at the main menu."], goto=menu_for(call))
    return _xml(render(call, step, get_settings()))
