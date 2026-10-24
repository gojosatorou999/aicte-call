"""TwiML builders: greeting, language menu, identify prompts and the relay hand-off."""

from __future__ import annotations

from twilio.twiml.voice_response import VoiceResponse

from app.config import Settings, get_settings
from app.speech import languages as langs
from app.speech.phrases import phrase

GREETING_EN = "Welcome to the AICTE Approval and Compliance helpline. This call may be recorded."
GREETING_HI = "एआईसीटीई अनुमोदन और अनुपालन हेल्पलाइन में आपका स्वागत है। यह कॉल रिकॉर्ड की जा सकती है।"
UNAVAILABLE_EN = "We are unable to take your call right now. Please try again shortly."

MENU_TIMEOUT_SEC = 5


def _say(node: VoiceResponse, text: str, lang: langs.Language) -> None:
    node.say(text, voice=f"Google.{langs.tts_voice(lang)}", language=lang.bcp47)


def language_menu(action: str) -> str:
    """Greeting, then the language menu (DTMF or speech) of the enabled languages.
    With a single enabled language there is no menu: the greeting plays and the
    call continues straight on in that language.

    The menu is spoken inside the <Gather> so the caller can barge in with a
    key press. If nothing is entered Twilio falls through to the <Redirect>
    (`noinput=1`); `action` carries the default language (`d=`) the route
    then falls back to: caller's preferred language, else English."""
    enabled = langs.enabled()
    default_redirect = f"{action}{'&' if '?' in action else '?'}noinput=1"
    vr = VoiceResponse()
    vr.say(GREETING_EN, language="en-IN")
    if len(enabled) == 1:
        vr.redirect(default_redirect, method="POST")
        return str(vr)
    if any(lang.code == "hi" for lang in enabled):
        vr.say(GREETING_HI, language="hi-IN")
    gather = vr.gather(
        input="dtmf speech",
        num_digits=1,
        timeout=MENU_TIMEOUT_SEC,
        speech_timeout="auto",
        action=action,
        method="POST",
        hints=langs.menu_hints(),
        language="en-IN",
    )
    for lang in enabled:
        _say(gather, phrase("language_menu", lang.code), lang)
    vr.redirect(default_redirect, method="POST")
    return str(vr)


def official_prompt(lang_code: str, identify_url: str, connect_url: str) -> str:
    """Unknown caller: "AICTE official or institution staff? press 9"."""
    lang = langs.get_language(lang_code)
    vr = VoiceResponse()
    gather = vr.gather(
        input="dtmf", num_digits=1, timeout=4, action=identify_url, method="POST"
    )
    _say(gather, phrase("official_prompt", lang.code), lang)
    vr.redirect(connect_url, method="POST")
    return str(vr)


def enter_number(lang_code: str, action: str, connect_url: str) -> str:
    lang = langs.get_language(lang_code)
    vr = VoiceResponse()
    gather = vr.gather(
        input="dtmf", finish_on_key="#", timeout=8, action=action, method="POST"
    )
    _say(gather, phrase("enter_number", lang.code), lang)
    vr.redirect(connect_url, method="POST")
    return str(vr)


def relay(
    *,
    ws_url: str,
    lang_code: str,
    session_jwt: str,
    welcome: str,
    recording_callback: str | None,
    action: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Hand the call to ConversationRelay, with dual-channel recording unless
    `recording_callback` is None (already recording). `action` is where Twilio
    sends the call when the relay ends: the Gather graph uses it to resume.

    The session JWT travels as a <Parameter> (arrives in the `setup`
    message's `customParameters`), not in the URL: query strings on the
    relay URL are not reliably forwarded."""
    s = settings or get_settings()
    lang = langs.get_language(lang_code)
    vr = VoiceResponse()
    if recording_callback:
        start = vr.start()
        start.recording(
            recording_status_callback=recording_callback,
            recording_status_callback_method="POST",
            recording_status_callback_event="completed",
            channels="dual",
            track="both",
        )
    connect = vr.connect(action=action) if action else vr.connect()
    relay_node = connect.conversation_relay(
        url=ws_url,
        language=lang.bcp47,
        welcome_greeting=welcome,
        tts_provider=langs.TTS_PROVIDER,
        voice=langs.tts_voice(lang, s),
        transcription_provider=langs.STT_PROVIDER,
        speech_model=langs.speech_model(lang, s),
        interruptible="any",
        dtmf_detection="true",
        profanity_filter="false",  # abuse detection is ours (guardrails.py), on the raw words
    )
    relay_node.parameter(name="session", value=session_jwt)
    return str(vr)


def redirect(url: str) -> str:
    vr = VoiceResponse()
    vr.redirect(url, method="POST")
    return str(vr)


def say_and_hangup(text: str, lang: langs.Language) -> str:
    vr = VoiceResponse()
    _say(vr, text, lang)
    vr.hangup()
    return str(vr)


def unavailable() -> str:
    """Played while draining or when the service cannot start a conversation."""
    vr = VoiceResponse()
    vr.say(UNAVAILABLE_EN, language="en-IN")
    vr.hangup()
    return str(vr)
