"""Per-call state for the Gather graph.

Twilio <Gather> is stateless between webhooks, so everything that must
survive from one turn to the next (current node, slots, attempt counters,
whether the caller is verified) lives here, keyed by CallSid. The store is
in-memory and per-process, like the daily limiter: fine for one worker, and
the interface is small enough to move to Redis for several (V7).

Never stored: PIN / OTP digits (the verifier forgets them as soon as it has
asked core), and nothing here is written to logs.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app import clock
from app.agent.personas import OutboundJob
from app.agent.policy import GatedToolRunner
from app.agent.tools import ToolNotAllowed
from app.agent.verification import Verifier
from app.core_client import CoreError, CoreUnavailable
from app.session import SessionClaims
from app.utils.redact import redact_transcript
from app.utils.transcript import TranscriptLine

MAX_AGE_SEC = 3 * 3600
CONSECUTIVE_OUTAGES_LIMIT = 3


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class CallState:
    call_sid: str
    claims: SessionClaims
    token: str  # session JWT for core calls
    runner: GatedToolRunner
    node: str = ""
    direction: str = "inbound"  # or "outbound" (a call the dial pad placed)
    phone_masked: str = ""  # last four digits only; the raw number is never kept
    started_at: datetime = field(
        default_factory=lambda: clock.now()
    )  # via the clock helper, so demo time applies
    # What the call was about, for the admin dashboard / CSV (see calllog.py). Written by nodes.
    facts: dict[str, Any] = field(default_factory=dict)
    # Secret carried in every action URL we hand Twilio for this call. It stands in for the
    # signature when Twilio's trial tier does not send one (see api/routes_graph.py).
    key: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    job: OutboundJob | None = None
    verifier: Verifier | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    silence: int = 0
    abuse_strikes: int = 0
    outages: int = 0
    hybrid_available: bool = False  # LLM relay may be used for open questions
    started: float = field(default_factory=time.monotonic)
    transcript: list[TranscriptLine] = field(default_factory=list)

    # -- helpers used by node handlers ----------------------------------------------
    def bump(self, key: str) -> int:
        """Count an attempt at `key`; returns the new count."""
        self.attempts[key] = self.attempts.get(key, 0) + 1
        return self.attempts[key]

    def clear(self, *keys: str) -> None:
        for key in keys:
            self.attempts.pop(key, None)
            self.slots.pop(key, None)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def note(self, role: str, text: str) -> None:
        if text.strip():
            self.transcript.append(
                TranscriptLine(role, redact_transcript(text), clock.now().isoformat())
            )

    async def tool(
        self, name: str, args: dict[str, Any] | None = None, *, confirmed: bool = False
    ) -> ToolResult:
        """Run a tool through the session policy. `confirmed=True` means the
        graph has already had an explicit yes from the caller. Never raises."""
        try:
            data = await self.runner.run(name, args or {}, skip_confirm=confirmed)
        except ToolNotAllowed:
            return ToolResult(False, error="not_allowed")
        except CoreUnavailable:
            self.outages += 1
            return ToolResult(False, error="unavailable")
        except CoreError as exc:
            self.outages = 0
            return ToolResult(False, error=exc.code or "rejected")
        self.outages = 0
        if isinstance(data, dict) and data.get("error"):
            return ToolResult(False, data=data, error=str(data["error"]))
        return ToolResult(True, data=data if isinstance(data, dict) else {"result": data})

    @property
    def core_down(self) -> bool:
        return self.outages >= CONSECUTIVE_OUTAGES_LIMIT


class StateStore:
    def __init__(self) -> None:
        self._calls: dict[str, CallState] = {}

    def get(self, call_sid: str) -> CallState | None:
        return self._calls.get(call_sid)

    def put(self, state: CallState) -> None:
        self._sweep()
        self._calls[state.call_sid] = state

    def drop(self, call_sid: str) -> None:
        self._calls.pop(call_sid, None)

    def _sweep(self) -> None:
        stale = [sid for sid, c in self._calls.items() if c.elapsed() > MAX_AGE_SEC]
        for sid in stale:
            del self._calls[sid]

    def __len__(self) -> int:
        return len(self._calls)
