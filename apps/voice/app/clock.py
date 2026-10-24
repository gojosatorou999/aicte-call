"""The only source of "now" for the voice service (app.md 0.2 rule 6, 18.1).

Mirrors core's `app.core.clock.now()`: in DEMO_MODE the same Redis key
(`demo:clock_offset_seconds`) shifts time, so relative dates the caller
says ("next Friday") resolve against the demo clock, not the wall clock.
Never call `datetime.now()` elsewhere in this service.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings

IST = ZoneInfo("Asia/Kolkata")
_OFFSET_KEY = "demo:clock_offset_seconds"

# Set by the app at boot when Redis is reachable; tests may set it directly.
_offset_seconds_provider = None


def set_offset_provider(provider) -> None:  # type: ignore[no-untyped-def]
    """`provider()` returns the current demo offset in seconds (sync)."""
    global _offset_seconds_provider
    _offset_seconds_provider = provider


def now() -> datetime:
    settings = get_settings()
    real = datetime.now(tz=ZoneInfo(settings.default_timezone))
    if settings.demo_mode and _offset_seconds_provider is not None:
        try:
            return real + timedelta(seconds=int(_offset_seconds_provider() or 0))
        except Exception:
            return real
    return real


def today():  # type: ignore[no-untyped-def]
    return now().date()


OFFSET_KEY = _OFFSET_KEY
