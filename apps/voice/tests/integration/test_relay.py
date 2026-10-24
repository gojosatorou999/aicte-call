from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import relay_ws
from app.config import get_settings
from app.main import create_app
from app.session import Persona
from tests.helpers import FakeLLM, Hang, mock_core_client, text, token, tool


@pytest.fixture(autouse=True)
def _fast_speech(monkeypatch):
    """Make the 'estimated speech time' waits negligible."""
    monkeypatch.setattr(relay_ws, "SPEECH_CHARS_PER_SEC", 1e9)
    monkeypatch.setattr(relay_ws, "END_PADDING_SEC", 0.0)


def open_relay(llm, **settings_overrides):
    settings = get_settings()
    for k, v in settings_overrides.items():
        setattr(settings, k, v)
    client = TestClient(create_app())
    client.__enter__()
    client.app.state.core = mock_core_client()
    client.app.state.llm = llm
    return client


def setup_msg(**kw):
    tok = kw.pop("tok", None) or token(**kw)
    return json.dumps(
        {"type": "setup", "callSid": kw.get("call_sid", "CA1"), "customParameters": {"session": tok}}
    )


def recv(ws):
    return json.loads(ws.receive_text())


def reply_text(ws):
    """Collect text tokens up to and including the `last: true` marker."""
    out = []
    while True:
        m = recv(ws)
        assert m["type"] == "text", m
        out.append(m["token"])
        if m["last"]:
            return "".join(out).strip()


@pytest.mark.parametrize(("lang", "persona"), [("en", Persona.PUBLIC), ("hi", Persona.PUBLIC)])
def test_public_asks_if_institution_is_approved_using_mock_tools(lang, persona):
    llm = FakeLLM(
        [tool("search_institutions", '{"name": "Sri Venkateswara"}', "c1")],
        [tool("get_public_approval_status", '{"institution_id": "inst-001"}', "c2")],
        [*text("Yes, it is approved for B.Tech CSE with 120 seats.")],
    )
    client = open_relay(llm)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg(persona=persona, language=lang))
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "Is Sri Venkateswara approved?", "last": True}))
            assert reply_text(ws) == "Yes, it is approved for B.Tech CSE with 120 seats."
        tool_results = [m["content"] for m in llm.calls[2] if m["role"] == "tool"]
        assert "inst-001" in tool_results[0] and "B.Tech CSE" in tool_results[1]
        assert "Telugu" not in llm.calls[0][0]["content"]
    finally:
        client.__exit__(None, None, None)


def test_partial_prompts_are_ignored():
    llm = FakeLLM([*text("Hello.")])
    client = open_relay(llm)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "is sri", "last": False}))
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "is sri venkat", "last": True}))
            reply_text(ws)
        assert len(llm.calls) == 1 and llm.calls[0][-1]["content"] == "is sri venkat"
    finally:
        client.__exit__(None, None, None)


def test_barge_in_stops_old_reply_and_trims_memory():
    llm = FakeLLM([*text("Sentence one. "), Hang()], [*text("Sure, go ahead.")])
    client = open_relay(llm)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "tell me about approvals", "last": True}))
            first = recv(ws)
            assert first["token"].strip() == "Sentence one."
            ws.send_text(json.dumps({"type": "interrupt", "utteranceUntilInterrupt": "Sentence", "durationUntilInterruptMs": 400}))
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "wait, another question", "last": True}))
            assert reply_text(ws) == "Sure, go ahead."
        history = llm.calls[1][1:]
        assert history[1] == {"role": "assistant", "content": "Sentence"}
        assert history[2]["content"] == "wait, another question"
    finally:
        client.__exit__(None, None, None)


def test_bad_session_token_is_rejected():
    client = open_relay(FakeLLM())
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg(tok="not-a-jwt"))
            with pytest.raises(WebSocketDisconnect) as ei:
                ws.receive_text()
        assert ei.value.code == relay_ws.WS_UNAUTHORIZED
    finally:
        client.__exit__(None, None, None)


def test_call_sid_mismatch_is_rejected():
    client = open_relay(FakeLLM())
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(
                json.dumps({"type": "setup", "callSid": "CA-OTHER", "customParameters": {"session": token()}})
            )
            with pytest.raises(WebSocketDisconnect) as ei:
                ws.receive_text()
        assert ei.value.code == relay_ws.WS_MISMATCH
    finally:
        client.__exit__(None, None, None)


def test_first_message_must_be_setup():
    client = open_relay(FakeLLM())
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "hi", "last": True}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
    finally:
        client.__exit__(None, None, None)


def test_silence_reprompts_then_goodbye_and_end():
    client = open_relay(FakeLLM(), silence_reprompts=1, no_input_timeout_ms=20)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            assert "still there" in reply_text(ws)
            assert "unable to hear you" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
    finally:
        client.__exit__(None, None, None)


def test_abuse_one_warning_then_end():
    llm = FakeLLM()
    client = open_relay(llm)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "you stupid bot", "last": True}))
            assert "respectful" in reply_text(ws)
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "stupid idiot", "last": True}))
            assert "ending this call" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
        assert llm.calls == []  # abusive input never reaches the model
    finally:
        client.__exit__(None, None, None)


def test_max_turns_ends_call_politely():
    client = open_relay(FakeLLM([*text("One.")]), max_turns=1)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "a", "last": True}))
            assert reply_text(ws) == "One."
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "b", "last": True}))
            assert "time limit" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
    finally:
        client.__exit__(None, None, None)


def test_llm_failure_says_technical_problem_and_ends():
    client = open_relay(FakeLLM([RuntimeError("boom")]))
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "hello", "last": True}))
            assert "technical problem" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
    finally:
        client.__exit__(None, None, None)


def test_no_llm_configured_says_technical_problem_and_ends():
    client = open_relay(None)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            assert "technical problem" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
    finally:
        client.__exit__(None, None, None)


def test_call_duration_limit_ends_call(monkeypatch):
    client = open_relay(FakeLLM(), max_call_duration_public_sec=0)
    try:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            assert "time limit" in reply_text(ws)
            assert recv(ws) == {"type": "end"}
    finally:
        client.__exit__(None, None, None)
