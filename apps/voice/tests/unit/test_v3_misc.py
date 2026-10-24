from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agent.personas import CallPurpose, OutboundJob, outbound_context
from app.config import get_settings
from app.main import create_app
from app.session import IdentitySource, Persona, verify_session
from tests.helpers import mock_core_client, relay_session_param

KNOWN_INSTITUTION = "+910000000001"  # mock core: prefers Telugu
KNOWN_FACULTY = "+910000000003"


def post(client, path, **form):
    return client.post(path, data={"CallSid": "CA1", "From": "+919999999999", **form})


@pytest.fixture
def english_only(monkeypatch):
    monkeypatch.setenv("ENABLED_LANGUAGES", "en")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.core = mock_core_client()
        yield c


# -- English-only default ------------------------------------------------------------------
def test_default_setting_is_english_only(monkeypatch):
    monkeypatch.delenv("ENABLED_LANGUAGES")
    get_settings.cache_clear()
    assert get_settings().enabled_language_codes == ["en"]


def test_english_only_has_no_menu_and_goes_straight_on(english_only):
    root = ET.fromstring(post(english_only, "/voice/inbound").text)
    assert root.find("Gather") is None
    assert len(root.findall("Say")) == 1 and "AICTE" in root.find("Say").text
    assert "noinput=1" in root.find("Redirect").text


def test_english_only_ignores_other_languages_everywhere(english_only):
    # caller's stored preference is Telugu, and they press 2 (Hindi)
    r = post(english_only, "/voice/inbound", From=KNOWN_INSTITUTION)
    assert "d=en" in ET.fromstring(r.text).find("Redirect").text
    r = post(english_only, "/voice/language?d=te", From=KNOWN_INSTITUTION, Digits="2")
    assert verify_session(relay_session_param(r.text)).language == "en"
    r = post(english_only, "/voice/connect?lang=ta", From=KNOWN_INSTITUTION)
    claims = verify_session(relay_session_param(r.text))
    assert claims.language == "en"
    assert ET.fromstring(r.text).find("Connect/ConversationRelay").get("language") == "en-IN"


# -- session claims & limits -------------------------------------------------------------------
def test_subject_id_travels_in_the_session(english_only):
    r = post(english_only, "/voice/language", From=KNOWN_FACULTY)
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.subject_id) == (Persona.FACULTY, "fac-001")
    assert claims.identity_source == IdentitySource.CALLER_ID


def test_public_callers_are_limited_per_number_per_day(monkeypatch):
    monkeypatch.setenv("PUBLIC_CALLS_PER_DAY", "2")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.core = mock_core_client()
        for _ in range(2):
            assert ET.fromstring(post(c, "/voice/connect").text).find("Connect") is not None
        third = ET.fromstring(post(c, "/voice/connect").text)
        assert third.find("Connect") is None and third.find("Hangup") is not None
        assert "number of calls allowed today" in third.find("Say").text
        # another number is unaffected, and known callers are never limited
        other = post(c, "/voice/connect", From="+918888888888")
        assert ET.fromstring(other.text).find("Connect") is not None
        for _ in range(4):
            known = post(c, "/voice/connect", From=KNOWN_INSTITUTION)
            assert ET.fromstring(known.text).find("Connect") is not None


# -- outbound scripts ---------------------------------------------------------------------------------
@pytest.mark.parametrize("purpose", list(CallPurpose))
def test_every_call_purpose_has_a_script_with_the_job_facts(purpose):
    job = OutboundJob(
        job_id="j",
        purpose=purpose,
        institution_name="Sri Venkateswara Institute of Technology",
        contact_name="Ramesh Kumar",
        item_title="Lease registration",
        subject_ref="UND-2026-0042",
        due_date=date(2026, 11, 5),
        committed_date=date(2026, 10, 15),
    )
    ctx = outbound_context(job)
    assert purpose.value in ctx and "UND-2026-0042" in ctx and "Ramesh Kumar" in ctx
    assert "2026-11-05" in ctx and "2026-10-15" in ctx
    assert "OUTBOUND CALL" in ctx


def test_visit_confirmation_is_not_a_commitment_call():
    job = OutboundJob("j", CallPurpose.EVC_VISIT_CONFIRM, "Inst", "Ramesh", "EVC visit", "VIS-1")
    ctx = outbound_context(job)
    assert "NOT call record_commitment" in ctx and "not about a deadline" in ctx


def test_blank_public_url_in_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_URL", "")
    get_settings.cache_clear()
    assert get_settings().public_base == "http://localhost:8080"
    monkeypatch.setenv("PUBLIC_VOICE_URL", "https://abc.ngrok-free.app/")
    get_settings.cache_clear()
    assert get_settings().public_base == "https://abc.ngrok-free.app"
