from __future__ import annotations

import pytest

from app.config import get_settings
from app.utils import drain


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_SIGNATURE_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    drain.reset_for_tests()
    yield
    get_settings.cache_clear()
