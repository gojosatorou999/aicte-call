from __future__ import annotations

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.agent.tools import ToolNotAllowed, ToolRunner, load_tool_specs, tools_for
from app.config import get_settings
from app.core_client import CoreClient, CoreError, CoreUnavailable
from app.main import create_app
from app.session import Persona, SessionClaims, mint_session, verify_session
from app.utils import drain

CORE = "http://localhost:9000/internal/voice"


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health_ok(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "acp-voice"


def test_health_reports_draining(client):
    drain.mark_draining()
    assert client.get("/health").json()["status"] == "draining"


def test_inbound_plays_bilingual_greeting(client):
    r = client.post("/voice/inbound", data={"CallSid": "CA1", "From": "+919876543210"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Say" in r.text and "recorded" in r.text and "हेल्पलाइन" in r.text


def test_inbound_while_draining_returns_fallback(client):
    drain.mark_draining()
    r = client.post("/voice/inbound", data={"CallSid": "CA1"})
    assert "unable to take your call" in r.text


def test_inbound_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("TWILIO_SIGNATURE_VALIDATION_ENABLED", "true")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("PUBLIC_VOICE_URL", "https://voice.example")
    get_settings.cache_clear()
    form = {"CallSid": "CA1"}
    with TestClient(create_app()) as c:
        assert c.post("/voice/inbound", data=form).status_code == 403
        assert (
            c.post("/voice/inbound", data=form, headers={"X-Twilio-Signature": "nope"}).status_code
            == 403
        )
        sig = RequestValidator("secret").compute_signature(
            "https://voice.example/voice/inbound", form
        )
        assert (
            c.post("/voice/inbound", data=form, headers={"X-Twilio-Signature": sig}).status_code
            == 200
        )


def test_session_roundtrip_and_tamper():
    claims = SessionClaims("CA1", Persona.PUBLIC, "hi", "hash", job_id="j1")
    token = mint_session(claims)
    assert verify_session(token) == claims
    with pytest.raises(jwt.InvalidTokenError):
        verify_session(token[:-2] + "xx")


def test_tool_contract_matches_routes_and_personas():
    specs = load_tool_specs()
    assert len(specs) == 22
    public = {t.name for t in tools_for(Persona.PUBLIC)}
    assert "search_institutions" in public
    assert "get_open_items" not in public and "verify_officer_pin" not in public


@respx.mock
async def test_tool_runner_routes_and_headers():
    route = respx.get(f"{CORE}/public/institutions/inst-001/approval").mock(
        return_value=httpx.Response(200, json={"approved": True})
    )
    core = CoreClient()
    out = await ToolRunner(core, "jwt-abc", Persona.PUBLIC).run(
        "get_public_approval_status", {"institution_id": "inst-001", "academic_year": "2026-27"}
    )
    assert out == {"approved": True}
    req = route.calls.last.request
    assert req.headers["x-voice-session"] == "jwt-abc"
    assert req.headers["authorization"].startswith("Bearer ")
    assert req.url.params["academic_year"] == "2026-27"
    await core.aclose()


@respx.mock
async def test_handoff_and_callback_share_endpoint_with_kind():
    route = respx.post(f"{CORE}/callbacks").mock(return_value=httpx.Response(200, json={}))
    core = CoreClient()
    await ToolRunner(core, "j", Persona.INSTITUTION).run("handoff_to_officer", {"summary": "x"})
    assert route.calls.last.request.read() == b'{"summary":"x","kind":"HANDOFF"}'
    await core.aclose()


async def test_tool_runner_blocks_other_personas_tools():
    core = CoreClient()
    with pytest.raises(ToolNotAllowed):
        await ToolRunner(core, "j", Persona.PUBLIC).run("record_commitment", {})
    with pytest.raises(ToolNotAllowed):
        await ToolRunner(core, "j", Persona.PUBLIC).run("no_such_tool", {})
    await core.aclose()


@respx.mock
async def test_core_error_code_and_circuit_breaker(monkeypatch):
    monkeypatch.setenv("CORE_CIRCUIT_OPEN_AFTER", "2")
    get_settings.cache_clear()
    core = CoreClient()

    respx.get(f"{CORE}/cases/x/status").mock(
        return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND", "params": {}}})
    )
    with pytest.raises(CoreError) as ei:
        await core.get("/cases/x/status")
    assert ei.value.code == "NOT_FOUND" and not isinstance(ei.value, CoreUnavailable)

    boom = respx.get(f"{CORE}/overdue").mock(return_value=httpx.Response(503))
    for _ in range(2):
        with pytest.raises(CoreUnavailable):
            await core.get("/overdue")
    calls = boom.call_count
    with pytest.raises(CoreUnavailable, match="circuit open"):
        await core.get("/overdue")
    assert boom.call_count == calls  # short-circuited, no request made
    await core.aclose()
