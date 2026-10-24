"""Caller-hash helper. Raw phone numbers are never persisted by this
service (CLAUDE.md invariant #6). Everything we store keys on
`HMAC-SHA256(msisdn, pepper)`."""

from __future__ import annotations

import hashlib
import hmac


def hash_msisdn(msisdn: str, pepper: str) -> str:
    """HMAC-SHA256(msisdn, pepper) as hex. Empty msisdn maps to a dedicated
    `<none>` sentinel so "no caller ID" is never confused with a collision."""
    if not msisdn:
        return "<none>"
    return hmac.new(pepper.encode("utf-8"), msisdn.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_msisdn(msisdn: str) -> str:
    """`+919876543210` -> `******3210` (keep last 4) - the only form of a
    phone number that may be logged or sent to core as `*_masked`."""
    digits = "".join(ch for ch in msisdn if ch.isdigit())
    if not digits:
        return ""
    return "*" * max(len(digits) - 4, 0) + digits[-4:]
