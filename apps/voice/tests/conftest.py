from __future__ import annotations

from datetime import datetime

import pytest

from app import clock
from app.config import Settings, get_settings
from app.utils import drain


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Each test gets its own call report files, so tests never touch the real ones.
    monkeypatch.setenv("CALL_LOG_DB", str(tmp_path / "calls.db"))
    monkeypatch.setenv("CALL_LOG_CSV", str(tmp_path / "calls.csv"))
    # Tests must behave the same on every machine: ignore any developer's local .env.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("TWILIO_SIGNATURE_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "development")
    # The suite exercises every language; English-only is the production default.
    monkeypatch.setenv("ENABLED_LANGUAGES", "en,hi,te,ml,ta,kn")
    # Existing suites exercise the LLM relay path; graph tests choose their own mode.
    monkeypatch.setenv("CONVERSATION_MODE", "relay")
    get_settings.cache_clear()
    drain.reset_for_tests()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Saturday 24 Oct 2026, 10:00 IST, so relative dates are deterministic."""
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 24, 10, 0, tzinfo=clock.IST))
