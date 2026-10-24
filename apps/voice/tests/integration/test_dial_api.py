"""The dial pad API and the outbound-call webhook, end to end (Twilio's REST client faked)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app import dial
from app.config import get_settings
from app.session import IdentitySource, Persona, verify_session
from tests.helpers import FakeCalls, relay_session_param
from tests.phone import ForbiddenLLM, open_graph

PAGES = "https://gojosatorou999.github.io"
PUBLIC_URL = "https://abc.ngrok-free.dev"
NUMBER = "+919876543210"


@pytest.fixture
def api():
    opened = []

    def _open(*, error=None, mode="live", **settings):
        s = get_settings()
        s.twilio_account_sid, s.twilio_voice_number = "ACtest", "+15005550006"
        s.twilio_auth_token = type(s.twilio_auth_token)("tok")
        s.public_voice_url = PUBLIC_URL
        client, core = open_graph(mode=mode, **settings)
        calls = FakeCalls(error)
        client.app.state.dialer = dial.Dialer(
            s, client_factory=lambda sid, tok: type("C", (), {"calls": calls})()
        )
        opened.append(client)
        return client, calls, core

    ForbiddenLLM.calls = 0
    yield _open
    for c in opened:
        c.__exit__(None, None, None)
    assert ForbiddenLLM.calls == 0


def post_call(client, phone=NUMBER, **extra):
    return client.post("/dial/call", data={"phone": phone, **extra}, headers={"origin": PAGES})


# -- /dial/call ----------------------------------------------------------------------------------
def test_call_me_places_a_twilio_call_to_the_number_with_our_webhook(api):
    client, calls, _ = api()
    r = post_call(client, "+91 98765 43210")
    assert r.status_code == 200
    body = r.json()
    assert body["call_sid"] == "CAfake1" and body["status"] == "queued"
    sent = calls.created[0]
    assert sent["to"] == NUMBER
    assert sent["url"].startswith(f"{PUBLIC_URL}/voice/outbound-twiml?d=")
    assert sent["status_callback"] == f"{PUBLIC_URL}/voice/status"


def test_old_page_contract_still_works(api):
    """The earlier page posted form-encoded `phone` and read `call_sid` / `detail`."""
    client, _, _ = api()
    r = client.post(
        "/dial/call",
        data={"phone": "9876543210"},
        headers={"ngrok-skip-browser-warning": "true", "origin": PAGES},
    )
    assert r.status_code == 200 and "call_sid" in r.json()
    bad = client.post("/dial/call", data={"phone": "123"})
    assert bad.status_code == 400 and "detail" in bad.json()


def test_cors_allows_the_dial_pad_origin_and_the_ngrok_header_only(api):
    client, _, _ = api()
    pre = client.options(
        "/dial/call",
        headers={
            "origin": PAGES,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type,ngrok-skip-browser-warning",
        },
    )
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == PAGES
    assert "ngrok-skip-browser-warning" in pre.headers["access-control-allow-headers"].lower()
    assert post_call(client).headers["access-control-allow-origin"] == PAGES

    other = client.options(
        "/dial/call",
        headers={"origin": "https://evil.example", "access-control-request-method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers


def test_untouched_when_twilio_credentials_are_missing(api):
    client, calls, _ = api()
    get_settings().twilio_account_sid = ""
    r = post_call(client)
    assert r.status_code == 503 and "TWILIO_ACCOUNT_SID" in r.json()["detail"]
    assert calls.created == []
    # and a misconfigured server does not eat the person's allowance
    get_settings().twilio_account_sid = "ACtest"
    assert post_call(client).status_code == 200


def test_trial_account_unverified_number_gets_a_clear_message(api):
    from twilio.base.exceptions import TwilioRestException

    client, _, _ = api(error=TwilioRestException(status=400, uri="/Calls", msg="x", code=21608))
    r = post_call(client)
    assert r.status_code == 403 and "isn't verified" in r.json()["detail"]


def test_limits_and_country_allowlist_apply(api):
    client, calls, _ = api(dial_per_number_per_hour=1)
    assert post_call(client).status_code == 200
    again = post_call(client)
    assert again.status_code == 429 and "recently" in again.json()["detail"]
    abroad = post_call(client, "+14155552671")
    assert abroad.status_code == 403
    assert len(calls.created) == 1


def test_optional_access_code(api):
    from pydantic import SecretStr

    client, calls, _ = api(dial_pad_token=SecretStr("open-sesame"))
    assert client.get("/dial/config").json()["code_required"] is True
    assert post_call(client).status_code == 401
    assert post_call(client, code="wrong").status_code == 401
    assert post_call(client, code="open-sesame").status_code == 200
    bearer = client.post(
        "/dial/call",
        data={"phone": "+919876543211"},
        headers={"authorization": "Bearer open-sesame"},
    )
    assert bearer.status_code == 200 and len(calls.created) == 2


def test_dial_pad_can_be_switched_off(api):
    client, calls, _ = api(dial_pad_enabled=False)
    assert post_call(client).status_code == 404 and calls.created == []


def test_scenarios_are_refused_unless_demo_scenarios_is_on(api):
    client, calls, _ = api()
    assert client.get("/dial/config").json()["scenarios"] == {}
    assert post_call(client, scenario="officer").status_code == 403
    assert post_call(client, scenario="nonsense").status_code == 400
    assert calls.created == []


def test_status_endpoint_follows_twilio_status_callbacks(api):
    client, _, _ = api()
    sid = post_call(client).json()["call_sid"]
    assert client.get(f"/dial/status/{sid}").json()["status"] == "queued"
    for status in ("ringing", "in-progress", "completed"):
        r = client.post("/voice/status", data={"CallSid": sid, "CallStatus": status})
        assert r.status_code == 204
        assert client.get(f"/dial/status/{sid}").json()["status"] == status
    assert client.get("/dial/status/CA-nope").status_code == 404


# -- /voice/outbound-twiml ---------------------------------------------------------------------
def answer(client, calls, to=NUMBER, sid="CAfake1"):
    """Place the call, then do what Twilio does when the phone is answered."""
    r = post_call(client, to, scenario=getattr(answer, "scenario", "public"))
    assert r.status_code == 200, r.text
    url = calls.created[-1]["url"]
    path = url.removeprefix(PUBLIC_URL)
    return client.post(
        path, data={"CallSid": sid, "To": to, "From": "+15005550006", "Direction": "outbound-api"}
    )


def test_answered_call_connects_the_live_graph_with_a_human_voice(api):
    client, calls, core = api()
    r = answer(client, calls)
    relay = ET.fromstring(r.text).find("Connect/ConversationRelay")
    assert relay is not None
    assert relay.get("url") == "wss://abc.ngrok-free.dev/voice/live"
    assert relay.get("language") == "en-IN"
    assert "AICTE assistant, calling as you requested" in relay.get("welcomeGreeting")
    assert (
        relay.get("voice") is None and relay.get("ttsProvider") is None
    )  # Twilio's default human voice
    assert (
        relay.get("interruptSensitivity") == "medium" and relay.get("ignoreBackchannel") == "true"
    )
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.identity_source) == (Persona.PUBLIC, IdentitySource.ANONYMOUS)
    assert claims.language == "en"
    reg = core.calls("POST", "/calls")[0]
    assert b'"OUTBOUND"' in reg.read()


def test_voice_overrides_reach_the_twiml(api):
    client, calls, _ = api(
        relay_tts_provider="ElevenLabs",
        relay_voice="abc123-flash_v2_5",
        relay_stt_provider="Deepgram",
        relay_stt_model="nova-3-general",
    )
    relay = ET.fromstring(answer(client, calls).text).find("Connect/ConversationRelay")
    assert (relay.get("ttsProvider"), relay.get("voice")) == ("ElevenLabs", "abc123-flash_v2_5")
    assert (relay.get("transcriptionProvider"), relay.get("speechModel")) == (
        "Deepgram",
        "nova-3-general",
    )


def test_a_registered_number_gets_its_own_persona_without_demo_mode(api):
    client, calls, _ = api()
    r = answer(client, calls, to="+910000000001")  # mock core: an institution contact
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.subject_id) == (Persona.INSTITUTION, "inst-001")
    assert claims.identity_source == IdentitySource.CALLER_ID


@pytest.mark.parametrize(
    ("scenario", "persona", "identity", "has_job"),
    [
        ("institution", Persona.INSTITUTION, IdentitySource.OUTBOUND_JOB, True),
        ("officer", Persona.OFFICER, IdentitySource.CALLER_ID, False),
        ("faculty", Persona.FACULTY, IdentitySource.CALLER_ID, False),
        ("public", Persona.PUBLIC, IdentitySource.ANONYMOUS, False),
    ],
)
def test_demo_scenarios_choose_the_conversation(api, scenario, persona, identity, has_job):
    client, calls, _ = api(demo_scenarios=True)
    answer.scenario = scenario
    try:
        r = answer(client, calls)
    finally:
        del answer.scenario
    claims = verify_session(relay_session_param(r.text))
    assert (claims.persona, claims.identity_source) == (persona, identity)
    assert bool(claims.job_id) is has_job
    if has_job:
        assert claims.job_id in client.app.state.jobs
    assert set(client.get("/dial/config").json()["scenarios"]) == set(dial.SCENARIOS)


def test_gather_mode_outbound_starts_the_graph_with_the_greeting(api):
    client, calls, _ = api(mode="gather")
    root = ET.fromstring(answer(client, calls).text)
    says = [e.text for e in root.iter("Say")]
    assert any("calling as you requested" in t for t in says)
    assert root.find("Gather") is not None and root.find("Connect") is None


def test_expired_or_unknown_call_link_hangs_up_politely(api):
    client, _, _ = api()
    r = client.post("/voice/outbound-twiml?d=nope", data={"CallSid": "CA1", "To": NUMBER})
    root = ET.fromstring(r.text)
    assert "expired" in root.find("Say").text and root.find("Hangup") is not None


def test_trial_account_flow_places_the_call_with_the_basics_and_polls_for_progress(api):
    client, calls, _ = api()
    calls.refuse_extras = True
    r = post_call(client)
    assert r.status_code == 200 and r.json()["call_sid"] == "CAfake1"
    assert len(calls.attempts) == 2 and set(calls.created[0]) == {"to", "from_", "url"}

    # no Twilio callbacks on a trial account: the status endpoint asks Twilio instead
    sid = r.json()["call_sid"]
    calls.statuses[sid] = "ringing"
    assert client.get(f"/dial/status/{sid}").json()["status"] == "ringing"
    fetched = calls.fetches
    client.get(f"/dial/status/{sid}")
    assert calls.fetches == fetched  # throttled: not asked again within 1.5 s

    client.app.state.dials.by_sid(sid).checked = 0.0
    calls.statuses[sid] = "completed"
    assert client.get(f"/dial/status/{sid}").json()["status"] == "completed"
    after = calls.fetches
    client.get(f"/dial/status/{sid}")
    assert calls.fetches == after  # finished calls are never asked about again


# -- unsigned TwiML fetches (Twilio's restricted trial tier sends no X-Twilio-Signature) ---------
def placed(api, **settings):
    """Place a dial pad call with signature checking ON; returns what Twilio would then send."""
    client, calls, core = api(twilio_signature_validation_enabled=True, **settings)
    assert post_call(client).status_code == 200
    path = calls.created[-1]["url"].removeprefix(PUBLIC_URL)
    good = {"AccountSid": "ACtest", "CallSid": "CAfake1", "To": NUMBER, "From": "+15005550006"}
    return client, path, good


def test_unsigned_fetch_is_accepted_only_when_it_proves_it_belongs_to_the_call(api):
    client, path, good = placed(api)
    r = client.post(path, data=good)  # no signature header at all
    assert r.status_code == 200
    assert ET.fromstring(r.text).find("Connect/ConversationRelay") is not None


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"AccountSid": "ACother"}, "someone else's account"),
        ({"CallSid": "CAnotours"}, "a call we did not place"),
        ({"To": "+919000000000"}, "a different number than we dialled"),
        ({"AccountSid": ""}, "missing account"),
    ],
)
def test_unsigned_fetch_with_the_wrong_proof_is_refused(api, change, why):
    client, path, good = placed(api)
    assert client.post(path, data={**good, **change}).status_code == 403, why


def test_unsigned_fetch_needs_the_unguessable_id_and_cannot_be_replayed(api):
    client, path, good = placed(api)
    assert client.post("/voice/outbound-twiml?d=guess", data=good).status_code == 403
    assert client.post("/voice/outbound-twiml", data=good).status_code == 403
    for _ in range(3):
        assert client.post(path, data=good).status_code == 200  # Twilio may retry a fetch
    assert client.post(path, data=good).status_code == 403  # a fourth is a replay


def test_unsigned_fetch_can_be_switched_off(api):
    client, path, good = placed(api, dial_accept_unsigned_twiml=False)
    assert client.post(path, data=good).status_code == 403


def test_a_wrong_signature_is_refused_even_though_a_missing_one_is_tolerated(api):
    client, path, good = placed(api)
    assert client.post(path, data=good, headers={"X-Twilio-Signature": "forged"}).status_code == 403


def test_a_correct_signature_is_accepted_without_the_extra_proof(api):
    from twilio.request_validator import RequestValidator

    client, path, _ = placed(api)
    form = {"CallSid": "CAanything", "To": "+919000000000"}  # signed requests need no other proof
    sig = RequestValidator("tok").compute_signature(PUBLIC_URL + path, form)
    assert client.post(path, data=form, headers={"X-Twilio-Signature": sig}).status_code == 200


def test_every_other_endpoint_still_requires_a_signature(api):
    client, _, good = placed(api)
    for path in ("/voice/inbound", "/voice/connect?lang=en", "/voice/status", "/voice/recording"):
        assert client.post(path, data=good).status_code == 403, path


# -- the page explains a call that ended before the live voice connected --------------------------
def finish(client, sid, status="completed"):
    client.post("/voice/status", data={"CallSid": sid, "CallStatus": status})
    return client.get(f"/dial/status/{sid}").json()


def test_a_call_that_ends_before_the_voice_connects_is_diagnosed(api):
    client, calls, _ = api()
    r = answer(client, calls)  # Twilio fetched our TwiML...
    assert ET.fromstring(r.text).find("Connect/ConversationRelay") is not None
    body = finish(client, "CAfake1")  # ...but the call ended and the WebSocket never opened
    assert body["status"] == "completed"
    assert "ConversationRelay" in body["hint"] and "AI/ML Features Addendum" in body["hint"]


def test_no_diagnosis_when_the_voice_did_connect(api):
    from tests.helpers import reply_text, setup_msg

    client, calls, _ = api()
    r = answer(client, calls)
    token = relay_session_param(r.text)
    with client.websocket_connect("/voice/live") as ws:
        ws.send_text(setup_msg(tok=token, call_sid="CAfake1"))
        assert reply_text(ws)  # the graph speaks first
    assert "hint" not in finish(client, "CAfake1")


def test_no_diagnosis_for_a_call_nobody_answered_or_in_gather_mode(api):
    client, calls, _ = api()
    assert post_call(client).status_code == 200  # placed, never answered: TwiML never fetched
    assert "hint" not in finish(client, "CAfake1", "no-answer")

    client2, calls2, _ = api(mode="gather")
    answer(client2, calls2)
    assert "hint" not in finish(client2, "CAfake1")


def test_no_operator_advice_is_shown_in_production(api):
    from app.config import AppEnv

    client, calls, _ = api()
    answer(client, calls)
    get_settings().app_env = AppEnv.PRODUCTION
    assert "hint" not in finish(client, "CAfake1")


# -- the conversation steps of a trial-tier (unsigned) call carry a per-call key ------------------
def gather_call(api, **settings):
    """A dial pad call in gather mode with signature checking ON, answered the way the trial tier
    answers it (unsigned). Returns the client, the first Gather's action path, and a good form."""
    client, calls, _ = api(mode="gather", twilio_signature_validation_enabled=True, **settings)
    assert post_call(client).status_code == 200
    path = calls.created[-1]["url"].removeprefix(PUBLIC_URL)
    form = {"AccountSid": "ACtest", "CallSid": "CAfake1", "To": NUMBER, "From": "+15005550006"}
    twiml = client.post(path, data=form)
    assert twiml.status_code == 200, twiml.text
    action = ET.fromstring(twiml.text).find("Gather").get("action").removeprefix(PUBLIC_URL)
    return client, action, {**form, "SpeechResult": "Sri Venkateswara"}


