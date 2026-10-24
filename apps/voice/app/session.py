"""Per-call session JWT (app.md 12.2 step 4, 13.13).

Minted when a call is routed to a persona, carried through the relay
WebSocket, and forwarded to core as `X-Voice-Session` so core can re-check
persona permissions server-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import jwt

from app import clock
from app.config import Settings, get_settings

_ALG = "HS256"


class Persona(StrEnum):
    INSTITUTION = "INSTITUTION"
    OFFICER = "OFFICER"
    PUBLIC = "PUBLIC"
    FACULTY = "FACULTY"
    EXPERT = "EXPERT"


class IdentitySource(StrEnum):
    """How the persona was established. `ENTERED_NUMBER` is only a claim made
    by the caller (IVR "press 9" path): personas reached that way must pass
    PIN / OTP before any non-public data is released (V3)."""

    CALLER_ID = "CALLER_ID"
    ENTERED_NUMBER = "ENTERED_NUMBER"
    ANONYMOUS = "ANONYMOUS"
    OUTBOUND_JOB = "OUTBOUND_JOB"


@dataclass(frozen=True, slots=True)
class SessionClaims:
    call_sid: str
    persona: Persona
    language: str
    caller_ref: str  # HMAC of the msisdn, never the number itself
    job_id: str | None = None
    identity_source: IdentitySource = IdentitySource.ANONYMOUS
    subject_id: str | None = None  # institution_id / officer_id / faculty_id from the caller lookup
    # Set when the Gather graph already verified the caller (PIN/OTP) and then handed the call
    # to the LLM relay, so the relay does not ask again. Only we can set it: the token is signed.
    verified: bool = False
    # The call came from the Gather graph: the relay may hand control back to it.
    via_graph: bool = False


def mint_session(claims: SessionClaims, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    iat = int(clock.now().timestamp())
    payload: dict[str, Any] = {
        "call_sid": claims.call_sid,
        "persona": claims.persona.value,
        "language": claims.language,
        "caller_ref": claims.caller_ref,
        "identity_source": claims.identity_source.value,
        "iat": iat,
        "exp": iat + s.session_jwt_ttl_sec,
    }
    if claims.subject_id:
        payload["subject_id"] = claims.subject_id
    if claims.job_id:
        payload["job_id"] = claims.job_id
    if claims.verified:
        payload["verified"] = True
    if claims.via_graph:
        payload["via_graph"] = True
    return jwt.encode(payload, s.jwt_secret, algorithm=_ALG)


def verify_session(token: str, settings: Settings | None = None) -> SessionClaims:
    """Raises `jwt.InvalidTokenError` (incl. expiry) on anything wrong.

    `exp` is checked against the demo-aware clock, not the wall clock."""
    s = settings or get_settings()
    data = jwt.decode(
        token,
        s.jwt_secret,
        algorithms=[_ALG],
        options={"verify_exp": False, "require": ["exp", "iat"]},
    )
    if int(data["exp"]) < int(clock.now().timestamp()):
        raise jwt.ExpiredSignatureError("session expired")
    return SessionClaims(
        call_sid=data["call_sid"],
        persona=Persona(data["persona"]),
        language=data["language"],
        caller_ref=data["caller_ref"],
        job_id=data.get("job_id"),
        identity_source=IdentitySource(data.get("identity_source", IdentitySource.ANONYMOUS)),
        subject_id=data.get("subject_id"),
        verified=bool(data.get("verified", False)),
        via_graph=bool(data.get("via_graph", False)),
    )
