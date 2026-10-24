"""Twilio webhook authentication, as FastAPI dependencies.

Twilio signs its webhooks (`X-Twilio-Signature`, an HMAC over the URL and form with the
account's auth token). `require_twilio_signature` insists on it. `twilio_request` is for the one
endpoint whose requests Twilio's restricted *trial* tier sends WITHOUT a signature: it still
rejects a signature that is present but wrong, and tells the route whether the request was
signed so the route can apply its own check (see `dial.unsigned_fetch_problem`).
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from app.config import get_settings

Trust = Literal["signed", "unsigned", "off"]


class TwilioRequest(NamedTuple):
    form: dict[str, str]
    trust: Trust  # signed: verified | unsigned: no signature sent | off: checks disabled (dev)


async def _read(request: Request) -> tuple[dict[str, str], bool]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    return form, bool(request.headers.get("X-Twilio-Signature"))


def _signature_ok(request: Request, form: dict[str, str]) -> bool:
    """The URL Twilio signed is the one we gave it (`PUBLIC_VOICE_URL` + path), not the one the
    proxy hands us, so it is rebuilt from settings."""
    settings = get_settings()
    url = settings.public_base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
    return validator.validate(url, form, request.headers.get("X-Twilio-Signature", ""))


async def require_twilio_signature(request: Request) -> dict[str, str]:
    """Strict: a valid signature is required. Returns the parsed form so the route does not
    read the body twice."""
    form, has_signature = await _read(request)
    if not get_settings().twilio_signature_validation_enabled:
        return form
    if not has_signature or not _signature_ok(request, form):
        raise HTTPException(status_code=403, detail="invalid twilio signature")
    return form


async def twilio_request(request: Request) -> TwilioRequest:
    """Lenient about a *missing* signature, strict about a *wrong* one."""
    form, has_signature = await _read(request)
    if not get_settings().twilio_signature_validation_enabled:
        return TwilioRequest(form, "off")
    if not has_signature:
        return TwilioRequest(form, "unsigned")
    if not _signature_ok(request, form):
        raise HTTPException(status_code=403, detail="invalid twilio signature")
    return TwilioRequest(form, "signed")
