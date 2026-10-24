"""Step -> TwiML for the Gather graph."""

from __future__ import annotations

from urllib.parse import urlencode

from twilio.twiml.voice_response import VoiceResponse

from app import twiml
from app.config import Settings
from app.graph.engine import REGISTRY, Step
from app.graph.state import CallState
from app.session import SessionClaims, mint_session
from app.speech import languages as langs

_ENGLISH = langs.LANGUAGES["en"]
RELAY_WELCOME = "Go ahead and ask your question. Say main menu at any time to go back."


def _voice(settings: Settings) -> str:
    return settings.gather_voice


def spoken_lines(call: CallState, step: Step) -> list[str]:
    """Everything the caller should hear for `step`, in order: the step's own lines,
    then (unless the call is ending) the next node's prompt. Moves the call to that
    node and records the lines in the transcript. Shared by the Gather and live paths."""
    lines = list(step.say)
    if not step.end:
        target = REGISTRY[step.goto] if step.goto else REGISTRY[call.node]
        call.node = target.id
        lines.append(target.prompt(call))
    for line in lines:
        call.note("assistant", line)
    return lines


def turn_url(settings: Settings, node_id: str, key: str, *, noinput: bool = False) -> str:
    query = {"n": node_id, "k": key} | ({"noinput": "1"} if noinput else {})
    return f"{settings.public_base}/voice/g/turn?{urlencode(query)}"


def render(
    call: CallState,
    step: Step,
    settings: Settings,
    *,
    recording_callback: str | None = None,
) -> str:
    """The TwiML for `step`. Spoken lines are also noted in the transcript."""
    if step.relay:
        return _relay(call, settings)

    vr = VoiceResponse()
    if recording_callback:
        vr.start().recording(
            recording_status_callback=recording_callback,
            recording_status_callback_method="POST",
            recording_status_callback_event="completed",
            channels="dual",
            track="both",
        )
    voice = _voice(settings)
    lines = spoken_lines(call, step)
    if step.end:
        for line in lines:
            vr.say(line, voice=voice, language=_ENGLISH.bcp47)
        vr.hangup()
        return str(vr)

    *intro, prompt = lines
    for line in intro:
        vr.say(line, voice=voice, language=_ENGLISH.bcp47)
    target = REGISTRY[call.node]

    options: dict[str, object] = {
        "input": target.kind.value,
        "action": turn_url(settings, target.id, call.key),
        "method": "POST",
        "timeout": max(1, round(settings.no_input_timeout_ms / 1000)),
        "language": _ENGLISH.bcp47,
        "speech_timeout": settings.gather_open_speech_timeout_sec
        if target.open_ended and settings.gather_open_speech_timeout_sec
        else "auto",
    }
    if target.num_digits:
        options["num_digits"] = target.num_digits
    if target.finish_on_key:
        options["finish_on_key"] = target.finish_on_key
    if target.hints:
        options["hints"] = target.hints
    # phone_call is the model the earlier agent used for en-IN on this account
    options["speech_model"] = settings.speech_model_overrides.get("en", "phone_call")
    if not target.sensitive:
        options["barge_in"] = "true"  # let callers speak or key over the prompt
    gather = vr.gather(**options)
    gather.say(prompt, voice=voice, language=_ENGLISH.bcp47)
    vr.redirect(turn_url(settings, target.id, call.key, noinput=True), method="POST")
    return str(vr)


def _relay(call: CallState, settings: Settings) -> str:
    """Hybrid mode: give this question to the LLM relay, then resume the graph."""
    c = call.claims
    token = mint_session(
        SessionClaims(
            call_sid=c.call_sid,
            persona=c.persona,
            language="en",
            caller_ref=c.caller_ref,
            job_id=c.job_id,
            identity_source=c.identity_source,
            subject_id=c.subject_id,
            verified=call.runner.policy.verified,
            via_graph=True,
        ),
        settings,
    )
    ws_url = settings.public_base.replace("https://", "wss://").replace("http://", "ws://")
    return twiml.relay(
        ws_url=ws_url + "/voice/relay",
        lang_code="en",
        session_jwt=token,
        welcome=RELAY_WELCOME,
        recording_callback=None,  # the call is already being recorded
        action=f"{settings.public_base}/voice/g/resume?k={call.key}",
        settings=settings,
    )
