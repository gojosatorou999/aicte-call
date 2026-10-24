from __future__ import annotations

import asyncio
import json

import pytest

from app.agent import guardrails
from app.agent.brain import Brain, BrainError
from app.agent.tools import openai_tools
from app.session import Persona
from tests.helpers import FakeLLM, FakeRunner, Hang, text, tool


def make_brain(llm, runner=None, persona=Persona.PUBLIC, language="en"):
    return Brain(llm=llm, runner=runner or FakeRunner(), persona=persona, language=language)


async def collect(brain, user_text):
    return [s async for s in brain.respond(user_text)]


async def test_text_reply_streams_as_sentences():
    brain = make_brain(FakeLLM([*text("Hello th", "ere. How can ", "I help?")]))
    assert await collect(brain, "hi") == ["Hello there.", "How can I help?"]


async def test_hindi_danda_ends_a_sentence():
    brain = make_brain(FakeLLM([*text("नमस्ते। मैं मदद करूँगा।")]), language="hi")
    assert await collect(brain, "hi") == ["नमस्ते।", "मैं मदद करूँगा।"]


async def test_tool_loop_runs_tool_then_answers_and_keeps_history():
    runner = FakeRunner({"search_institutions": {"results": [{"id": "inst-001"}]}})
    llm = FakeLLM(
        [tool("search_institutions", '{"name": "Sri Venkateswara"}')],
        [*text("Yes, it is listed.")],
        [*text("Anything else?")],
    )
    brain = make_brain(llm, runner)
    assert await collect(brain, "is Sri Venkateswara approved") == ["Yes, it is listed."]
    assert runner.called == [("search_institutions", {"name": "Sri Venkateswara"})]

    second_round = llm.calls[1]
    assert second_round[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": json.dumps({"results": [{"id": "inst-001"}]}),
    }
    assert second_round[-2]["tool_calls"][0]["function"]["name"] == "search_institutions"

    await collect(brain, "thanks")  # next turn sees the whole prior exchange, still valid
    roles = [m["role"] for m in llm.calls[2]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]


async def test_bad_tool_arguments_become_error_result_not_crash():
    llm = FakeLLM([tool("search_institutions", "{not json")], [*text("Sorry.")])
    brain = make_brain(llm)
    assert await collect(brain, "x") == ["Sorry."]
    assert json.loads(llm.calls[1][-1]["content"]) == {"error": "invalid_arguments"}


async def test_decision_request_is_refused_without_llm_and_hands_off():
    runner = FakeRunner()
    llm = FakeLLM()
    brain = make_brain(llm, runner, persona=Persona.INSTITUTION)
    out = await collect(brain, "Please approve my application today")
    assert out[0].startswith("I cannot approve")
    assert "asked an officer" in out[1]
    assert runner.called[0][0] == "handoff_to_officer"
    assert llm.calls == []


async def test_decision_refusal_for_officer_has_no_handoff_claim():
    brain = make_brain(FakeLLM(), FakeRunner(allowed={"get_my_queue_summary"}), Persona.OFFICER)
    out = await collect(brain, "approve my application")
    assert len(out) == 1 and "officer to follow up" not in out[0]


async def test_forbidden_promise_in_reply_is_replaced_and_not_spoken():
    runner = FakeRunner()
    llm = FakeLLM([*text("Sure. Your application has been approved. Bye.")])
    out = await collect(make_brain(llm, runner, Persona.INSTITUTION), "what happens now")
    assert out[0] == "Sure."
    assert not any("has been approved" in s for s in out)
    assert any("I cannot approve" in s for s in out)
    assert runner.called[0][0] == "handoff_to_officer"


async def test_disallowed_tool_is_refused_and_history_stays_valid():
    llm = FakeLLM([tool("record_commitment", "{}")], [*text("Fine.")])
    brain = make_brain(llm, FakeRunner(allowed={"search_institutions"}))
    out = await collect(brain, "x")
    assert "I cannot approve" in out[0]
    await collect(brain, "next")  # must not carry an unanswered tool_calls message
    assert all("tool_calls" not in m for m in llm.calls[1])


