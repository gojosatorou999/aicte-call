from __future__ import annotations

from datetime import date

import pytest

from app.speech.dates import DateParseError, resolve

TODAY = date(2026, 10, 24)  # a Saturday


def d(m: int, day: int, y: int = 2026) -> date:
    return date(y, m, day)


@pytest.mark.parametrize(
    ("lang", "phrase", "expected"),
    [
        # English
        ("en", "tomorrow", d(10, 25)),
        ("en", "the day after tomorrow", d(10, 26)),
        ("en", "Friday", d(10, 30)),
        ("en", "next Monday", d(10, 26)),
        ("en", "next Friday", d(10, 30)),
        ("en", "this Saturday", d(10, 24)),
        ("en", "Saturday", d(10, 31)),
        ("en", "20th", d(11, 20)),
        ("en", "the 30th", d(10, 30)),
        ("en", "20th of this month", d(11, 20)),
        ("en", "20 November", d(11, 20)),
        ("en", "November 20th", d(11, 20)),
        ("en", "20 November 2027", d(11, 20, 2027)),
        ("en", "5 Nov", d(11, 5)),
        ("en", "in 2 weeks", d(11, 7)),
        ("en", "within 10 days", d(11, 3)),
        ("en", "in two months", d(12, 24)),
        ("en", "end of this month", d(10, 31)),
        ("en", "end of next month", d(11, 30)),
        ("en", "next week", d(10, 31)),
        ("en", "2026-12-01", d(12, 1)),
        ("en", "1 January", date(2027, 1, 1)),
        # Hindi
        ("hi", "कल", d(10, 25)),
        ("hi", "परसों", d(10, 26)),
        ("hi", "20 नवंबर", d(11, 20)),
        ("hi", "२० तारीख", d(11, 20)),
        ("hi", "इस महीने के अंत तक", d(10, 31)),
        ("hi", "अगले महीने के अंत तक", d(11, 30)),
        ("hi", "अगले शुक्रवार", d(10, 30)),
        ("hi", "2 हफ्ते में", d(11, 7)),
        ("hi", "5 दिन में", d(10, 29)),
        # Telugu
        ("te", "రేపు", d(10, 25)),
        ("te", "ఎల్లుండి", d(10, 26)),
        ("te", "నవంబర్ 20", d(11, 20)),
        ("te", "ఈ నెల చివర", d(10, 31)),
        ("te", "నెలాఖరు", d(10, 31)),
        ("te", "శుక్రవారం నాడు", d(10, 30)),
        ("te", "౨౦ నవంబర్", d(11, 20)),
        # Malayalam
        ("ml", "നാളെ", d(10, 25)),
        ("ml", "മറ്റന്നാൾ", d(10, 26)),
        ("ml", "നവംബർ 20", d(11, 20)),
        ("ml", "ഈ മാസം അവസാനം", d(10, 31)),
        ("ml", "വെള്ളിയാഴ്ച", d(10, 30)),
        # Tamil
        ("ta", "நாளை", d(10, 25)),
        ("ta", "நாளை மறுநாள்", d(10, 26)),
        ("ta", "நவம்பர் 20", d(11, 20)),
        ("ta", "இந்த மாத இறுதி", d(10, 31)),
        ("ta", "வெள்ளிக்கிழமை", d(10, 30)),
        # Kannada
        ("kn", "ನಾಳೆ", d(10, 25)),
        ("kn", "ನಾಡಿದ್ದು", d(10, 26)),
        ("kn", "ನವೆಂಬರ್ 20", d(11, 20)),
        ("kn", "ಈ ತಿಂಗಳ ಕೊನೆ", d(10, 31)),
        ("kn", "ಶುಕ್ರವಾರ", d(10, 30)),
        # Code-mixing: English words inside a Hindi call still work
        ("hi", "next Friday", d(10, 30)),
    ],
)
def test_resolve(lang, phrase, expected):
    assert resolve(phrase, TODAY, lang).date == expected


@pytest.mark.parametrize("phrase", ["", "   ", "soon", "whenever you like", "February 30", "99"])
def test_unparseable_raises(phrase):
    with pytest.raises(DateParseError):
        resolve(phrase, TODAY, "en")


def test_month_abbreviation_does_not_collide_with_month_word():
    # "mon" (Monday) must not match inside "month"
    assert resolve("end of the month", TODAY, "en").date == d(10, 31)


def test_day_number_skips_months_that_are_too_short():
    assert resolve("the 31st", date(2026, 11, 5), "en").date == d(12, 31)


def test_spoken_forms():
    r = resolve("20 November", TODAY, "en")
    assert r.iso == "2026-11-20"
    assert r.spoken_en == "Friday, 20 November 2026"
    assert resolve("20 November", TODAY, "hi").spoken_local == "शुक्रवार, 20 नवंबर 2026"
