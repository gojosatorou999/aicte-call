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


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forwards to the in-process mock core and remembers every request."""

    def __init__(self) -> None:
        self._inner = httpx.ASGITransport(app=mock_core_app)
        self.requests: list[httpx.Request] = []
        # path suffix -> JSON to return instead of asking the mock core
        self.overrides: dict[str, Any] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for suffix, payload in self.overrides.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(200, json=payload)
        return await self._inner.handle_async_request(request)

    def calls(self, method: str, path_suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(path_suffix)]


def mock_core_client(transport: RecordingTransport | None = None) -> CoreClient:
    """A CoreClient wired straight into the in-process mock core."""
    return CoreClient(transport=transport or httpx.ASGITransport(app=mock_core_app))


def token(
    call_sid: str = "CA1",
    persona: Persona = Persona.PUBLIC,
    language: str = "en",
    source: IdentitySource = IdentitySource.ANONYMOUS,
    subject_id: str | None = None,
    job_id: str | None = None,
    verified: bool = False,
    via_graph: bool = False,
) -> str:
    return mint_session(
        SessionClaims(
            call_sid,
            persona,
            language,
            "ref",
            job_id=job_id,
            identity_source=source,
            subject_id=subject_id,
            verified=verified,
            via_graph=via_graph,
        )
    )


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
        self.tool_names: list[list[str]] = []  # tools offered on each call

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append([dict(m) for m in messages])
        self.tool_names.append([t["function"]["name"] for t in tools])
        script = self._scripts.pop(0)
        for event in script:
            if isinstance(event, Hang):
                await asyncio.Event().wait()
            elif isinstance(event, Exception):
                raise event
            else:
                yield event


class FakeRunner:
    def __init__(
        self, results: dict[str, Any] | None = None, allowed: set[str] | None = None
    ) -> None:
        self.results = results or {}
        self.allowed = allowed
        self.called: list[tuple[str, dict[str, Any]]] = []

    def allows(self, name: str) -> bool:
        return self.allowed is None or name in self.allowed

    def schemas(self) -> list[dict[str, Any]]:
        return []

    def note_user(self, text: str) -> None:
        pass

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


# -- relay test harness ----------------------------------------------------------------
def open_relay(llm, *, transport=None, jobs=None, **settings_overrides):
    """Start the app, swap in a scripted LLM and the in-process mock core."""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    settings = get_settings()
    for k, v in settings_overrides.items():
        setattr(settings, k, v)
    client = TestClient(create_app())
    client.__enter__()
    client.app.state.core = mock_core_client(transport)
    client.app.state.llm = llm
    if jobs:
        client.app.state.jobs.update(jobs)
    return client


def setup_msg(**kw) -> str:
    import json

    tok = kw.pop("tok", None) or token(**kw)
    return json.dumps(
        {
            "type": "setup",
            "callSid": kw.get("call_sid", "CA1"),
            "customParameters": {"session": tok},
        }
    )


def recv(ws) -> dict:
    import json

    return json.loads(ws.receive_text())


def reply_text(ws) -> str:
    """Collect text tokens up to and including the `last: true` marker."""
    out = []
    while True:
        m = recv(ws)
        assert m["type"] == "text", m
        out.append(m["token"])
        if m["last"]:
            return "".join(out).strip()


def say(ws, text: str) -> None:
    import json

    ws.send_text(json.dumps({"type": "prompt", "voicePrompt": text, "last": True}))


def press(ws, digits: str) -> None:
    import json

    for d in digits:
        ws.send_text(json.dumps({"type": "dtmf", "digit": d}))


class FakeCalls:
    """Stands in for `client.calls`: `.create(...)` places a call, `calls(sid).fetch()` reads one."""

    def __init__(self, error=None, refuse_extras=False):
        self.created = []
        self.attempts = []  # every create() call, including refused ones
        self.error = error
        self.refuse_extras = refuse_extras  # act like a trial account
        self.statuses = {}  # sid -> status returned by fetch()
        self.fetches = 0

    def __call__(self, sid):
        calls = self

        class Resource:
            def fetch(self):
                calls.fetches += 1
                return type("Call", (), {"status": calls.statuses.get(sid, "queued")})()

        return Resource()

    def create(self, **kw):
        self.attempts.append(kw)
        if self.error:
            raise self.error
        extras = set(kw) - {"to", "from_", "url"}
        if self.refuse_extras and extras:  # exactly what a Twilio trial account answers
            from twilio.base.exceptions import TwilioRestException

            raise TwilioRestException(
                status=400,
                uri="/Accounts/ACx/Calls.json",
                msg="Unable to create record: Invalid or disallowed parameters provided - "
                "trial accounts have limited parameter access, upgrade your account to unlock "
                "full functionality",
                code=0,
            )
        self.created.append(kw)
        return type("Call", (), {"sid": "CAfake1", "status": "queued"})()
