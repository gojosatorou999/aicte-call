from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ivr
from app.core_client import CoreClient
from app.main import create_app
from app.session import IdentitySource, Persona, verify_session
from tests.helpers import mock_core_client, relay_session_param

KNOWN_INSTITUTION = "+910000000001"  # mock_core: ...0001 -> INSTITUTION, prefers Telugu
KNOWN_OFFICER = "+910000000002"


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        c.app.state.core = mock_core_client()
        yield c


def _post(client, path, **form):
    return client.post(path, data={"CallSid": "CA1", "From": "+919999999999", **form})


def test_inbound_menu_offers_all_six_languages(client):
    r = _post(client, "/voice/inbound")
    root = ET.fromstring(r.text)
    gather = root.find("Gather")
    assert gather is not None and gather.get("numDigits") == "1"
    assert gather.get("input") == "dtmf speech"
    assert len(gather.findall("Say")) == 6
    assert "Google.te-IN" in gather.findall("Say")[2].get("voice")
    assert root.find("Redirect") is not None and "noinput=1" in root.find("Redirect").text


def test_inbound_uses_preferred_language_as_default(client):
    r = _post(client, "/voice/inbound", From=KNOWN_INSTITUTION)
    assert "d=te" in ET.fromstring(r.text).find("Gather").get("action")


@pytest.mark.parametrize(
    ("form", "expected"),
    [({"Digits": "2"}, "hi"), ({"SpeechResult": "Telugu please"}, "te"), ({"Digits": "6"}, "kn")],
)
def test_language_choice_for_known_caller_connects_relay(client, form, expected):
    r = _post(client, "/voice/language", From=KNOWN_INSTITUTION, **form)
    relay = ET.fromstring(r.text).find("Connect/ConversationRelay")
    assert relay is not None
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.language) == (Persona.INSTITUTION, expected)
    assert claims.identity_source == IdentitySource.CALLER_ID
    assert claims.call_sid == "CA1"
    assert relay.get("language").startswith(expected)
    assert relay.get("url").endswith("/voice/relay")
    # Raw number must not be in the token.
    assert "9999" not in relay_session_param(r.text)


def test_dual_channel_recording_started_before_relay(client):
    r = _post(client, "/voice/language", From=KNOWN_INSTITUTION, Digits="1")
    root = ET.fromstring(r.text)
    rec = root.find("Start/Recording")
    assert rec.get("channels") == "dual" and rec.get("track") == "both"
    assert [c.tag for c in root][:2] == ["Start", "Connect"]


def test_no_input_falls_back_to_default_language(client):
    r = _post(client, "/voice/language?d=ml&noinput=1", From=KNOWN_OFFICER)
    assert verify_session(relay_session_param(r.text)).language == "ml"


def test_unknown_caller_gets_press_nine_prompt_not_relay(client):
    r = _post(client, "/voice/language", Digits="1")
    root = ET.fromstring(r.text)
    assert root.find("Connect") is None
    assert "/voice/identify" in root.find("Gather").get("action")
    assert "/voice/connect" in root.find("Redirect").text


def test_press_nine_asks_for_registered_number(client):
    r = _post(client, "/voice/identify?lang=en", Digits="9")
    gather = ET.fromstring(r.text).find("Gather")
    assert gather.get("finishOnKey") == "#"
    assert "/voice/identify-number" in gather.get("action")


def test_entered_number_gives_persona_but_marks_it_unverified(client):
    # 10 typed digits are normalised to +91..., which the mock maps to an officer
    r = _post(client, "/voice/identify-number?lang=en", Digits="0000000002")
    claims = verify_session(relay_session_param(r.text))
    assert claims.persona == Persona.OFFICER
    assert claims.identity_source == IdentitySource.ENTERED_NUMBER


def test_unknown_entered_number_continues_as_public_without_telling(client):
    r = _post(client, "/voice/identify-number?lang=en", Digits="123456")
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.identity_source) == (Persona.PUBLIC, IdentitySource.ANONYMOUS)


def test_core_down_degrades_to_public_not_dropped_call():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with TestClient(create_app()) as c:
        c.app.state.core = CoreClient(transport=httpx.MockTransport(boom))
        r = _post(c, "/voice/language", Digits="1")
        assert r.status_code == 200 and "/voice/identify" in r.text
        r = _post(c, "/voice/connect?lang=en")
        assert verify_session(relay_session_param(r.text)).persona == Persona.PUBLIC


def test_status_and_recording_callbacks_return_204(client):
    assert _post(client, "/voice/status", CallStatus="completed", CallDuration="42").status_code == 204
    assert _post(client, "/voice/recording", RecordingUrl="https://api.twilio.com/r/1").status_code == 204


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("9876543210", "+919876543210"), ("919876543210", "+919876543210"), ("", ""), ("12345", "+12345")],
)
def test_normalize_msisdn(raw, expected):
    assert ivr.normalize_msisdn(raw) == expected
