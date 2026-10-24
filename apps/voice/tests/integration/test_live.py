"""The graph over a ConversationRelay WebSocket ("live" mode): what Twilio would send, and
what we send back to be spoken. No LLM (the stub fails the test if it is called)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from starlette.websockets import WebSocketDisconnect

from app import relay_setup
from app.agent.personas import CallPurpose, OutboundJob
from app.session import IdentitySource, Persona
from tests.helpers import press, recv, reply_text, say, setup_msg
from tests.phone import ForbiddenLLM, open_graph

PIN = "246810"


@pytest.fixture
def live():
    opened = []

    def _open(**kw):
        client, core = open_graph(mode="live", **kw)
        opened.append(client)
        return client, core

    ForbiddenLLM.calls = 0
    yield _open
    for c in opened:
        c.__exit__(None, None, None)
    assert ForbiddenLLM.calls == 0


def connect(client, **claims):
    ws = client.websocket_connect("/voice/live")
    return ws, setup_msg(**claims)


def test_public_caller_says_a_college_name_and_hears_the_answer(live):
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        # Twilio's welcomeGreeting already said hello, so we open straight with the question.
        assert reply_text(ws) == "Please say the name of the college."
        say(ws, "Sri Venkateswara")
        answer = reply_text(ws)
    # written for the eye, spoken for the ear
    assert "approved for 2026 to 27" in answer
    assert "B Tech C S E, intake 120" in answer and "B.Tech" not in answer
    assert "anything else" not in answer.lower()


def test_keypad_digit_answers_a_numbered_question_immediately(live):
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        say(ws, "Sri")
        assert "Which one?" in reply_text(ws)
        press(ws, "2")
        assert "Sri Sai College of Engineering is approved" in reply_text(ws)


def test_keys_pressed_at_a_speech_question_are_ignored(live):
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        press(ws, "5")  # this question is answered by speaking
        say(ws, "Sri Venkateswara")
        assert "is approved for" in reply_text(ws)


def test_partial_transcripts_are_ignored_until_the_final_one(live):
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "Sri Ven", "last": False}))
        ws.send_text(
            json.dumps({"type": "prompt", "voicePrompt": "Sri Venkateswara", "last": True})
        )
        assert "is approved for" in reply_text(ws)


def officer(client):
    return connect(
        client, persona=Persona.OFFICER, subject_id="off-001", source=IdentitySource.CALLER_ID
    )


def test_officer_pin_is_taken_on_the_keypad_only_and_never_kept(live):
    client, core = live()
    ws_cm, setup = officer(client)
    with ws_cm as ws:
        ws.send_text(setup)
        assert "six digit voice PIN" in reply_text(ws)

        say(ws, "my pin is two four six eight one zero")
        spoken = reply_text(ws)
        assert "keypad" in spoken and "six digit voice PIN" in spoken  # refused, asked again

        press(ws, PIN)
        assert "you are verified" in reply_text(ws)
        say(ws, "what is in my queue")
        assert "14 pending, 3 overdue, and 5 due this week" in reply_text(ws)

        call = client.app.state.calls.get("CA1")
        kept = json.dumps([(line.role, line.text) for line in call.transcript]) + repr(call.slots)
        assert PIN not in kept and "two four six" not in kept
    assert len(core.calls("POST", "/officers/verify-pin")) == 1


def test_officer_is_locked_out_after_three_wrong_pins_and_the_call_ends(live):
    client, core = live()
    ws_cm, setup = officer(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        for _ in range(2):
            press(ws, "000000")
            assert "did not match" in reply_text(ws)
        press(ws, "111111")
        assert "Too many incorrect attempts" in reply_text(ws)
        assert recv(ws) == {"type": "end"}
    assert not core.calls("GET", "/officers/me/queue-summary")


def test_short_pin_ended_with_hash_counts_as_an_attempt(live):
    client, _ = live()
    ws_cm, setup = officer(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        press(ws, "123#")
        assert "did not match" in reply_text(ws)


def test_star_clears_a_half_typed_pin(live):
    client, _ = live()
    ws_cm, setup = officer(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        press(ws, "99*")
        press(ws, PIN)
        assert "you are verified" in reply_text(ws)


def test_outbound_reminder_call_runs_the_scripted_conversation(live):
    job = OutboundJob(
        job_id="j1",
        purpose=CallPurpose.UNDERTAKING_DUE,
        institution_name="Sri Venkateswara Institute of Technology",
        contact_name="Ramesh Kumar",
        item_title="Lease registration",
        subject_ref="UND-2026-0042",
        due_date=date(2026, 11, 5),
    )
    client, core = live(jobs={"j1": job})
    ws_cm, setup = connect(
        client,
        persona=Persona.INSTITUTION,
        subject_id="inst-001",
        source=IdentitySource.OUTBOUND_JOB,
        job_id="j1",
    )
    with ws_cm as ws:
        ws.send_text(setup)
        assert "Am I speaking with Ramesh Kumar" in reply_text(ws)
        say(ws, "yes speaking")
        assert "due on Thursday, 5 November 2026" in reply_text(ws)
        say(ws, "by the 20th of November")
        reply_text(ws)
        say(ws, "skip")
        assert "Friday, 20 November 2026" in reply_text(ws)
        say(ws, "yes")
        assert "Recorded" in reply_text(ws)
        say(ws, "no thanks")
        assert "Goodbye" in reply_text(ws)
        assert recv(ws) == {"type": "end"}
    assert (
        json.loads(core.calls("POST", "/commitments")[0].read())["committed_date"] == "2026-11-20"
    )
    assert client.app.state.calls.get("CA1") is None  # state dropped when the call ends


def test_silence_reprompts_then_ends_the_call(live):
    client, _ = live(silence_reprompts=1, no_input_timeout_ms=20)
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        second = reply_text(ws)  # no answer -> "did not hear" + the question again
        assert "did not hear anything" in second and "Please say the name of the college" in second
        last = reply_text(ws)  # one reprompt allowed, then the polite goodbye
        assert "unable to hear you" in last
        assert recv(ws) == {"type": "end"}


def test_silence_after_an_answer_is_a_natural_end_not_a_reprompt(live):
    client, _ = live(no_input_timeout_ms=20)
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        say(ws, "Sri Venkateswara")
        assert "is approved for" in reply_text(ws)
        assert "Have a nice day" in reply_text(ws)  # silence ends it politely
        assert recv(ws) == {"type": "end"}


def test_abuse_gets_one_warning_then_the_call_ends(live):
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        say(ws, "you stupid machine")
        assert "respectful" in reply_text(ws)
        say(ws, "stupid idiot")
        assert "ending this call" in reply_text(ws)
        assert recv(ws) == {"type": "end"}


def test_asking_to_approve_is_refused_in_live_mode_too(live):
    client, core = live()
    ws_cm, setup = connect(
        client, persona=Persona.INSTITUTION, subject_id="inst-001", source=IdentitySource.CALLER_ID
    )
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        say(ws, "please approve my application")
        assert "I cannot approve, reject or change an application" in reply_text(ws)
    assert json.loads(core.calls("POST", "/callbacks")[0].read())["kind"] == "HANDOFF"


def test_a_session_token_is_required(live):
    client, _ = live()
    with client.websocket_connect("/voice/live") as ws:
        ws.send_text(setup_msg(tok="not-a-jwt"))
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
    assert ei.value.code == relay_setup.WS_UNAUTHORIZED


class RecordingLog:
    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append((event, kw))

    warning = error = info


def test_each_turn_logs_our_latency_without_the_caller_s_words(live, monkeypatch):
    from app.graph import live as live_module

    recorder = RecordingLog()
    monkeypatch.setattr(live_module, "log", recorder)
    client, _ = live()
    ws_cm, setup = connect(client)
    with ws_cm as ws:
        ws.send_text(setup)
        reply_text(ws)
        say(ws, "Sri Venkateswara")
        reply_text(ws)
    turns = [kw for event, kw in recorder.events if event == "live_turn"]
    assert turns and turns[0]["node"] == "pub_menu" and isinstance(turns[0]["ms"], int)
    assert "Sri Venkateswara" not in repr(recorder.events)
