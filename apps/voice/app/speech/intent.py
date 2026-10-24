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


_NUMBER_WORDS: Final = {
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4,
    "fourth": 4, "five": 5, "fifth": 5,
}  # fmt: skip
# Common speech-to-text mishearings of a spoken digit; trusted only in very short replies,
# otherwise "in order to" would pick option 2.
_MISHEARD: Final = {"for": 4, "to": 2, "too": 2, "won": 1}
_SHORT_REPLY: Final = 3


def is_negative(text: str) -> bool:
    """A clear no. "no" anywhere wins, so "no, not that date" is negative."""
    tokens = _tokens(text)
    return any(t in _NO for t in tokens) and not any(t in _YES for t in tokens)


def pick_number(text: str, limit: int) -> int | None:
    """ "two", "the second one", "2" -> 2, if within 1..limit."""
    tokens = _tokens(text)
    for token in tokens:
        n = int(token) if token.isdigit() else _NUMBER_WORDS.get(token)
        if n is None and len(tokens) <= _SHORT_REPLY:
            n = _MISHEARD.get(token)
        if n is not None and 1 <= n <= limit:
            return n
    return None


def has_any(text: str, *words: str) -> bool:
    """True if any word / short phrase occurs in `text` (whole-word match)."""
    haystack = " " + " ".join(_tokens(text)) + " "
    return any(f" {w} " in haystack for w in words)