async def test_cancellation_keeps_user_line_and_spoken_text_only():
    llm = FakeLLM([*text("First sentence. "), Hang()], [*text("Ok.")])
    brain = make_brain(llm)

    async def run():
        async for _ in brain.respond("tell me"):
            pass

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    brain.note_interrupted("First")
    await collect(brain, "stop")
    history = llm.calls[1][1:]
    assert history[0] == {"role": "user", "content": "tell me"}
    assert history[1] == {"role": "assistant", "content": "First"}


async def test_llm_failure_raises_brain_error():
    brain = make_brain(FakeLLM([RuntimeError("timeout")]))
    with pytest.raises(BrainError):
        await collect(brain, "hi")


async def test_core_error_in_tool_is_reported_to_model():
    from app.core_client import CoreUnavailable

    class Boom(FakeRunner):
        async def run(self, name, args):
            raise CoreUnavailable("down")

    llm = FakeLLM([tool("search_institutions", "{}")], [*text("I could not check.")])
    assert await collect(make_brain(llm, Boom()), "x") == ["I could not check."]
    result = json.loads(llm.calls[1][-1]["content"])
    assert result["error"] == "core_unavailable" and "handoff_to_officer" in result["hint"]


async def test_three_consecutive_core_outages_end_the_conversation_gracefully():
    from app.core_client import CoreUnavailable

    class Boom(FakeRunner):
        async def run(self, name, args):
            raise CoreUnavailable("down")

    llm = FakeLLM(*[[tool("search_institutions", "{}", f"c{i}")] for i in range(3)])
    with pytest.raises(BrainError):
        await collect(make_brain(llm, Boom()), "x")


async def test_a_successful_tool_call_resets_the_outage_count():
    from app.core_client import CoreUnavailable

    class Flaky(FakeRunner):
        n = 0

        async def run(self, name, args):
            Flaky.n += 1
            if Flaky.n in (1, 2, 4, 5):
                raise CoreUnavailable("blip")
            return {}

    llm = FakeLLM(
        [tool("a", "{}", "1")], [tool("a", "{}", "2")], [tool("a", "{}", "3")],
        [tool("a", "{}", "4")], [tool("a", "{}", "5")], [*text("Done.")],
    )  # fmt: skip
    # 2 failures, 1 success, 2 failures: never 3 in a row (the tool loop also caps rounds at 4,
    # so the turn ends with the technical-problem phrase instead of raising)
    out = await collect(make_brain(llm, Flaky()), "x")
    assert out == ["I am having a technical problem right now. Please try again shortly. Goodbye."]


def test_system_prompt_has_persona_language_and_date():
    prompt = guardrails.build_system_prompt(Persona.PUBLIC, "te")
    assert "member of the public" in prompt and "Telugu" in prompt and "Today is" in prompt
    assert "never approve" in prompt.lower()


@pytest.mark.parametrize(
    "utterance",
    [
        "approve my application",
        "Please reject the application",
        "meri application approve kar do",
        "मेरा आवेदन मंजूर कर दो",
    ],
)
def test_wants_decision_positive(utterance):
    assert guardrails.wants_decision(utterance)


@pytest.mark.parametrize(
    "utterance", ["is my application approved", "what is the status of the file", "how do I apply"]
)
def test_wants_decision_negative(utterance):
    assert not guardrails.wants_decision(utterance)


def test_abuse_detection():
    assert guardrails.is_abusive("you stupid machine")
    assert guardrails.is_abusive("तुम बेवकूफ हो")
    assert not guardrails.is_abusive("this is a classical college")


def test_openai_tool_schema_respects_optional_and_enum():
    tools = {
        t["function"]["name"]: t["function"]["parameters"]
        for t in openai_tools(Persona.INSTITUTION)
    }
    assert "record_commitment" in tools and "verify_officer_pin" not in tools
    chan = tools["send_upload_link"]["properties"]["channel"]
    assert chan["enum"] == ["WHATSAPP", "SMS", "EMAIL"]
    pub = {t["function"]["name"]: t["function"]["parameters"] for t in openai_tools(Persona.PUBLIC)}
    assert pub["search_institutions"]["required"] == ["name"]
