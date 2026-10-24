"""The six supported languages and their per-language speech settings
(app.md 12.2, 20 V1).

Twilio's ConversationRelay docs do not list Indian-locale STT/TTS support,
so providers, voices and speech models are defaults that can be overridden
per language from env (`TWILIO_VOICES_JSON`, `GATHER_SPEECH_MODELS_JSON`) and
must be checked by ear in V7 before the demo.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class Language:
    code: str  # short code used everywhere inside the service and in core
    bcp47: str  # what Twilio STT/TTS and <Gather language> expect
    name: str  # English name
    native_name: str
    menu_digit: str  # DTMF key in the language menu
    tts_voice: str  # Google voice
    spoken_names: tuple[str, ...]  # what a caller may say in the language menu


LANGUAGES: dict[str, Language] = {
    lang.code: lang
    for lang in (
        Language(
            "en",
            "en-IN",
            "English",
            "English",
            "1",
            "en-IN-Standard-A",
            ("english", "इंग्लिश", "अंग्रेजी"),
        ),
        Language(
            "hi", "hi-IN", "Hindi", "हिन्दी", "2", "hi-IN-Standard-A", ("hindi", "हिंदी", "हिन्दी")
        ),
        Language("te", "te-IN", "Telugu", "తెలుగు", "3", "te-IN-Standard-A", ("telugu", "తెలుగు")),
        Language(
            "ml", "ml-IN", "Malayalam", "മലയാളം", "4", "ml-IN-Standard-A", ("malayalam", "മലയാളം")
        ),
        Language("ta", "ta-IN", "Tamil", "தமிழ்", "5", "ta-IN-Standard-A", ("tamil", "தமிழ்")),
        Language("kn", "kn-IN", "Kannada", "ಕನ್ನಡ", "6", "kn-IN-Standard-A", ("kannada", "ಕನ್ನಡ")),
    )
}
DEFAULT_LANGUAGE = "en"

_BY_DIGIT = {lang.menu_digit: lang for lang in LANGUAGES.values()}

STT_PROVIDER = "Google"
TTS_PROVIDER = "Google"
DEFAULT_SPEECH_MODEL = "telephony"


def get_language(code: str | None) -> Language:
    """Unknown or missing codes fall back to English, never raise: a bad
    value from core must not drop a live call."""
    return LANGUAGES.get((code or "").lower()[:2], LANGUAGES[DEFAULT_LANGUAGE])


def enabled(settings: Settings | None = None) -> list[Language]:
    """Languages callers may use, in configured order (unknown codes ignored)."""
    codes = (settings or get_settings()).enabled_language_codes
    found = [LANGUAGES[c] for c in codes if c in LANGUAGES]
    return found or [LANGUAGES[DEFAULT_LANGUAGE]]


def pick(code: str | None, settings: Settings | None = None) -> Language:
    """`code` if it is enabled, else the first enabled language."""
    allowed = enabled(settings)
    wanted = get_language(code)
    return wanted if wanted in allowed else allowed[0]


def from_digit(digit: str | None) -> Language | None:
    return _BY_DIGIT.get((digit or "").strip())


def from_speech(text: str | None) -> Language | None:
    """Match a spoken language name from the language-menu Gather."""
    t = (text or "").lower()
    if not t:
        return None
    for lang in LANGUAGES.values():
        if any(name in t for name in lang.spoken_names):
            return lang
    return None


def tts_voice(lang: Language, settings: Settings | None = None) -> str:
    return (settings or get_settings()).voice_overrides.get(lang.code, lang.tts_voice)


def speech_model(lang: Language, settings: Settings | None = None) -> str:
    return (settings or get_settings()).speech_model_overrides.get(lang.code, DEFAULT_SPEECH_MODEL)


def menu_hints() -> str:
    """Comma-separated hints for the language-menu <Gather>."""
    return ",".join(n for lang in LANGUAGES.values() for n in lang.spoken_names)
