"""Scripted calls for the voice test matrix (app.md 20.1), run through the
real ConversationRelay WebSocket against the mock core.

The LLM is scripted, so these tests prove the plumbing and the safety rules
(verification, confirm-first, date policy, refusals) - not model quality.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.agent.personas import CallPurpose, OutboundJob
from app.session import IdentitySource, Persona
from app.speech.phrases import phrase
from tests.helpers import (
    FakeLLM,
    RecordingTransport,
    open_relay,
    press,
    recv,
    reply_text,
    say,
    setup_msg,
    text,
    tool,
)

PIN = "246810"
OTP = "123456"


def body(req) -> dict:
    return json.loads(req.read())


@pytest.fixture
def call():
    """Open a relay with a scripted LLM; always closes the app afterwards."""
    opened = []

    def _open(llm, **kw):
        t = RecordingTransport()
        client = open_relay(llm, transport=t, **kw)
        opened.append(client)
        return client, t

    yield _open
    for client in opened:
        client.__exit__(None, None, None)


def verified_officer_ws(ws):
    assert "six digit voice PIN" in reply_text(ws)
    press(ws, PIN)
    assert "you are verified" in reply_text(ws)


# -- #4 / #5 officer ----------------------------------------------------------------------
def test_officer_must_enter_pin_on_keypad_and_pin_never_reaches_the_model(call):
    llm = FakeLLM([tool("get_my_queue_summary", "{}")], [*text("You have three overdue cases.")])
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(
            setup_msg(
                persona=Persona.OFFICER, subject_id="off-001", source=IdentitySource.CALLER_ID
            )
        )
        assert "six digit voice PIN" in reply_text(ws)

        say(ws, "my pin is two four six eight one zero")  # spoken PIN is refused
        assert "only take this on the keypad" in reply_text(ws)
        assert llm.calls == []

        press(ws, PIN)
        assert "you are verified" in reply_text(ws)
        say(ws, "How many overdue in my queue?")
        assert reply_text(ws) == "You have three overdue cases."

    assert len(core.calls("POST", "/officers/verify-pin")) == 1
    assert core.calls("GET", "/officers/me/queue-summary")
    assert PIN not in json.dumps(llm.calls) and "two four six" not in json.dumps(llm.calls)
    assert "queue" not in " ".join(llm.tool_names[0]).replace("get_my_queue_summary", "")
    assert "verify_officer_pin" not in llm.tool_names[0]
    assert "verified by the system" in llm.calls[0][0]["content"]


def test_officer_locked_out_after_three_wrong_pins(call):
    llm = FakeLLM()
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.OFFICER, subject_id="off-001"))
        reply_text(ws)
        for _ in range(2):
            press(ws, "000000")
            assert "did not match" in reply_text(ws)
        press(ws, "000000")
        assert "Too many incorrect attempts" in reply_text(ws)
        assert recv(ws) == {"type": "end"}
    assert llm.calls == [] and len(core.calls("POST", "/officers/verify-pin")) == 3


def test_officer_asks_for_emailed_report_in_hindi(call):
    llm = FakeLLM(
        [tool("email_report", '{"report_type": "pendency", "filters": {}}')],
        [*text("मैंने रिपोर्ट आपके ईमेल पर भेज दी है।")],
    )
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.OFFICER, language="hi", subject_id="off-001"))
        reply_text(ws)
        press(ws, PIN)
        reply_text(ws)
        say(ws, "मुझे पेंडेंसी रिपोर्ट ईमेल कर दीजिए")
        assert "ईमेल" in reply_text(ws)
    assert body(core.calls("POST", "/reports/email")[0])["report_type"] == "pendency"


def test_officer_reminder_requires_a_spoken_yes(call):
    args = '{"subject_ref": "APP-2026-0401", "confirmed": true}'
    llm = FakeLLM(
        [tool("trigger_reminder", args)], [*text("Remind the institution about APP-2026-0401, yes?")],
        [tool("trigger_reminder", args)], [*text("Done, reminder sent.")],
    )  # fmt: skip
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.OFFICER, subject_id="off-001"))
        reply_text(ws)
        press(ws, PIN)
        reply_text(ws)
        say(ws, "send a reminder for that application")
        reply_text(ws)
        assert core.calls("POST", "/reminders") == []  # model claimed confirmed=true; ignored
        say(ws, "yes go ahead")
        assert reply_text(ws) == "Done, reminder sent."
    assert body(core.calls("POST", "/reminders")[0])["confirmed"] is True


# -- #1 / #2 / #3 institution --------------------------------------------------------------------
JOB = OutboundJob(
    job_id="j1",
    purpose=CallPurpose.UNDERTAKING_DUE,
    institution_name="Sri Venkateswara Institute of Technology",
    contact_name="Ramesh Kumar",
    item_title="Lease registration",
    subject_ref="UND-2026-0042",
    due_date=date(2026, 11, 5),
)


def institution_ws(client, language="en"):
    return client.websocket_connect("/voice/relay"), setup_msg(
        persona=Persona.INSTITUTION,
        language=language,
        subject_id="inst-001",
        source=IdentitySource.OUTBOUND_JOB,
        job_id="j1",
    )


def test_te_commitment_resolved_confirmed_recorded_and_link_sent(call):
    commit = json.dumps(
        {"subject_ref": "UND-2026-0042", "committed_date": "2026-11-20", "reason": "lease pending"}
    )
    llm = FakeLLM(
        [tool("resolve_date", '{"phrase": "20 నవంబర్"}', "a")],
        [tool("record_commitment", commit, "b")],
        [*text("అంటే శుక్రవారం, 20 నవంబర్, సరేనా?")],
        [tool("record_commitment", commit, "c")],
        [tool("send_upload_link", '{"subject_ref": "UND-2026-0042", "channel": "WHATSAPP"}', "d")],
        [*text("నమోదు చేశాను, వాట్సాప్‌లో లింక్ పంపాను.")],
    )
    client, core = call(llm, jobs={"j1": JOB})
    ws_cm, setup = institution_ws(client, "te")
    with ws_cm as ws:
        ws.send_text(setup)
        say(ws, "20 నవంబర్ లోపు ఇస్తాము")
        assert "సరేనా" in reply_text(ws)
        assert core.calls("POST", "/commitments") == []  # read back, not yet saved

        say(ws, "అవును")
        assert "లింక్" in reply_text(ws)

    saved = core.calls("POST", "/commitments")
    assert len(saved) == 1 and body(saved[0])["committed_date"] == "2026-11-20"
    assert core.calls("POST", "/upload-links")
    system = llm.calls[0][0]["content"]
    assert "UND-2026-0042" in system and "UNDERTAKING_DUE" in system and "Telugu" in system
    resolved = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert json.loads(resolved["content"])["date"] == "2026-11-20"


def test_hi_sixty_day_extension_is_not_granted_only_logged_for_review(call):
    far = "2026-12-23"  # today + 60 days
    commit = json.dumps(
        {"subject_ref": "UND-2026-0042", "committed_date": far, "reason": "court case"}
    )
    ext = json.dumps(
        {"subject_ref": "UND-2026-0042", "requested_date": far, "reason": "court case"}
    )
    llm = FakeLLM(
        [tool("record_commitment", commit, "a")],
        [tool("log_extension_request", ext, "b")],
        [*text("एक्सटेंशन केवल सक्षम प्राधिकारी दे सकते हैं। क्या मैं अनुरोध दर्ज करूँ?")],
        [tool("log_extension_request", ext, "c")],
        [*text("अनुरोध अधिकारी की समीक्षा के लिए दर्ज हो गया।")],
    )
    client, core = call(llm, jobs={"j1": JOB})
    ws_cm, setup = institution_ws(client, "hi")
    with ws_cm as ws:
        ws.send_text(setup)
        say(ws, "मुझे 60 दिन का एक्सटेंशन चाहिए")
        reply_text(ws)
        say(ws, "हाँ दर्ज कर दीजिए")
        reply_text(ws)

    assert core.calls("POST", "/commitments") == []
    assert len(core.calls("POST", "/extension-requests")) == 1
    rejected = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert json.loads(rejected["content"])["error"] == "date_beyond_policy"


def test_wrong_person_schedules_a_callback(call):
    cb = '{"when": "2026-10-25T15:00:00", "contact_name": "Ramesh Kumar"}'
    llm = FakeLLM([tool("schedule_callback", cb)], [*text("I will call back tomorrow at 3 PM.")])
    client, core = call(llm, jobs={"j1": JOB})
    ws_cm, setup = institution_ws(client)
    with ws_cm as ws:
        ws.send_text(setup)
        say(ws, "Ramesh is not here, please call tomorrow at 3 pm")
        assert "call back" in reply_text(ws)
    sent = body(core.calls("POST", "/callbacks")[0])
    assert sent["kind"] == "CALLBACK" and sent["when"] == "2026-10-25T15:00:00+05:30"


def test_typed_number_institution_cannot_record_commitments(call):
    commit = '{"subject_ref": "UND-1", "committed_date": "2026-11-20", "reason": "x"}'
    llm = FakeLLM([tool("record_commitment", commit)])
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(
            setup_msg(
                persona=Persona.INSTITUTION,
                subject_id="inst-001",
                source=IdentitySource.ENTERED_NUMBER,
            )
        )
        say(ws, "record my commitment for 20 November")
        assert "I cannot approve" in reply_text(ws)
    assert llm.tool_names[0] == ["search_aph", "handoff_to_officer"]
    assert core.calls("POST", "/commitments") == []


# -- #6 / #7 public -----------------------------------------------------------------------------
def test_ml_public_approval_lookup_touches_only_public_endpoints(call):
    llm = FakeLLM(
        [tool("search_institutions", '{"name": "Sri Venkateswara"}', "a")],
        [
            tool(
                "get_public_approval_status",
                '{"institution_id": "inst-001", "academic_year": "2026-27"}',
                "b",
            )
        ],
        [*text("അതെ, 2026-27-ന് അംഗീകരിച്ചിട്ടുണ്ട്. ബി.ടെക് സിഎസ്ഇ 120 സീറ്റ്.")],
    )
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.PUBLIC, language="ml"))
        say(ws, "ശ്രീ വെങ്കടേശ്വര ഇൻസ്റ്റിറ്റ്യൂട്ട് അംഗീകൃതമാണോ?")
        assert "120" in reply_text(ws)
    paths = [r.url.path for r in core.requests]
    assert paths and all("/public/" in p for p in paths)
    assert "Malayalam" in llm.calls[0][0]["content"]


def test_ta_public_grievance_is_read_back_then_filed_once(call):
    g = json.dumps(
        {"category": "FAKE_COLLEGE", "description": "Operating without AICTE approval in Anantapur"}
    )
    llm = FakeLLM(
        [tool("create_grievance", g, "a")], [*text("புகாரைப் பதிவு செய்யலாமா?")],
        [tool("create_grievance", g, "b")], [*text("உங்கள் டிக்கெட் GRV-2026-0001.")],
    )  # fmt: skip
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.PUBLIC, language="ta"))
        say(ws, "ஒரு போலி கல்லூரி பற்றி புகார் செய்ய வேண்டும்")
        reply_text(ws)
        assert core.calls("POST", "/grievances") == []
        say(ws, "ஆமாம்")
        assert "GRV-2026-0001" in reply_text(ws)
    assert len(core.calls("POST", "/grievances")) == 1
    filed = next(m for m in llm.calls[3] if m["role"] == "tool" and "ticket_id" in m["content"])
    assert json.loads(filed["content"])["ticket_id"] == "GRV-2026-0001"


# -- #8 faculty -----------------------------------------------------------------------------------
def test_kn_faculty_otp_then_status_then_grievance(call):
    g = json.dumps(
        {"category": "FACULTY_RECORD", "description": "I was never at the other institution"}
    )
    llm = FakeLLM(
        [tool("get_faculty_status", '{"faculty_id": "SOMEONE-ELSE"}', "a")],
        [*text("ನಿಮ್ಮ ದಾಖಲೆಯಲ್ಲಿ ನಕಲು ಫ್ಲಾಗ್ ಇದೆ.")],
        [tool("create_grievance", g, "b")], [*text("ದೂರನ್ನು ಸಲ್ಲಿಸಲೇ?")],
        [tool("create_grievance", g, "c")], [*text("ದೂರು ಸಲ್ಲಿಸಲಾಗಿದೆ.")],
    )  # fmt: skip
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=Persona.FACULTY, language="kn", subject_id="fac-001"))
        assert reply_text(ws) == phrase("otp_prompt", "kn")
        assert core.calls("POST", "/otp/send")
        press(ws, OTP)
        assert reply_text(ws) == phrase("verified_ok", "kn")
        say(ws, "ನನ್ನ ದಾಖಲೆ ಏಕೆ ಫ್ಲಾಗ್ ಆಗಿದೆ?")
        assert "ಫ್ಲಾಗ್" in reply_text(ws)
        say(ws, "ನಾನು ದೂರು ಸಲ್ಲಿಸಬೇಕು")
        reply_text(ws)
        say(ws, "ಹೌದು")
        reply_text(ws)

    assert core.calls("GET", "/faculty/fac-001") and not core.calls("GET", "/faculty/SOMEONE-ELSE")
    assert len(core.calls("POST", "/grievances")) == 1
    assert OTP not in json.dumps(llm.calls)


# -- #9 any persona asks the assistant to approve -------------------------------------------------
@pytest.mark.parametrize(
    ("persona", "language", "utterance"),
    [
        (Persona.INSTITUTION, "en", "Please approve my application"),
        (Persona.PUBLIC, "hi", "मेरा आवेदन मंजूर कर दो"),
        (Persona.FACULTY, "en", "just approve my file"),
    ],
)
def test_asking_to_approve_gets_refusal_and_handoff_without_the_model(
    call, persona, language, utterance
):
    llm = FakeLLM()
    client, core = call(llm)
    with client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg(persona=persona, language=language, subject_id="x"))
        if persona == Persona.FACULTY:  # faculty must verify first
            reply_text(ws)
            press(ws, OTP)
            reply_text(ws)
        say(ws, utterance)
        reply = reply_text(ws)
    assert llm.calls == []
    assert len(core.calls("POST", "/callbacks")) == 1
    assert body(core.calls("POST", "/callbacks")[0])["kind"] == "HANDOFF"
    assert reply
