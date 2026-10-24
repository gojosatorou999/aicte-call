"""IVR routing: who is calling, and which persona they get (app.md 12.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core_client import CoreClient, CoreError
from app.obs import get_logger
from app.session import IdentitySource, Persona

log = get_logger(__name__)

_PERSONA_BY_KIND = {
    "INSTITUTION": Persona.INSTITUTION,
    "OFFICER": Persona.OFFICER,
    "FACULTY": Persona.FACULTY,
}


@dataclass(frozen=True, slots=True)
class Caller:
    persona: Persona
    preferred_language: str | None = None
    known: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def subject_id(self) -> str | None:
        """The institution / officer / faculty id core resolved for this caller."""
        for key in ("institution_id", "officer_id", "faculty_id"):
            if value := self.raw.get(key):
                return str(value)
        return None


ANONYMOUS = Caller(persona=Persona.PUBLIC)


def normalize_msisdn(raw: str) -> str:
    """Digits typed on the keypad -> E.164 for core's lookup. Bare 10-digit
    numbers are taken as Indian mobiles."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return ("+" + digits) if digits else ""


async def lookup_caller(core: CoreClient, phone: str) -> Caller:
    """`GET /callers/lookup`. Any failure (core down, bad payload) degrades
    to an anonymous PUBLIC caller; a live call is never dropped over lookup."""
    if not phone:
        return ANONYMOUS
    try:
        data = await core.get("/callers/lookup", params={"phone": phone})
    except CoreError as exc:
        log.warning("caller_lookup_failed", error=str(exc))
        return ANONYMOUS
    if not isinstance(data, dict):
        return ANONYMOUS
    persona = _PERSONA_BY_KIND.get(str(data.get("kind", "")).upper())
    if persona is None:
        return ANONYMOUS
    return Caller(
        persona=persona,
        preferred_language=data.get("preferred_language"),
        known=True,
        raw=data,
    )


def identity_source(caller: Caller, *, entered: bool) -> IdentitySource:
    if not caller.known:
        return IdentitySource.ANONYMOUS
    return IdentitySource.ENTERED_NUMBER if entered else IdentitySource.CALLER_ID
