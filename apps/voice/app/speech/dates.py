"""Spoken-date understanding for the six call languages (app.md 20 V3).

`resolve(phrase, today, lang)` turns what the caller said ("next Friday",
"20th", "20 नवंबर", "ఈ నెల చివర", "இந்த மாத இறுதி") into a calendar date.
The brain reads the result back to the caller before anything is saved, so
an ambiguous phrase is corrected by the caller, never silently committed.

Rules worth knowing
  * A bare weekday is the next such day strictly after today; "this <day>"
    allows today; "next <day>" is that weekday in the following Mon-Sun week.
  * A bare day number ("the 20th") is its next occurrence on or after today.
  * Month + day with no year is the next occurrence on or after today.
  * "end of the month" is the last day of this month ("next" -> next month).

The vocabularies (especially ml/ta/kn/te) were written without a native
reviewer; extend `_LEX` as real speech-to-text output shows gaps.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from app.speech.languages import DEFAULT_LANGUAGE


class DateParseError(ValueError):
    """The phrase could not be turned into a date."""


@dataclass(frozen=True, slots=True)
class Resolved:
    date: date
    spoken_en: str
    spoken_local: str

    @property
    def iso(self) -> str:
        return self.date.isoformat()


@dataclass(frozen=True, slots=True)
class _Lex:
    today: tuple[str, ...]
    tomorrow: tuple[str, ...]
    day_after: tuple[str, ...]  # may be multi-word
    nxt: tuple[str, ...]
    this: tuple[str, ...]
    end: tuple[str, ...]  # "end/last (of the month)"
    month_end: tuple[str, ...]  # single words meaning "end of month"
    day: tuple[str, ...]
    week: tuple[str, ...]
    month: tuple[str, ...]
    weekdays: tuple[tuple[str, ...], ...]  # Mon..Sun
    months: tuple[tuple[str, ...], ...]  # Jan..Dec


_EN = _Lex(
    today=("today", "tonight"),
    tomorrow=("tomorrow", "tmrw"),
    day_after=("day after tomorrow",),
    nxt=("next", "coming", "following"),
    this=("this",),
    end=("end", "last", "close"),
    month_end=("month-end", "monthend"),
    day=("day", "days"),
    week=("week", "weeks"),
    month=("month", "months"),
    weekdays=(
        ("monday", "mon"), ("tuesday", "tue", "tues"), ("wednesday", "wed"),
        ("thursday", "thu", "thur", "thurs"), ("friday", "fri"), ("saturday", "sat"),
        ("sunday", "sun"),
    ),
    months=(
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ),
)  # fmt: skip

_LEX: Final[dict[str, _Lex]] = {
    "en": _EN,
    "hi": _Lex(
        today=("आज", "aaj"),
        tomorrow=("कल", "kal"),
        day_after=("परसों", "parson"),
        nxt=("अगले", "अगला", "अगली", "आने", "agle", "agla"),
        this=("इस", "is"),
        end=("अंत", "आखिर", "आखिरी", "अंतिम", "end"),
        month_end=("महीनाअंत",),
        day=("दिन", "din"),
        week=("हफ्ते", "हफ़्ते", "हफ्ता", "सप्ताह", "हफ्ते", "hafte", "hafta"),
        month=("महीने", "महीना", "माह", "mahine", "mahina"),
        weekdays=(
            ("सोमवार", "somvar"), ("मंगलवार", "mangalvar"), ("बुधवार", "budhvar"),
            ("गुरुवार", "बृहस्पतिवार", "वीरवार", "guruvar"), ("शुक्रवार", "shukravar"),
            ("शनिवार", "shanivar"), ("रविवार", "इतवार", "ravivar"),
        ),
        months=(
            ("जनवरी",), ("फरवरी", "फ़रवरी"), ("मार्च",), ("अप्रैल", "अप्रेल"), ("मई",), ("जून",),
            ("जुलाई",), ("अगस्त",), ("सितंबर", "सितम्बर"), ("अक्टूबर", "अक्तूबर"),
            ("नवंबर", "नवम्बर"), ("दिसंबर", "दिसम्बर"),
        ),
    ),
    "te": _Lex(
        today=("ఈరోజు", "ఈ రోజు", "నేడు", "ఇవాళ"),
        tomorrow=("రేపు",),
        day_after=("ఎల్లుండి",),
        nxt=("వచ్చే", "తదుపరి", "మరుసటి"),
        this=("ఈ",),
        end=("చివర", "చివరి", "ఆఖరి", "చివరకు"),
        month_end=("నెలాఖరు", "నెలాఖరులోగా", "నెలాఖరున"),
        day=("రోజులు", "రోజుల", "రోజు"),
        week=("వారం", "వారాలు", "వారాల"),
        month=("నెల", "నెలలు", "నెలలో"),
        weekdays=(
            ("సోమవారం",), ("మంగళవారం",), ("బుధవారం",), ("గురువారం",), ("శుక్రవారం",),
            ("శనివారం",), ("ఆదివారం",),
        ),
        months=(
            ("జనవరి",), ("ఫిబ్రవరి",), ("మార్చి",), ("ఏప్రిల్",), ("మే",), ("జూన్",), ("జూలై",),
            ("ఆగస్టు", "ఆగస్ట్"), ("సెప్టెంబర్",), ("అక్టోబర్",), ("నవంబర్",), ("డిసెంబర్",),
        ),
    ),
    "ml": _Lex(
        today=("ഇന്ന്",),
        tomorrow=("നാളെ",),
        day_after=("മറ്റന്നാൾ",),
        nxt=("അടുത്ത", "വരുന്ന"),
        this=("ഈ",),
        end=("അവസാനം", "അവസാന", "അവസാനത്തിൽ"),
        month_end=("മാസാവസാനം", "മാസാന്ത്യം"),
        day=("ദിവസം", "ദിവസത്തിനകം", "ദിവസങ്ങൾ"),
        week=("ആഴ്ച", "ആഴ്ചകൾ", "ആഴ്ചയ്ക്കകം"),
        month=("മാസം", "മാസത്തിന്റെ", "മാസത്തിൽ", "മാസങ്ങൾ"),
        weekdays=(
            ("തിങ്കൾ", "തിങ്കളാഴ്ച"), ("ചൊവ്വ", "ചൊവ്വാഴ്ച"), ("ബുധൻ", "ബുധനാഴ്ച"),
            ("വ്യാഴം", "വ്യാഴാഴ്ച"), ("വെള്ളി", "വെള്ളിയാഴ്ച"), ("ശനി", "ശനിയാഴ്ച"),
            ("ഞായർ", "ഞായറാഴ്ച"),
        ),
        months=(
            ("ജനുവരി",), ("ഫെബ്രുവരി",), ("മാർച്ച്",), ("ഏപ്രിൽ",), ("മേയ്", "മെയ്"), ("ജൂൺ",),
            ("ജൂലൈ",), ("ഓഗസ്റ്റ്", "ആഗസ്റ്റ്"), ("സെപ്റ്റംബർ",), ("ഒക്ടോബർ",), ("നവംബർ",),
            ("ഡിസംബർ",),
        ),
    ),
    "ta": _Lex(
        today=("இன்று",),
        tomorrow=("நாளை",),
        day_after=("நாளை மறுநாள்", "நாளைமறுநாள்"),
        nxt=("அடுத்த", "வரும்"),
        this=("இந்த",),
        end=("இறுதி", "கடைசி", "முடிவு", "இறுதியில்"),
        month_end=("மாதஇறுதி",),
        day=("நாள்", "நாட்கள்", "நாட்களில்", "நாளில்"),
        week=("வாரம்", "வாரங்கள்", "வாரத்தில்", "வாரங்களில்"),
        month=("மாதம்", "மாத", "மாதத்தின்", "மாதங்கள்", "மாதத்தில்"),
        weekdays=(
            ("திங்கள்", "திங்கட்கிழமை"), ("செவ்வாய்", "செவ்வாய்க்கிழமை"), ("புதன்", "புதன்கிழமை"),
            ("வியாழன்", "வியாழக்கிழமை"), ("வெள்ளி", "வெள்ளிக்கிழமை"), ("சனி", "சனிக்கிழமை"),
            ("ஞாயிறு", "ஞாயிற்றுக்கிழமை"),
        ),
        months=(
            ("ஜனவரி",), ("பிப்ரவரி",), ("மார்ச்",), ("ஏப்ரல்",), ("மே",), ("ஜூன்",), ("ஜூலை",),
            ("ஆகஸ்ட்",), ("செப்டம்பர்",), ("அக்டோபர்",), ("நவம்பர்",), ("டிசம்பர்",),
        ),
    ),
    "kn": _Lex(
        today=("ಇಂದು", "ಇವತ್ತು"),
        tomorrow=("ನಾಳೆ",),
        day_after=("ನಾಡಿದ್ದು",),
        nxt=("ಮುಂದಿನ", "ಬರುವ"),
        this=("ಈ",),
        end=("ಕೊನೆ", "ಅಂತ್ಯ", "ಕೊನೆಯಲ್ಲಿ", "ಕೊನೆಯ"),
        month_end=("ತಿಂಗಳಾಂತ್ಯ",),
        day=("ದಿನ", "ದಿನಗಳು", "ದಿನಗಳಲ್ಲಿ"),
        week=("ವಾರ", "ವಾರಗಳು", "ವಾರಗಳಲ್ಲಿ"),
        month=("ತಿಂಗಳು", "ತಿಂಗಳ", "ತಿಂಗಳಲ್ಲಿ"),
        weekdays=(
            ("ಸೋಮವಾರ",), ("ಮಂಗಳವಾರ",), ("ಬುಧವಾರ",), ("ಗುರುವಾರ",), ("ಶುಕ್ರವಾರ",), ("ಶನಿವಾರ",),
            ("ಭಾನುವಾರ",),
        ),
        months=(
            ("ಜನವರಿ",), ("ಫೆಬ್ರವರಿ",), ("ಮಾರ್ಚ್",), ("ಏಪ್ರಿಲ್",), ("ಮೇ",), ("ಜೂನ್",), ("ಜುಲೈ",),
            ("ಆಗಸ್ಟ್",), ("ಸೆಪ್ಟೆಂಬರ್",), ("ಅಕ್ಟೋಬರ್",), ("ನವೆಂಬರ್",), ("ಡಿಸೆಂಬರ್",),
        ),
    ),
}

_EN_NUMBER_WORDS: Final = {
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
}  # fmt: skip

# Indic digits -> ASCII (Devanagari, Bengali-range is not needed, Telugu, Malayalam, Tamil, Kannada)
_DIGITS: Final = {
    ord(c): str(i)
    for base in (0x0966, 0x0C66, 0x0D66, 0x0BE6, 0x0CE6)
    for i, c in enumerate(chr(base + k) for k in range(10))
}

_ISO: Final = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_PUNCT: Final = re.compile(r"[,.;:!?।॥\"'()\[\]{}/\\]+")
_DIGIT_RUN: Final = re.compile(r"(\d+)")
_ORDINAL_SUFFIX: Final = {"st", "nd", "rd", "th", "వ", "వతేదీ", "तारीख", "तारीख़", "तक"}

_WEEKDAYS_EN: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS_EN: Final = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip


def _tokens(text: str) -> list[str]:
    text = text.casefold().translate(_DIGITS)
    text = _PUNCT.sub(" ", text).replace("-", " ")
    text = _DIGIT_RUN.sub(r" \1 ", text)
    return [t for t in text.split() if t not in _ORDINAL_SUFFIX]


def _match(token: str, variants: tuple[str, ...]) -> bool:
    """Whole-token match, or prefix match for variants of 4+ chars so that
    Indic suffixes ("శుక్రవారం నాడు", "நாட்களில்") still hit. Shorter variants
    must match exactly: "mon" must not match "month"."""
    return any(token == v or (len(v) >= 4 and token.startswith(v)) for v in variants)


def _has(tokens: list[str], joined: str, variants: tuple[str, ...]) -> bool:
    for v in variants:
        if " " in v:
            if v in joined:
                return True
        elif any(_match(t, (v,)) for t in tokens):
            return True
    return False


def _add_months(d: date, n: int) -> date:
    idx = d.month - 1 + n
    year, month = d.year + idx // 12, idx % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _next_day_of_month(today: date, day: int) -> date:
    """First date >= today whose day-of-month is `day` (skips months too short)."""
    y, m = today.year, today.month
    for _ in range(14):
        if day <= calendar.monthrange(y, m)[1] and date(y, m, day) >= today:
            return date(y, m, day)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise DateParseError("no such day")


def _next_month_day(today: date, month: int, day: int) -> date:
    for year in (today.year, today.year + 1, today.year + 2):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
    raise DateParseError("invalid month/day")


def _lexes(lang: str) -> list[_Lex]:
    """Session language first, English always (callers mix)."""
    primary = _LEX.get(lang, _LEX[DEFAULT_LANGUAGE])
    return [primary] if primary is _EN else [primary, _EN]


def resolve(phrase: str, today: date, lang: str = DEFAULT_LANGUAGE) -> Resolved:
    d = _resolve_date(phrase, today, lang)
    return Resolved(d, _spoken(d, "en"), _spoken(d, lang))


def _spoken(d: date, lang: str) -> str:
    lex = _LEX.get(lang, _EN)
    if lex is _EN:
        return f"{_WEEKDAYS_EN[d.weekday()]}, {d.day} {_MONTHS_EN[d.month - 1]} {d.year}"
    return f"{lex.weekdays[d.weekday()][0]}, {d.day} {lex.months[d.month - 1][0]} {d.year}"


def _resolve_date(phrase: str, today: date, lang: str) -> date:
    if not phrase or not phrase.strip():
        raise DateParseError("empty")

    translated = phrase.translate(_DIGITS)
    if m := _ISO.search(translated):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError as exc:
            raise DateParseError("invalid ISO date") from exc

    tokens = _tokens(phrase)
    joined = " ".join(tokens)
    lexes = _lexes(lang)

    def any_lex(attr: str) -> bool:
        return any(_has(tokens, joined, getattr(lx, attr)) for lx in lexes)

    numbers = [int(t) for t in tokens if t.isdigit()]
    year = next((n for n in numbers if 2000 <= n <= 2100), None)
    day_nums = [n for n in numbers if 1 <= n <= 31]

    # 1. month name + day number
    month_idx = next(
        (i for i in range(12) for lx in lexes for t in tokens if _match(t, lx.months[i])), None
    )
    if month_idx is not None and day_nums:
        month = month_idx + 1
        if year:
            try:
                return date(year, month, day_nums[0])
            except ValueError as exc:
                raise DateParseError("invalid date") from exc
        return _next_month_day(today, month, day_nums[0])

    nxt = any_lex("nxt")

    # 2. end of month
    if any_lex("month_end") or (any_lex("end") and any_lex("month")):
        return _month_end(_add_months(today, 1) if nxt else today)

    # 3. today / tomorrow / day after tomorrow  (longest first)
    if any_lex("day_after"):
        return today + timedelta(days=2)
    if any_lex("tomorrow"):
        return today + timedelta(days=1)
    if any_lex("today"):
        return today

    # 4. "in N days/weeks/months": the unit must directly follow the number, so
    #    "the 20th of this month" is not read as "in 20 months".
    for i, tok in enumerate(tokens[:-1]):
        n = int(tok) if tok.isdigit() else _EN_NUMBER_WORDS.get(tok)
        if n is None:
            continue
        unit = tokens[i + 1]
        if any(_match(unit, lx.week) for lx in lexes):
            return today + timedelta(weeks=n)
        if any(_match(unit, lx.month) for lx in lexes):
            return _add_months(today, n)
        if any(_match(unit, lx.day) for lx in lexes):
            return today + timedelta(days=n)

    # 5. weekday names
    for i in range(7):
        if any(_match(t, lx.weekdays[i]) for lx in lexes for t in tokens):
            return _weekday(today, i, nxt=nxt, this=any_lex("this"))

    # 6. "next week" / "next month"
    if nxt and any_lex("week"):
        return today + timedelta(weeks=1)
    if nxt and any_lex("month"):
        return _add_months(today, 1)

    # 7. bare day-of-month ("the 20th")
    if day_nums:
        return _next_day_of_month(today, day_nums[0])

    raise DateParseError(f"cannot understand {phrase!r}")


def _weekday(today: date, idx: int, *, nxt: bool, this: bool) -> date:
    if nxt:  # that weekday in the following Mon-Sun week
        monday_next = today + timedelta(days=7 - today.weekday())
        return monday_next + timedelta(days=idx)
    delta = (idx - today.weekday()) % 7
    if delta == 0 and not this:
        delta = 7
    return today + timedelta(days=delta)


_TIME_RE: Final = re.compile(
    r"\b(?P<h>1[0-2]|0?[1-9])(?:[:.](?P<m>[0-5]\d))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)\b", re.IGNORECASE
)
_BARE_24H: Final = re.compile(r"\b(?P<h>[01]?\d|2[0-3])[:.](?P<m>[0-5]\d)\b")


def resolve_time(phrase: str) -> tuple[int, int]:
    """"3 pm", "3:30 p.m.", "15:00", "noon", "in the morning" -> (hour, minute).

    Vague day-parts map to a fixed hour (morning 10, afternoon 15, evening 17)
    and are read back to the caller like any other time."""
    text = (phrase or "").lower()
    if m := _TIME_RE.search(text):
        hour = int(m["h"]) % 12 + (12 if m["ap"].lower().startswith("p") else 0)
        return hour, int(m["m"] or 0)
    if m := _BARE_24H.search(text):
        return int(m["h"]), int(m["m"])
    if "noon" in text or "midday" in text:
        return 12, 0
    for word, hour in (("morning", 10), ("afternoon", 15), ("evening", 17)):
        if word in text:
            return hour, 0
    raise DateParseError(f"no time in {phrase!r}")


def spoken_time(hour: int, minute: int) -> str:
    suffix = "A M" if hour < 12 else "P M"
    h12 = hour % 12 or 12
    return f"{h12} {suffix}" if minute == 0 else f"{h12} {minute:02d} {suffix}"
