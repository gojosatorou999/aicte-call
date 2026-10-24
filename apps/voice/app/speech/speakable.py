"""Make text sound right when a TTS voice reads it.

Written text is tuned for the eye ("B.Tech", "2026-27", "A P P"); a neural voice
reads those literally or oddly. Twilio's own guidance is to write numbers and
abbreviations out before sending text to the voice. Applied only to the live
(streaming-voice) path - the Gather path uses Twilio's <Say>.
"""

from __future__ import annotations

import re
from typing import Final

_ACADEMIC_YEAR: Final = re.compile(r"\b(20\d{2})-(\d{2})\b")
_REPLACEMENTS: Final = (
    (re.compile(r"\bB\.?\s?Tech\b", re.IGNORECASE), "B Tech"),
    (re.compile(r"\bM\.?\s?Tech\b", re.IGNORECASE), "M Tech"),
    (re.compile(r"\bB\.?\s?Arch\b", re.IGNORECASE), "B Arch"),
    (re.compile(r"\bMBA\b"), "M B A"),
    (re.compile(r"\bMCA\b"), "M C A"),
    (re.compile(r"\bCSE\b"), "C S E"),
    (re.compile(r"\bECE\b"), "E C E"),
    (re.compile(r"\bEEE\b"), "E E E"),
    (re.compile(r"\bEVC\b"), "E V C"),
    (re.compile(r"\bSMS\b"), "S M S"),
    (re.compile(r"\bOTP\b"), "O T P"),
    (re.compile(r"\s*&\s*"), " and "),
)


def speakable(text: str) -> str:
    out = _ACADEMIC_YEAR.sub(r"\1 to \2", text)
    for pattern, spoken in _REPLACEMENTS:
        out = pattern.sub(spoken, out)
    return re.sub(r"\s{2,}", " ", out).strip()
