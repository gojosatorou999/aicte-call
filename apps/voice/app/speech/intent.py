"""Tiny multilingual yes/no detector.

Used only to gate irreversible tool calls on an explicit spoken "yes"
(commitments, reminders, grievances). It is deliberately conservative:
anything ambiguous, or containing a negation, is NOT a yes.
"""

from __future__ import annotations

import re
from typing import Final

_YES: Final = frozenset(
    {
        # English / romanised Hindi
        "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "correct", "right", "confirm",
        "confirmed", "haan", "han", "haanji", "ji", "theek", "thik", "sahi",
        # Hindi
        "हाँ", "हां", "हा", "जी", "ठीक", "सही", "बिल्कुल", "कन्फर्म",
        # Telugu
        "అవును", "సరే", "ఔను", "కరెక్ట్",
        # Malayalam
        "അതെ", "ശരി", "ശെരി", "ഓക്കെ",
        # Tamil
        "ஆம்", "ஆமாம்", "சரி", "ஓகே",
        # Kannada
        "ಹೌದು", "ಸರಿ", "ಓಕೆ",
    }
)  # fmt: skip

_NO: Final = frozenset(
    {
        "no", "not", "nope", "dont", "don't", "never", "wrong", "stop", "cancel", "wait",
        "nahi", "nahin", "mat", "galat",
        "नहीं", "नही", "मत", "गलत", "रुको", "रुकिए",
        "కాదు", "వద్దు", "లేదు", "తప్పు",
        "അല്ല", "വേണ്ട", "ഇല്ല", "തെറ്റ്",
        "இல்லை", "வேண்டாம்", "தவறு",
        "ಇಲ್ಲ", "ಬೇಡ", "ತಪ್ಪು",
    }
)  # fmt: skip

_SPLIT: Final = re.compile(r"[\s,.;:!?।॥\"'()\-]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _SPLIT.split(text.casefold()) if t]


def is_affirmative(text: str) -> bool:
    tokens = _tokens(text)
    if not tokens or any(t in _NO for t in tokens):
        return False
    return any(t in _YES for t in tokens)
