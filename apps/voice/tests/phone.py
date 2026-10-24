"""A fake telephone for the Gather graph.

It does what Twilio does: read the TwiML, play the <Say>s, and POST the
caller's answer (speech or keypad) to the <Gather> action URL; on silence it
follows the <Redirect>. Tests then assert on what was said and what reached core.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from tests.helpers import RecordingTransport, mock_core_client


class Turn:
    def __init__(self, xml: str) -> None:
        self.xml = xml
        self.root = ET.fromstring(xml)

    @property
    def says(self) -> list[str]:
        return [e.text or "" for e in self.root.iter("Say")]

    @property
    def text(self) -> str:
        return " ".join(self.says)

    @property
    def gather(self) -> ET.Element | None:
        return self.root.find("Gather")

    @property
    def prompt(self) -> str:
        g = self.gather
        return (g.find("Say").text or "") if g is not None and g.find("Say") is not None else ""

    @property
    def ended(self) -> bool:
        return self.root.find("Hangup") is not None

    @property
    def relay(self) -> ET.Element | None:
        return self.root.find("Connect/ConversationRelay")

    @property
    def node(self) -> str:
        """The node whose prompt this turn ends with."""
        g = self.gather
        return _query(g.get("action"))["n"] if g is not None else ""


def _query(url: str | None) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(urlsplit(url or "").query))


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class Phone:
    def __init__(self, client: TestClient, first: Turn, call_sid: str = "CA1") -> None:
        self.client = client
        self.sid = call_sid
        self.turn = first
        self.history = [first]

    def _post(self, url: str, **form: str) -> Turn:
        r = self.client.post(
            _path(url), data={"CallSid": self.sid, "From": "+910000000000", **form}
        )
        assert r.status_code == 200, r.text
        self.turn = Turn(r.text)
        self.history.append(self.turn)
        return self.turn

    def _action(self) -> str:
        g = self.turn.gather
        assert g is not None, f"nothing to answer; call said: {self.turn.text!r}"
        return g.get("action") or ""

    def say(self, text: str) -> Turn:
        return self._post(self._action(), SpeechResult=text, Confidence="0.9")

    def press(self, digits: str) -> Turn:
        return self._post(self._action(), Digits=digits)

    def silence(self) -> Turn:
        redirect = self.turn.root.find("Redirect")
        assert redirect is not None, "no redirect to follow"
        return self._post(redirect.text or "")


def open_graph(*, mode: str = "gather", llm: Any = None, jobs: dict | None = None, **settings: Any):
    """App in the given mode, mock core behind a recording transport.

    `llm` defaults to a stub that fails the test if anything calls it, which is
    how the suite proves OpenAI is not in the call path in gather mode."""
    s = get_settings()
    s.conversation_mode = mode  # type: ignore[assignment]
    for k, v in settings.items():
        setattr(s, k, v)
    transport = RecordingTransport()
    client = TestClient(create_app())
    client.__enter__()
    client.app.state.core = mock_core_client(transport)
    client.app.state.llm = llm if llm is not None else ForbiddenLLM()
    if jobs:
        client.app.state.jobs.update(jobs)
    return client, transport


class ForbiddenLLM:
    calls = 0

    def stream(self, messages: Any, tools: Any):
        ForbiddenLLM.calls += 1
        raise AssertionError("the LLM must not be called in gather mode")


def dial(client: TestClient, from_number: str = "+919999999999", call_sid: str = "CA1") -> Phone:
    """An inbound call that has chosen English (the only enabled language by default)."""
    r = client.post("/voice/connect?lang=en", data={"CallSid": call_sid, "From": from_number})
    assert r.status_code == 200, r.text
    return Phone(client, Turn(r.text), call_sid)
