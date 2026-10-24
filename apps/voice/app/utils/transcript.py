"""Transcript line shared by the Gather graph and the LLM relay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranscriptLine:
    role: str  # "assistant" | "caller"
    text: str  # already redacted (see utils/redact.py)
    at: str  # ISO timestamp, demo-clock aware