def test_every_action_url_carries_a_secret_per_call_key(api):
    client, action, _ = gather_call(api)
    assert "k=" in action and "n=pub_menu" in action
    call = client.app.state.calls.get("CAfake1")
    assert f"k={call.key}" in action and len(call.key) >= 16


def test_unsigned_conversation_step_with_the_key_is_accepted_and_continues(api):
    client, action, form = gather_call(api)
    r = client.post(action, data=form)  # no signature header, as the trial tier sends it
    assert r.status_code == 200
    nxt = ET.fromstring(r.text).find("Gather").get("action")
    assert "n=pub_more" in nxt  # the conversation moved on (the college was looked up)
    again = client.post(
        nxt.removeprefix(PUBLIC_URL), data={**form, "SpeechResult": "which courses"}
    )
    assert again.status_code == 200


@pytest.mark.parametrize(
    "tamper",
    [
        lambda a: a.replace("k=", "k=x"),  # wrong key
        lambda a: a.split("&k=")[0].split("?")[0] + "?n=pub_menu",  # key missing
    ],
)
def test_unsigned_step_without_or_with_the_wrong_key_is_refused(api, tamper):
    client, action, form = gather_call(api)
    assert client.post(tamper(action), data=form).status_code == 403


def test_unsigned_step_from_another_account_or_unknown_call_is_refused(api):
    client, action, form = gather_call(api)
    assert client.post(action, data={**form, "AccountSid": "ACother"}).status_code == 403
    assert client.post(action, data={**form, "CallSid": "CAnotours"}).status_code == 403


def test_unsigned_steps_can_be_switched_off_and_wrong_signatures_never_pass(api):
    client, action, form = gather_call(api)
    assert (
        client.post(action, data=form, headers={"X-Twilio-Signature": "forged"}).status_code == 403
    )
    get_settings().dial_accept_unsigned_twiml = False
    assert client.post(action, data=form).status_code == 403


def test_a_correct_signature_works_on_conversation_steps(api):
    from twilio.request_validator import RequestValidator

    client, action, form = gather_call(api)
    sig = RequestValidator("tok").compute_signature(PUBLIC_URL + action, form)
    assert client.post(action, data=form, headers={"X-Twilio-Signature": sig}).status_code == 200
