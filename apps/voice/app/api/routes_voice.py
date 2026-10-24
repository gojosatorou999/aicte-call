"""Twilio-facing webhooks under /voice/* (inbound IVR, status callbacks)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from app import dial, ivr, twiml
from app.agent.personas import OutboundJob
from app.api.twilio_auth import require_twilio_signature
from app.config import get_settings
from app.core_client import CoreClient, CoreError
from app.graph.start import start_call
from app.obs import get_logger
from app.session import IdentitySource, Persona, SessionClaims, mint_session
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


def _ws_url(path: str) -> str:
    base = get_settings().public_base
    return base.replace("https://", "wss://").replace("http://", "ws://") + path


def _core(request: Request) -> CoreClient:
    return request.app.state.core


INBOUND_WELCOME = "Hello, this is the AICTE assistant."
OUTBOUND_WELCOME = "Hello, this is the AICTE assistant, calling as you requested from our website."


@dataclass(frozen=True)
class Outbound:
    """A call we placed (the dial pad): the person on the line is the one we dialled."""

    identity: IdentitySource
    job: OutboundJob | None = None
    welcome: str = OUTBOUND_WELCOME


async def _connect(
    request: Request,
    form: Form,
    lang_code: str,
    caller: ivr.Caller,
    *,
    entered: bool,
    party_number: str | None = None,
    outbound: Outbound | None = None,
) -> Response:
    """Mint the session, register the call with core, and hand over to the conversation.

    `party_number` is the number of the person we are talking to: the caller on an
    inbound call (the default), the number we dialled on an outbound one."""
    settings = get_settings()
    lang = langs.pick(lang_code)  # a disabled language can never reach a session
    call_sid = form.get("CallSid", "")
    # The line the call is on is always the identity, even when the persona was
    # established by an entered number.
    party = form.get("From", "") if party_number is None else party_number
    caller_ref = hash_msisdn(party, settings.caller_hash_pepper.get_secret_value())
    # Anonymous public callers: bounded calls per number per day (app.md 12.3).
    if (
        outbound is None
        and caller.persona == Persona.PUBLIC
        and party
        and not request.app.state.limiter.allow(caller_ref)
    ):
        log.info("public_daily_limit", call_sid=call_sid)
        return _xml(twiml.say_and_hangup(phrase("daily_limit", lang.code), lang))

    job = outbound.job if outbound else None
    if job:
        request.app.state.jobs[job.job_id] = job
    claims = SessionClaims(
        call_sid=call_sid,
        persona=caller.persona,
        language=lang.code,
        caller_ref=caller_ref,
        job_id=job.job_id if job else None,
        identity_source=outbound.identity
        if outbound
        else ivr.identity_source(caller, entered=entered),
        subject_id=caller.subject_id,
    )
    token = mint_session(claims)
    try:
        await _core(request).post(
            "/calls",
            session_jwt=token,
            json={
                "call_sid": call_sid,
                "direction": "OUTBOUND" if outbound else "INBOUND",
                "persona": claims.persona.value,
                "language": lang.code,
                "caller_ref": claims.caller_ref,
                "caller_masked": mask_msisdn(party),
                "identity_source": claims.identity_source.value,
            },
        )
    except CoreError as exc:  # the call goes ahead; core just won't have a start record
        log.warning("call_register_failed", call_sid=call_sid, error=str(exc))

    mode = settings.conversation_mode
    welcome = outbound.welcome if outbound else INBOUND_WELCOME
    log.info(
        "call_routed",
        call_sid=call_sid,
        persona=claims.persona.value,
        language=lang.code,
        mode=mode,
        direction="outbound" if outbound else "inbound",
    )
    if mode == "live":  # the graph over ConversationRelay: streaming speech + human voice
        return _xml(
            twiml.live(
                ws_url=_ws_url("/voice/live"),
                session_jwt=token,
                welcome=welcome,
                recording_callback=_url("/voice/recording"),
                settings=settings,
            )
        )
    if mode != "relay":  # gather / hybrid: the deterministic graph over <Gather>
        return _xml(
            await start_call(
                claims=claims,
                token=token,
                core=_core(request),
                store=request.app.state.calls,
                settings=settings,
                job=job,
                recording_callback=_url("/voice/recording"),
                greeting=welcome,
            )
        )
    return _xml(
        twiml.relay(
            ws_url=_ws_url("/voice/relay"),
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
    request.app.state.dials.update_status(call_sid, form.get("CallStatus", ""))
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
        {
            "recording_url": form.get("RecordingUrl", ""),
            "recording_sid": form.get("RecordingSid", ""),
        },
    )
    return Response(status_code=204)


@router.post("/outbound-twiml")
async def outbound_twiml(
    request: Request, d: str = "", form: Form = Depends(require_twilio_signature)
) -> Response:
    """Twilio fetches this when the person we rang picks up (the `url` of calls.create)."""
    settings = get_settings()
    record = request.app.state.dials.get(d)
    if record is None:
        return _xml(
            twiml.say_and_hangup(
                "Sorry, this call request has expired. Please request a new call from the website.",
                langs.LANGUAGES["en"],
            )
        )
    to_number = form.get("To", "")
    if settings.demo_scenarios and record.scenario != "public":
        persona = dial.scenario_persona(record.scenario)
        assert persona is not None
        subject = dial.scenario_subject(record.scenario)
        caller = ivr.Caller(
            persona=persona,
            known=True,
            raw={f"{persona.value.lower()}_id": subject} if subject else {},
        )
        job = dial.demo_job(record.dial_id) if record.scenario == "institution" else None
        identity = IdentitySource.OUTBOUND_JOB if job else IdentitySource.CALLER_ID
    else:
        caller = await ivr.lookup_caller(_core(request), to_number)
        job, identity = None, ivr.identity_source(caller, entered=False)
    return await _connect(
        request,
        form,
        langs.DEFAULT_LANGUAGE,
        caller,
        entered=False,
        party_number=to_number,
        outbound=Outbound(identity=identity, job=job),
    )
