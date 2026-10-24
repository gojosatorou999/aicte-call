"""Twilio-facing webhooks under /voice/* (inbound IVR, status callbacks)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from app import ivr, twiml
from app.api.twilio_auth import require_twilio_signature
from app.config import get_settings
from app.core_client import CoreClient, CoreError
from app.obs import get_logger
from app.session import Persona, SessionClaims, mint_session
from app.speech import languages as langs
from app.speech.phrases import phrase
from app.utils.drain import is_draining
from app.utils.hashing import hash_msisdn, mask_msisdn

router = APIRouter(prefix="/voice", tags=["voice"])
log = get_logger(__name__)

_XML = "application/xml"
Form = dict[str, str]


def _xml(body: str) -> Response:
    return Response(body, media_type=_XML)


def _url(path: str, **params: str) -> str:
    return get_settings().public_base + path + (f"?{urlencode(params)}" if params else "")


def _ws_url() -> str:
    base = get_settings().public_base
    return base.replace("https://", "wss://").replace("http://", "ws://") + "/voice/relay"


def _core(request: Request) -> CoreClient:
    return request.app.state.core


async def _connect(
    request: Request, form: Form, lang_code: str, caller: ivr.Caller, *, entered: bool
) -> Response:
    """Mint the session, register the call with core, hand over to the relay."""
    settings = get_settings()
    lang = langs.pick(lang_code)  # a disabled language can never reach a session
    call_sid = form.get("CallSid", "")
    # Caller ID is always the line the call came from, even when the persona
    # was established by an entered number.
    from_number = form.get("From", "")
    caller_ref = hash_msisdn(from_number, settings.caller_hash_pepper.get_secret_value())
    # Anonymous public callers: bounded calls per number per day (app.md 12.3).
    if (
        caller.persona == Persona.PUBLIC
        and from_number
        and not request.app.state.limiter.allow(caller_ref)
    ):
        log.info("public_daily_limit", call_sid=call_sid)
        return _xml(twiml.say_and_hangup(phrase("daily_limit", lang.code), lang))
    claims = SessionClaims(
        call_sid=call_sid,
        persona=caller.persona,
        language=lang.code,
        caller_ref=caller_ref,
        identity_source=ivr.identity_source(caller, entered=entered),
        subject_id=caller.subject_id,
    )
    token = mint_session(claims)
    try:
        await _core(request).post(
            "/calls",
            session_jwt=token,
            json={
                "call_sid": call_sid,
                "direction": "INBOUND",
                "persona": claims.persona.value,
                "language": lang.code,
                "caller_ref": claims.caller_ref,
                "caller_masked": mask_msisdn(from_number),
                "identity_source": claims.identity_source.value,
            },
        )
    except CoreError as exc:  # the call goes ahead; core just won't have a start record
        log.warning("call_register_failed", call_sid=call_sid, error=str(exc))

    log.info("call_routed", call_sid=call_sid, persona=claims.persona.value, language=lang.code)
    return _xml(
        twiml.relay(
            ws_url=_ws_url(),
            lang_code=lang.code,
            session_jwt=token,
            welcome=phrase("welcome", lang.code),
            recording_callback=_url("/voice/recording"),
        )
    )


@router.post("/inbound")
async def inbound(request: Request, form: Form = Depends(require_twilio_signature)) -> Response:
    # `From` is deliberately not logged; callers are identified by hash only.
    log.info("inbound_call", call_sid=form.get("CallSid", ""))
    if is_draining():
        return _xml(twiml.unavailable())
    caller = await ivr.lookup_caller(_core(request), form.get("From", ""))
    default = langs.pick(caller.preferred_language).code
    return _xml(twiml.language_menu(_url("/voice/language", d=default)))


@router.post("/language")
async def language(
    request: Request,
    d: str = langs.DEFAULT_LANGUAGE,
    noinput: str = "",
    form: Form = Depends(require_twilio_signature),
) -> Response:
    chosen = langs.from_digit(form.get("Digits")) or langs.from_speech(form.get("SpeechResult"))
    if chosen is not None and chosen not in langs.enabled():
        chosen = None
    lang = chosen or langs.pick(d)
    if chosen is None:
        log.info("language_defaulted", language=lang.code, noinput=bool(noinput))

    caller = await ivr.lookup_caller(_core(request), form.get("From", ""))
    if caller.known:
        return await _connect(request, form, lang.code, caller, entered=False)
    return _xml(
        twiml.official_prompt(
            lang.code,
            identify_url=_url("/voice/identify", lang=lang.code),
            connect_url=_url("/voice/connect", lang=lang.code),
        )
    )


@router.post("/identify")
async def identify(
    lang: str = langs.DEFAULT_LANGUAGE, form: Form = Depends(require_twilio_signature)
) -> Response:
    """Result of the "press 9" prompt."""
    if form.get("Digits") == "9":
        return _xml(
            twiml.enter_number(
                lang,
                action=_url("/voice/identify-number", lang=lang),
                connect_url=_url("/voice/connect", lang=lang),
            )
        )
    return _xml(twiml.redirect(_url("/voice/connect", lang=lang)))


@router.post("/identify-number")
async def identify_number(
    request: Request,
    lang: str = langs.DEFAULT_LANGUAGE,
    form: Form = Depends(require_twilio_signature),
) -> Response:
    """Caller typed a registered number. An unknown number is not reported
    back (no registry probing): they simply continue as a public caller."""
    entered = ivr.normalize_msisdn(form.get("Digits", ""))
    caller = await ivr.lookup_caller(_core(request), entered)
    return await _connect(request, form, lang, caller, entered=caller.known)


@router.post("/connect")
async def connect(
    request: Request,
    lang: str = langs.DEFAULT_LANGUAGE,
    form: Form = Depends(require_twilio_signature),
) -> Response:
    """Connect on caller ID alone (unknown callers fall through to PUBLIC)."""
    caller = await ivr.lookup_caller(_core(request), form.get("From", ""))
    return await _connect(request, form, lang, caller, entered=False)


async def _relay_to_core(request: Request, call_sid: str, payload: dict[str, object]) -> None:
    try:
        await _core(request).post(f"/calls/{call_sid}/status", json=payload)
    except CoreError as exc:
        log.warning("status_relay_failed", call_sid=call_sid, error=str(exc))


@router.post("/status")
async def call_status(request: Request, form: Form = Depends(require_twilio_signature)) -> Response:
    """Twilio call status callback (set as the number's Status Callback URL)."""
    call_sid = form.get("CallSid", "")
    await _relay_to_core(
        request,
        call_sid,
        {
            "status": form.get("CallStatus", ""),
            "duration_sec": int(form.get("CallDuration") or 0),
            "direction": form.get("Direction", ""),
        },
    )
    return Response(status_code=204)


@router.post("/recording")
async def recording(request: Request, form: Form = Depends(require_twilio_signature)) -> Response:
    call_sid = form.get("CallSid", "")
    await _relay_to_core(
        request,
        call_sid,
        {"recording_url": form.get("RecordingUrl", ""), "recording_sid": form.get("RecordingSid", "")},
    )
    return Response(status_code=204)
