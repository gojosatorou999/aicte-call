"""Twilio webhook signature check, as a FastAPI dependency."""

from __future__ import annotations

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from app.config import get_settings


async def require_twilio_signature(request: Request) -> dict[str, str]:
    """Returns the parsed form so the route doesn't read the body twice.

    The URL Twilio signed is the one we gave it (`PUBLIC_VOICE_URL` + path),
    not the one the proxy hands us, so it is rebuilt from settings."""
    settings = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not settings.twilio_signature_validation_enabled:
        return form

    url = settings.public_base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
    if not signature or not validator.validate(url, form, signature):
        raise HTTPException(status_code=403, detail="invalid twilio signature")
    return form
