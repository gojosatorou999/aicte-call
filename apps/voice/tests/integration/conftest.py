from __future__ import annotations

import pytest

from app import relay_ws


@pytest.fixture(autouse=True)
def _fast_speech(monkeypatch):
    """Make the 'estimated speech time' waits negligible."""
    monkeypatch.setattr(relay_ws, "SPEECH_CHARS_PER_SEC", 1e9)
    monkeypatch.setattr(relay_ws, "END_PADDING_SEC", 0.0)
