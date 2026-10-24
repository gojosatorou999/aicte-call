from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.agent.llm import LLMEvent, TextDelta, ToolCallEvent
from app.core_client import CoreClient
from app.session import IdentitySource, Persona, SessionClaims, mint_session
from mock_core.app import app as mock_core_app


def mock_core_client() -> CoreClient:
    """A CoreClient wired straight into the in-process mock core."""
    return CoreClient(transport=httpx.ASGITransport(app=mock_core_app))


def token(
    call_sid: str = "CA1",
    persona: Persona = Persona.PUBLIC,
    language: str = "en",
    source: IdentitySource = IdentitySource.ANONYMOUS,
) -> str:
    return mint_session(SessionClaims(call_sid, persona, language, "ref", identity_source=source))


def text(*parts: str) -> list[LLMEvent]:
    return [TextDelta(p) for p in parts]


def tool(name: str, arguments: str, call_id: str = "call_1") -> ToolCallEvent:
    return ToolCallEvent(call_id, name, arguments)


class Hang:
    """Script marker: stop producing events and wait until cancelled."""


class FakeLLM:
    """Scripted ChatStream. Each `stream()` call plays the next script; a
    script is a list of events, optionally ending in `Hang()`."""

    def __init__(self, *scripts: list[Any]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[dict[str, Any]]] = []

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append([dict(m) for m in messages])
        script = self._scripts.pop(0)
        for event in script:
            if isinstance(event, Hang):
                await asyncio.Event().wait()
            elif isinstance(event, Exception):
                raise event
            else:
                yield event


class FakeRunner:
    def __init__(self, results: dict[str, Any] | None = None, allowed: set[str] | None = None) -> None:
        self.results = results or {}
        self.allowed = allowed
        self.called: list[tuple[str, dict[str, Any]]] = []

    def allows(self, name: str) -> bool:
        return self.allowed is None or name in self.allowed

    async def run(self, name: str, args: dict[str, Any]) -> Any:
        from app.agent.tools import ToolNotAllowed

        if not self.allows(name):
            raise ToolNotAllowed(name)
        self.called.append((name, args))
        return self.results.get(name, {})


def relay_session_param(twiml_xml: str) -> str:
    """The session JWT inside a ConversationRelay <Parameter>."""
    root = ET.fromstring(twiml_xml)
    for p in root.iter("Parameter"):
        if p.get("name") == "session":
            return p.get("value") or ""
    raise AssertionError("no session parameter in TwiML")
