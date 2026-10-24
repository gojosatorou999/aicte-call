from __future__ import annotations

import httpx

from app.agent.policy import Verification
from app.agent.verification import Outcome, Verifier
from app.core_client import CoreClient
from tests.helpers import RecordingTransport, mock_core_client


def pin_verifier(core=None, max_attempts=3):
    return Verifier(
        kind=Verification.PIN,
        core=core or mock_core_client(),
        session_jwt="j",
        max_attempts=max_attempts,
    )


async def type_code(v: Verifier, digits: str, terminator: str = "") -> Outcome:
    out = Outcome.NEED_MORE
    for d in digits + terminator:
        out = await v.feed(d)
    return out


async def test_pin_submits_automatically_at_six_digits():
    assert await type_code(pin_verifier(), "246810") == Outcome.VERIFIED


async def test_hash_submits_a_short_entry_which_then_fails():
    v = pin_verifier()
    assert await type_code(v, "2468", "#") == Outcome.FAILED
    assert v.attempts == 1


async def test_partial_entry_waits_and_star_restarts():
    v = pin_verifier()
    assert await type_code(v, "2468") == Outcome.NEED_MORE
    assert await v.feed("*") == Outcome.NEED_MORE
    assert await type_code(v, "246810") == Outcome.VERIFIED


async def test_hash_alone_does_nothing():
    assert await pin_verifier().feed("#") == Outcome.NEED_MORE


async def test_wrong_pin_fails_then_locks_after_max_attempts():
    v = pin_verifier(max_attempts=3)
    assert await type_code(v, "000000") == Outcome.FAILED
    assert await type_code(v, "111111") == Outcome.FAILED
    assert await type_code(v, "222222") == Outcome.LOCKED


async def test_buffer_is_cleared_after_each_attempt():
    v = pin_verifier()
    await type_code(v, "000000")
    assert await type_code(v, "246810") == Outcome.VERIFIED  # not "000000246810"


async def test_otp_is_sent_on_begin_and_checked_with_code_field():
    t = RecordingTransport()
    v = Verifier(kind=Verification.OTP, core=mock_core_client(t), session_jwt="j", max_attempts=3)
    assert await v.begin() is True
    assert t.calls("POST", "/otp/send")
    assert await type_code(v, "123456") == Outcome.VERIFIED
    assert b'"code"' in t.calls("POST", "/otp/check")[0].read()


async def test_pin_needs_no_begin_step_and_core_outage_is_not_a_failed_attempt():
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    v = pin_verifier(CoreClient(transport=httpx.MockTransport(down)))
    assert await v.begin() is True
    assert await type_code(v, "246810") == Outcome.ERROR
    assert v.attempts == 0


async def test_otp_send_failure_means_cannot_start():
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    v = Verifier(
        kind=Verification.OTP,
        core=CoreClient(transport=httpx.MockTransport(down)),
        session_jwt="j",
        max_attempts=3,
    )
    assert await v.begin() is False
