from __future__ import annotations

from typing import Any

import pytest

from app.agent.policy import GatedToolRunner, SessionPolicy, Verification
from app.agent.tools import ToolNotAllowed
from app.config import get_settings
from app.session import IdentitySource, Persona, SessionClaims


class Inner:
    """Stands in for ToolRunner: records what actually reaches core."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        return {"ok": True}


def make(
    persona=Persona.INSTITUTION, source=IdentitySource.CALLER_ID, subject="inst-001", lang="en"
):
    claims = SessionClaims("CA1", persona, lang, "ref", identity_source=source, subject_id=subject)
    inner = Inner()
    policy = SessionPolicy(claims, get_settings())
    return GatedToolRunner(inner, policy), inner, policy  # type: ignore[arg-type]


def names(runner: GatedToolRunner) -> set[str]:
    return {s["function"]["name"] for s in runner.schemas()}


# -- visibility -------------------------------------------------------------------
def test_unverified_officer_and_faculty_see_only_open_tools():
    # (the contract gives officers no handoff tool)
    expected = {
        Persona.OFFICER: (Verification.PIN, {"search_aph"}),
        Persona.FACULTY: (Verification.OTP, {"search_aph", "handoff_to_officer"}),
    }
    for persona, (kind, tools) in expected.items():
        runner, _, policy = make(persona, subject="x")
        assert policy.verification_needed() == kind
        assert names(runner) == tools


def test_verified_officer_gets_officer_tools_but_never_pin_tools():
    runner, _, policy = make(Persona.OFFICER, subject="off-001")
    policy.mark_verified()
    got = names(runner)
    assert {"get_my_queue_summary", "list_overdue", "email_report", "trigger_reminder"} <= got
    assert not got & {"verify_officer_pin", "send_otp", "check_otp"}
    assert policy.verification_needed() == Verification.NONE


def test_typed_number_institution_is_restricted_to_open_tools():
    runner, _, _ = make(source=IdentitySource.ENTERED_NUMBER)
    assert names(runner) == {"search_aph", "handoff_to_officer"}


async def test_hidden_tool_call_is_refused_even_if_model_tries():
    runner, inner, _ = make(Persona.OFFICER, subject="off-001")
    with pytest.raises(ToolNotAllowed):
        await runner.run("get_my_queue_summary", {})
    assert inner.calls == []


def test_resolve_date_is_for_institution_only_and_faculty_id_is_hidden():
    assert "resolve_date" in names(make(Persona.INSTITUTION)[0])
    assert "resolve_date" not in names(make(Persona.PUBLIC)[0])
    runner, _, policy = make(Persona.FACULTY, subject="fac-001")
    policy.mark_verified()
    status = next(s for s in runner.schemas() if s["function"]["name"] == "get_faculty_status")
    assert status["function"]["parameters"]["properties"] == {}


async def test_faculty_id_is_injected_from_session_not_from_model():
    runner, inner, policy = make(Persona.FACULTY, subject="fac-001")
    policy.mark_verified()
    await runner.run("get_faculty_status", {"faculty_id": "someone-else"})
    assert inner.calls == [("get_faculty_status", {"faculty_id": "fac-001"})]


async def test_faculty_without_known_identity_gets_error():
    runner, inner, policy = make(Persona.FACULTY, subject=None)
    policy.mark_verified()
    assert await runner.run("get_faculty_status", {}) == {"error": "identity_unknown"}
    assert inner.calls == []


# -- confirm first -------------------------------------------------------------------
COMMIT = {"subject_ref": "UND-1", "committed_date": "2026-11-20", "reason": "lease"}


async def test_commitment_needs_a_spoken_yes_after_the_readback():
    runner, inner, policy = make()
    policy.note_user("we will submit by 20 November")
    first = await runner.run("record_commitment", COMMIT)
    assert first["status"] == "needs_confirmation" and inner.calls == []

    policy.note_user("yes that is right")
    done = await runner.run("record_commitment", COMMIT)
    assert done == {"ok": True}
    assert inner.calls == [("record_commitment", COMMIT)]

    again = await runner.run("record_commitment", COMMIT)  # consumed: must confirm anew
    assert again["status"] == "needs_confirmation"


@pytest.mark.parametrize("reply", ["no, not that date", "hmm let me think", "", "नहीं"])
async def test_non_yes_replies_never_confirm(reply):
    runner, inner, policy = make()
    policy.note_user("20th")
    await runner.run("record_commitment", COMMIT)
    policy.note_user(reply)
    assert (await runner.run("record_commitment", COMMIT))["status"] == "needs_confirmation"
    assert inner.calls == []


async def test_model_cannot_preview_and_confirm_in_the_same_turn():
    runner, inner, policy = make()
    policy.note_user("yes")  # an old yes does not count
    await runner.run("record_commitment", COMMIT)
    assert (await runner.run("record_commitment", COMMIT))["status"] == "needs_confirmation"
    assert inner.calls == []


async def test_changed_arguments_restart_confirmation():
    runner, inner, policy = make()
    policy.note_user("20th")
    await runner.run("record_commitment", COMMIT)
    policy.note_user("yes")
    changed = COMMIT | {"committed_date": "2026-11-21"}
    assert (await runner.run("record_commitment", changed))["status"] == "needs_confirmation"
    assert inner.calls == []


async def test_reminder_is_confirmed_then_sent_with_confirmed_true():
    runner, inner, policy = make(Persona.OFFICER, subject="off-001")
    policy.mark_verified()
    policy.note_user("remind them")
    args = {"subject_ref": "APP-1", "confirmed": False}
    assert (await runner.run("trigger_reminder", args))["status"] == "needs_confirmation"
    policy.note_user("yes please")
    await runner.run("trigger_reminder", args | {"confirmed": True})  # model flips the flag
    assert inner.calls == [("trigger_reminder", {"subject_ref": "APP-1", "confirmed": True})]


async def test_grievance_needs_description_and_confirmation():
    runner, inner, policy = make(Persona.PUBLIC, source=IdentitySource.ANONYMOUS, subject=None)
    assert (await runner.run("create_grievance", {"category": "FAKE", "description": "bad"})) == {
        "error": "description_too_short"
    }
    g = {"category": "FAKE_COLLEGE", "description": "Operating without approval in Anantapur"}
    policy.note_user("file it")
    assert (await runner.run("create_grievance", g))["status"] == "needs_confirmation"
    policy.note_user("yes")
    await runner.run("create_grievance", g)
    assert [c[0] for c in inner.calls] == ["create_grievance"]


# -- dates ------------------------------------------------------------------------------
async def test_natural_language_date_is_normalised_before_confirmation():
    runner, _, policy = make()
    policy.note_user("next friday")
    out = await runner.run("record_commitment", COMMIT | {"committed_date": "next Friday"})
    assert out["details"]["committed_date"] == "2026-10-30"


async def test_commitment_date_policy():
    runner, inner, policy = make()
    policy.note_user("x")
    assert (await runner.run("record_commitment", COMMIT | {"committed_date": "2026-10-01"})) == {
        "error": "date_in_past"
    }
    beyond = await runner.run("record_commitment", COMMIT | {"committed_date": "2026-12-25"})
    assert beyond["error"] == "date_beyond_policy" and beyond["max_date"] == "2026-11-23"
    invalid = await runner.run("record_commitment", COMMIT | {"committed_date": "whenever"})
    assert invalid["error"] == "invalid_date"
    assert inner.calls == []


async def test_extension_request_may_go_beyond_the_commitment_limit():
    runner, inner, policy = make()
    req = {"subject_ref": "UND-1", "requested_date": "2026-12-25", "reason": "court case"}
    policy.note_user("60 days please")
    assert (await runner.run("log_extension_request", req))["status"] == "needs_confirmation"
    policy.note_user("yes")
    await runner.run("log_extension_request", req)
    assert inner.calls == [("log_extension_request", req)]


async def test_callback_time_must_be_in_the_future_and_gets_timezone():
    runner, inner, _ = make()
    past = await runner.run(
        "schedule_callback", {"when": "2026-10-24T09:00:00", "contact_name": "R"}
    )
    assert past == {"error": "time_in_past"}
    await runner.run("schedule_callback", {"when": "2026-10-26T15:00:00", "contact_name": "R"})
    assert inner.calls[0][1]["when"] == "2026-10-26T15:00:00+05:30"
    bad = await runner.run("schedule_callback", {"when": "tomorrow", "contact_name": "R"})
    assert bad["error"] == "invalid_datetime"


async def test_resolve_date_tool_in_any_language():
    runner, inner, _ = make(lang="hi")
    out = await runner.run("resolve_date", {"phrase": "अगले शुक्रवार"})
    assert out == {
        "date": "2026-10-30",
        "spoken": "Friday, 30 October 2026",
        "spoken_local": "शुक्रवार, 30 अक्टूबर 2026",
    }
    assert (await runner.run("resolve_date", {"phrase": "soonish"}))["error"] == "unparseable"
    assert inner.calls == []


# -- limits -----------------------------------------------------------------------------------
async def test_public_lookup_limit_per_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_lookups_per_call", 2)
    runner, inner, _ = make(Persona.PUBLIC, source=IdentitySource.ANONYMOUS, subject=None)
    for _ in range(2):
        await runner.run("search_institutions", {"name": "x"})
    assert await runner.run("search_institutions", {"name": "x"}) == {
        "error": "lookup_limit_reached"
    }
    assert len(inner.calls) == 2


async def test_overdue_list_limit_is_clamped():
    runner, inner, policy = make(Persona.OFFICER, subject="off-001")
    policy.mark_verified()
    await runner.run("list_overdue", {"kind": "STAGE", "limit": 500})
    assert inner.calls[0][1]["limit"] == 10
