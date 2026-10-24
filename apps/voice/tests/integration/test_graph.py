"""The Gather graph, end to end: a fake phone follows the TwiML the service
returns, exactly as Twilio would, against the mock core. No LLM is involved
(the stub in `open_graph` fails the test if one is called)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.agent.personas import CallPurpose, OutboundJob
from app.config import get_settings
from app.session import IdentitySource, Persona, SessionClaims, verify_session
from tests.helpers import relay_session_param
from tests.phone import ForbiddenLLM, Phone, Turn, dial, open_graph

INSTITUTION = "+910000000001"
OFFICER = "+910000000002"
FACULTY = "+910000000003"
PIN = "246810"
OTP = "123456"


@pytest.fixture
def graph():
    opened = []

    def _open(**kw):
        client, core = open_graph(**kw)
        opened.append(client)
        return client, core

    ForbiddenLLM.calls = 0
    yield _open
    for c in opened:
        c.__exit__(None, None, None)
    assert ForbiddenLLM.calls == 0


def body(req) -> dict:
    return json.loads(req.read())


# -- public ---------------------------------------------------------------------------------------
def test_public_approval_lookup_without_any_llm(graph):
    client, core = graph()
    phone = dial(client)
    assert "AICTE assistant" in phone.turn.text and "How can I help" in phone.turn.prompt
    assert phone.turn.node == "pub_menu"
    assert phone.turn.gather.get("bargeIn") == "true"

    t = phone.say("I want to check an institution")
    assert "name of the institution" in t.prompt

    t = phone.say("Sri Venkateswara")
    assert "Sri Venkateswara Institute of Technology is approved for 2026-27" in t.text
    assert "B.Tech CSE, intake 120" in t.text and "B.Tech ECE, intake 60" in t.text
    assert "anything else" in t.prompt.lower()
    assert all(
        "/public/" in r.url.path
        for r in core.requests
        if "/callers/" not in r.url.path and "/calls" not in r.url.path
    )


def test_public_can_say_the_whole_question_at_once(graph):
    client, _ = graph()
    phone = dial(client)
    t = phone.say("is Sri Venkateswara Institute of Technology approved")
    assert "is approved for 2026-27" in t.text


def test_public_multiple_matches_are_listed_and_picked_by_voice_or_keypad(graph):
    client, _ = graph()
    phone = dial(client)
    phone.say("check an institution")
    t = phone.say("Sri")
    assert "I found 2 matches" in t.text
    assert "1: Sri Venkateswara Institute of Technology, Anantapur" in t.prompt
    assert "2: Sri Sai College of Engineering, Chennai" in t.prompt
    assert "Sri Sai College of Engineering is approved" in phone.press("2").text

    phone = dial(client, call_sid="CA2")
    phone.say("check an institution")
    phone.say("Sri")
    assert "Venkateswara" in phone.say("the first one").text


def test_public_unknown_institution_retries_then_gives_up_politely(graph):
    client, _ = graph()
    phone = dial(client)
    phone.say("check an institution")
    t = phone.say("Nonexistent University")
    assert "could not find" in t.text and t.node == "pub_inst_name"
    t = phone.say("Nonexistent University")
    assert "AICTE website" in t.text and t.node == "pub_menu"


def test_public_complaint_is_read_back_then_filed_once_with_spelled_ticket(graph):
    client, core = graph()
    phone = dial(client)
    phone.say("I want to file a complaint")
    phone.say("Green Valley College")
    t = phone.say("hi")
    assert "more detail" in t.text  # too short
    t = phone.say("They are running courses without any AICTE approval and taking fees")
    assert "Green Valley College" in t.prompt and "Say yes to file it" in t.prompt
    assert core.calls("POST", "/grievances") == []

    t = phone.say("yes")
    assert "G, R, V" not in t.text  # separators are per part, see spell()
    assert "ticket number is G R V, 2 0 2 6, 0 0 0 1" in t.text
    sent = body(core.calls("POST", "/grievances")[0])
    assert sent["category"] == "FAKE_COLLEGE" and sent["institution_ref"] == "Green Valley College"
    assert len(core.calls("POST", "/grievances")) == 1


def test_public_can_cancel_a_complaint(graph):
    client, core = graph()
    phone = dial(client)
    phone.say("complaint")
    phone.say("Some College")
    phone.say("The fees they charge are far more than allowed")
    t = phone.say("no")
    assert "have not filed anything" in t.text
    assert core.calls("POST", "/grievances") == []


def test_public_lookup_limit_per_call(graph):
    client, _ = graph(public_lookups_per_call=1)
    phone = dial(client)
    phone.say("check an institution")
    phone.say("Sri Venkateswara")
    phone.say("check another institution")
    t = phone.say("Sri Sai")
    assert "only look up a few institutions" in t.text


def test_unclear_menu_answers_end_in_an_officer_callback_without_llm(graph):
    client, core = graph()
    phone = dial(client)
    assert "did not understand" in phone.say("banana").text
    assert "did not understand" in phone.say("purple").text
    t = phone.say("elephant")
    assert t.ended and "asked an officer to call you back" in t.text
    assert body(core.calls("POST", "/callbacks")[0])["kind"] == "HANDOFF"


# -- officer ----------------------------------------------------------------------------------------
def officer_phone(graph, **kw):
    client, core = graph(**kw)
    phone = dial(client, OFFICER)
    assert phone.turn.node == "verify_pin"
    g = phone.turn.gather
    assert g.get("numDigits") == "6" and g.get("finishOnKey") == "#" and g.get("input") == "dtmf"
    assert g.get("bargeIn") is None  # nothing may talk over the PIN prompt
    return client, core, phone


def test_officer_pin_then_queue_overdue_report_and_confirmed_reminder(graph):
    client, core, phone = officer_phone(graph)
    t = phone.press("000000")
    assert "did not match" in t.text and t.node == "verify_pin"
    t = phone.press(PIN)
    assert "you are verified" in t.text and "Say: queue, overdue" in t.prompt
    assert len(core.calls("POST", "/officers/verify-pin")) == 2

    assert "14 pending, 3 overdue, and 5 due this week" in phone.say("what is in my queue").text
    t = phone.say("show me overdue items")
    assert "stages, undertakings, documents, or visits" in t.prompt
    t = phone.say("undertakings")
    assert "3 overdue undertaking items" in t.text and "A P P, 2 0 2 6, 0 4 0 0" in t.text

    t = phone.say("remind")
    assert "send the institution a reminder" in t.prompt
    assert core.calls("POST", "/reminders") == []
    t = phone.say("yes")
    assert "reminder has been sent" in t.text
    assert body(core.calls("POST", "/reminders")[0])["confirmed"] is True

    t = phone.say("email me a pendency report")
    assert "emailed the pendency report" in t.text
    assert body(core.calls("POST", "/reports/email")[0])["report_type"] == "pendency"

    assert "applications are in progress" in phone.say("region statistics").text
    assert phone.say("goodbye").ended


def test_officer_keypad_pin_is_never_kept_in_state_or_transcript(graph):
    client, _, phone = officer_phone(graph)
    phone.press(PIN)
    phone.say("queue")
    call = client.app.state.calls.get("CA1")
    dump = json.dumps([(line.role, line.text) for line in call.transcript])
    assert PIN not in dump and PIN not in repr(call.slots)
    assert call.verifier is None


def test_officer_is_locked_out_after_three_wrong_pins(graph):
    client, core, phone = officer_phone(graph)
    assert "did not match" in phone.press("111111").text
    assert "did not match" in phone.press("222222").text
    t = phone.press("333333")
    assert t.ended and "Too many incorrect attempts" in t.text
    assert len(core.calls("GET", "/officers/me/queue-summary")) == 0


def test_officer_short_pin_with_hash_counts_as_an_attempt(graph):
    _, _, phone = officer_phone(graph)
    assert "did not match" in phone.press("123").text


def test_officer_reminder_needs_an_overdue_list_first(graph):
    _, _, phone = officer_phone(graph)
    phone.press(PIN)
    t = phone.say("send a reminder")
    assert "Ask for an overdue list first" in t.text


def test_officer_cannot_reach_tools_before_verifying(graph):
    client, core, phone = officer_phone(graph)
    phone.press("999999")
    call = client.app.state.calls.get("CA1")
    assert call.runner.policy.verification_needed().value == "PIN"
    assert not core.calls("GET", "/officers/me/queue-summary")


# -- faculty ----------------------------------------------------------------------------------------------
def test_faculty_otp_then_status_and_grievance_with_session_bound_id(graph):
    client, core = graph()
    phone = dial(client, FACULTY)
    assert core.calls("POST", "/otp/send") and phone.turn.node == "verify_otp"
    assert "six digit code" in phone.turn.prompt
    assert "did not match" in phone.press("000000").text
    t = phone.press(OTP)
    assert "you are verified" in t.text

    t = phone.say("what is my status")
    assert "registration status is active" in t.text
    assert "duplicate flag" in t.text and "no-objection letter" in t.text
    assert core.calls("GET", "/faculty/fac-001")

    phone.say("I want to file a complaint")
    t = phone.say("I never worked at the other institution that is flagged")
    assert "Say yes to file it" in t.prompt
    phone.say("yes")
    sent = body(core.calls("POST", "/grievances")[0])
    assert sent["category"] == "FACULTY_RECORD"


# -- institution (inbound) -----------------------------------------------------------------------------------
def test_institution_open_items_then_commitment_confirmed_and_link_sent(graph):
    client, core = graph()
    phone = dial(client, INSTITUTION)
    assert phone.turn.node == "inst_menu"
    t = phone.say("what are my open items")
    assert "You have 1 open item. Lease registration, due Thursday, 5 November 2026" in t.text

    t = phone.say("I want to record a commitment")
    assert "By when will you complete Lease registration" in t.prompt
    t = phone.say("by the 20th of November")
    assert "reason" in t.prompt
    t = phone.say("the lease registration is pending at the sub registrar")
    assert "Friday, 20 November 2026" in t.prompt and "Is that correct" in t.prompt
    assert core.calls("POST", "/commitments") == []  # read back, not yet saved

    t = phone.say("yes")
    saved = body(core.calls("POST", "/commitments")[0])
    assert saved["committed_date"] == "2026-11-20" and saved["subject_ref"] == "UND-2026-0042"
    assert "pending at the sub registrar" in saved["reason"]
    assert "upload link" in t.prompt

    t = phone.say("send it on WhatsApp")
    assert body(core.calls("POST", "/upload-links")[0])["channel"] == "WHATSAPP"
    assert "Is there anything else" in t.prompt


def test_institution_wrong_date_is_corrected_before_saving(graph):
    client, core = graph()
    phone = dial(client, INSTITUTION)
    phone.say("commitment")
    phone.say("next Friday")
    phone.say("skip")
    t = phone.say("no")
    assert "By when will you complete" in t.prompt
    assert core.calls("POST", "/commitments") == []


def test_institution_date_beyond_policy_becomes_an_extension_request_only(graph):
    client, core = graph()
    phone = dial(client, INSTITUTION)
    phone.say("commitment")
    t = phone.say("the 25th of December")
    assert "Only the competent authority can grant extensions" in t.text
    assert "Monday, 23 November 2026" in t.text  # the latest recordable date
    assert core.calls("POST", "/commitments") == []

    t = phone.say("extension request")
    assert "reason you need more time" in t.prompt
    t = phone.say("the court case is still going on")
    assert "for an officer to review. This does not grant an extension" in t.prompt
    t = phone.say("yes")
    assert "logged for officer review" in t.text
    sent = body(core.calls("POST", "/extension-requests")[0])
    assert sent["requested_date"] == "2026-12-25"
    assert core.calls("POST", "/commitments") == []


def test_institution_past_date_and_gibberish_dates(graph):
    client, core = graph()
    phone = dial(client, INSTITUTION)
    phone.say("commitment")
    assert "already passed" in phone.say("20th October 2026").text
    assert "did not understand" in phone.say("sometime soon").text
    assert core.calls("POST", "/commitments") == []


def test_institution_with_two_items_asks_which(graph):
    client, core = graph()
    core.overrides["/open-items"] = {
        "items": [
            {"subject_ref": "UND-1", "title": "Lease registration", "due_date": "2026-11-05"},
            {
                "subject_ref": "DOC-2",
                "title": "Fire safety certificate",
                "due_date": "2026-11-10",
                "overdue": True,
            },
        ]
    }
    phone = dial(client, INSTITUTION)
    t = phone.say("commitment")
    assert "1: Lease registration" in t.prompt and "2: Fire safety certificate" in t.prompt
    t = phone.press("2")
    assert "Fire safety certificate" in t.prompt
    phone.say("tomorrow")
    phone.say("skip")
    phone.say("yes")
    assert body(core.calls("POST", "/commitments")[0])["subject_ref"] == "DOC-2"


def test_typed_number_institution_cannot_use_commitment_flow(graph):
    client, core = graph()
    # unknown caller line, but they typed a registered institution number ("press 9" path)
    r = client.post(
        "/voice/identify-number?lang=en",
        data={"CallSid": "CA1", "From": "+919999999999", "Digits": "0000000001"},
    )
    phone = Phone(client, Turn(r.text))
    assert phone.turn.node == "inst_limited" and "could not verify this number" in phone.turn.prompt
    assert not core.calls("GET", "/open-items")

    t = phone.say("no")
    assert t.ended
    phone = dial(
        client, "+919999999999", call_sid="CA2"
    )  # same again, this time accept the callback
    r = client.post(
        "/voice/identify-number?lang=en",
        data={"CallSid": "CA3", "From": "+919999999999", "Digits": "0000000001"},
    )
    phone = Phone(client, Turn(r.text), "CA3")
    t = phone.say("yes please")
    assert t.ended and "asked an officer to call you back" in t.text
    assert body(core.calls("POST", "/callbacks")[0])["kind"] == "HANDOFF"
    assert core.calls("POST", "/commitments") == []


# -- institution (outbound) --------------------------------------------------------------------------------------
JOB = OutboundJob(
    job_id="j1",
    purpose=CallPurpose.UNDERTAKING_DUE,
    institution_name="Sri Venkateswara Institute of Technology",
    contact_name="Ramesh Kumar",
    item_title="Lease registration",
    subject_ref="UND-2026-0042",
    due_date=date(2026, 11, 5),
)


def outbound_phone(graph, job=JOB):
    from functools import partial

    from app.agent.tools import ToolRunner  # noqa: F401
    from app.graph.start import start_call
    from app.session import mint_session
    from tests.helpers import mock_core_client

    client, core = graph(jobs={job.job_id: job})
    claims = SessionClaims(
        "CA9", Persona.INSTITUTION, "en", "ref", job_id=job.job_id,
        identity_source=IdentitySource.OUTBOUND_JOB, subject_id="inst-001",
    )  # fmt: skip
    xml = client.portal.call(
        partial(
            start_call,
            claims=claims,
            token=mint_session(claims),
            core=client.app.state.core,
            store=client.app.state.calls,
            settings=get_settings(),
            job=job,
            recording_callback=None,
        )
    )
    del mock_core_client
    return client, core, Phone(client, Turn(xml), "CA9")


def test_outbound_commitment_call_end_to_end(graph):
    client, core, phone = outbound_phone(graph)
    assert "automated call from AICTE" in phone.turn.prompt
    assert "Sri Venkateswara Institute of Technology" in phone.turn.prompt
    assert "Ramesh Kumar" in phone.turn.prompt

    t = phone.say("yes speaking")
    assert "Your undertaking, Lease registration, is due on Thursday, 5 November 2026." in t.text
    assert "By when will you complete Lease registration" in t.prompt
    phone.say("next Friday")
    phone.say("skip")
    t = phone.say("yes that is correct")
    assert "Recorded" in t.text
    t = phone.say("no thanks")
    assert t.ended and "Goodbye" in t.text
    assert body(core.calls("POST", "/commitments")[0])["committed_date"] == "2026-10-30"
    assert client.app.state.calls.get("CA9") is None  # state dropped at hang-up


def test_outbound_wrong_person_gets_a_scheduled_callback(graph):
    client, core, phone = outbound_phone(graph)
    t = phone.say("no, he is not here")
    assert "good time to call back" in t.prompt
    t = phone.say("tomorrow")
    assert "What time?" in t.text
    t = phone.say("3 pm")
    assert "Sunday, 25 October 2026 at 3 P M" in t.prompt
    t = phone.say("yes")
    assert t.ended
    sent = body(core.calls("POST", "/callbacks")[0])
    assert sent["kind"] == "CALLBACK" and sent["when"] == "2026-10-25T15:00:00+05:30"
    assert sent["contact_name"] == "Ramesh Kumar"


def test_outbound_visit_confirmation_never_records_a_commitment(graph):
    visit = OutboundJob(
        "j2", CallPurpose.EVC_VISIT_CONFIRM, "Sri Venkateswara Institute of Technology",
        "Ramesh Kumar", "EVC visit", "VIS-1", due_date=date(2026, 11, 12),
    )  # fmt: skip
    client, core, phone = outbound_phone(graph, visit)
    t = phone.say("yes")
    assert "expert committee visit is scheduled for Thursday, 12 November 2026" in t.text
    assert "campus will be ready" in t.prompt
    t = phone.say("no we are closed that day")
    assert t.ended and "I will not change the date myself" in t.text
    assert body(core.calls("POST", "/callbacks")[0])["kind"] == "HANDOFF"
    assert core.calls("POST", "/commitments") == []


# -- global behaviour ----------------------------------------------------------------------------------------------------
def test_silence_reprompts_twice_then_ends_the_call(graph):
    client, _ = graph()
    phone = dial(client)
    for _ in range(2):
        t = phone.silence()
        assert "did not hear anything" in t.text and t.node == "pub_menu"
    t = phone.silence()
    assert t.ended and "unable to hear you" in t.text


def test_empty_speech_counts_as_silence(graph):
    client, _ = graph()
    phone = dial(client)
    assert "did not hear anything" in phone.say("").text


def test_abuse_one_warning_then_the_call_ends(graph):
    client, _ = graph()
    phone = dial(client)
    t = phone.say("you stupid machine")
    assert "respectful" in t.text and not t.ended
    t = phone.say("stupid idiot")
    assert t.ended


def test_asking_to_approve_is_refused_and_handed_off_without_leaving_the_menu(graph):
    client, core = graph()
    phone = dial(client, INSTITUTION)
    t = phone.say("please approve my application")
    assert "I cannot approve, reject or change an application" in t.text
    assert "asked an officer to call you back" in t.text and not t.ended
    assert t.node == "inst_menu"
    assert body(core.calls("POST", "/callbacks")[0])["kind"] == "HANDOFF"


def test_goodbye_menu_repeat_and_officer_work_from_anywhere(graph):
    client, core = graph()
    phone = dial(client)
    phone.say("check an institution")
    assert phone.say("repeat that").node == "pub_inst_name"
    assert phone.say("main menu").node == "pub_menu"
    t = phone.say("can I talk to an officer")
    assert (
        t.ended
        and body(core.calls("POST", "/callbacks")[0])["summary"]
        == "Caller asked to speak to an officer."
    )

    phone = dial(client, call_sid="CA2")
    assert phone.say("goodbye").ended


def test_free_text_nodes_do_not_trigger_global_commands(graph):
    client, core = graph()
    phone = dial(client)
    phone.say("complaint")
    t = phone.say("the officer at that college took money and said goodbye")
    assert t.node == "pub_gv_desc"  # stored as the institution name; did not hand off or end
    assert not t.ended and client.app.state.calls.get("CA1").slots["gv_inst"].startswith(
        "the officer"
    )


def test_call_time_limit_ends_politely(graph):
    client, _ = graph(max_call_duration_public_sec=0)
    phone = dial(client)
    t = phone.say("check an institution")
    assert t.ended and "time limit" in t.text


def test_missing_state_after_a_restart_is_said_not_silent(graph):
    client, _ = graph()
    phone = dial(client)
    client.app.state.calls.drop("CA1")
    t = phone.say("check an institution")
    assert t.ended and "technical problem" in t.text


def test_core_outage_is_explained_and_never_invented(graph):
    client, core = graph()
    phone = dial(client)
    core.overrides.clear()
    phone.say("check an institution")

    import httpx

    from app.core_client import CoreClient

    client.app.state.calls.get("CA1").runner._inner._core = CoreClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    t = phone.say("Sri Venkateswara")
    assert t.ended and "cannot check that right now" in t.text and "approved for" not in t.text


def test_every_node_target_is_registered():
    import app.graph.nodes  # noqa: F401
    from app.graph.engine import MENU_NODE, REGISTRY

    assert set(MENU_NODE.values()) <= set(REGISTRY)
    assert {"verify_pin", "verify_otp", "out_open", "inst_commit_confirm", "pub_gv_confirm"} <= set(
        REGISTRY
    )


# -- modes and hybrid hand-off --------------------------------------------------------------------------------------------
def test_relay_mode_is_unchanged_and_hybrid_without_a_key_stays_in_the_graph(graph):
    client, _ = graph(mode="relay")
    r = client.post("/voice/connect?lang=en", data={"CallSid": "CA1", "From": "+919999999999"})
    assert Turn(r.text).relay is not None

    client2, _ = graph(mode="hybrid")  # no LLM_API_KEY -> graph only
    r = client2.post("/voice/connect?lang=en", data={"CallSid": "CA1", "From": "+919999999999"})
    assert Turn(r.text).gather is not None and Turn(r.text).relay is None


def test_hybrid_hands_unclear_questions_to_the_llm_relay_and_resumes(graph, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    client, core = graph(mode="hybrid", llm=ForbiddenLLM())
    phone = dial(client, OFFICER)
    phone.press(PIN)
    phone.say("banana")
    phone.say("purple")
    t = phone.say("elephant")

    relay = t.relay
    assert relay is not None and t.root.find("Start") is None  # already recording
    assert t.root.find("Connect").get("action").endswith("/voice/g/resume")
    claims = verify_session(relay_session_param(t.xml))
    assert (claims.persona, claims.verified, claims.via_graph) == (Persona.OFFICER, True, True)
    assert claims.language == "en"

    r = client.post("/voice/g/resume", data={"CallSid": "CA1"})
    back = Turn(r.text)
    assert "Back at the main menu" in back.text and back.node == "off_menu"


def test_graph_hands_a_verified_officer_to_the_relay_without_asking_for_the_pin_again(
    graph, monkeypatch
):
    """The relay side of the hand-off: a verified, via_graph session skips verification
    and can send the caller back to the graph."""
    from tests.helpers import FakeLLM, open_relay, press, recv, reply_text, say, setup_msg, text

    llm = FakeLLM([*text("Sure.")])
    client = open_relay(llm)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(
                setup_msg(
                    persona=Persona.OFFICER, subject_id="off-001", verified=True, via_graph=True
                )
            )
            say(ws, "how many cases do I have")
            assert reply_text(ws) == "Sure."  # no PIN prompt first
            say(ws, "okay main menu please")
            assert "going back to the menu" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
        assert len(llm.calls) == 1
        del press
    finally:
        client.__exit__(None, None, None)
