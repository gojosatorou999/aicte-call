"""Transcript PII redaction (app.md 12.4: OTP digits and phone numbers,
keep last 4).

Primary defence is at the source: nodes flagged `sensitive` (PIN, OTP) never
store what the caller said. This module is the second layer, applied to every
transcript line before it is stored or sent to core / the LLM.
"""

from __future__ import annotations

import re
from typing import Final

# A run of digits optionally separated by single spaces / hyphens, so that
# "9 8 7 6 5 4 3 2 1 0" and "98765-43210" are caught as well as "9876543210".
_DIGIT_RUN: Final = re.compile(r"(?<![A-Za-z0-9])(?<![A-Za-z]-)\+?(?:\d[\s-]?){4,}\d(?!\d)")
_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PHONE_MIN_DIGITS: Final = 10


def _mask_run(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = [c for c in raw if c.isdigit()]
    if len(digits) >= PHONE_MIN_DIGITS:
        return "*" * (len(digits) - 4) + "".join(digits[-4:])  # phone: keep last 4
    return "[digits]"  # OTP / PIN / short code: drop entirely


def redact_transcript(text: str | None) -> str:
    """Return `text` with phone numbers masked (last 4 kept), short digit
    runs (OTP/PIN) removed and emails replaced. None/empty -> ''.

    Application numbers like `APP-2026-0451` are deliberately preserved: the
    lookbehind skips digit runs that follow a letter-hyphen prefix."""
    if not text:
        return ""
    out = _EMAIL.sub("[email]", text)
    return _DIGIT_RUN.sub(_mask_run, out)
