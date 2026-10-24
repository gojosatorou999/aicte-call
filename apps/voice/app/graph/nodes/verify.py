"""Keypad PIN / OTP verification nodes (officer / faculty)."""

from __future__ import annotations

from app.agent.verification import Outcome
from app.graph.engine import MSG_TECHNICAL, Input, Kind, Step, menu_for, node
from app.graph.state import CallState

MSG_VERIFIED = "Thank you, you are verified."
MSG_MISMATCH = "That code did not match."
MSG_LOCKED = (
    "Too many incorrect attempts, so I must end this call for your security. "
    "Please try again later. Goodbye."
)


async def _verify(call: CallState, inp: Input) -> Step:
    verifier = call.verifier
    if verifier is None:  # already verified (e.g. a repeated webhook)
        return Step(goto=menu_for(call))
    outcome = Outcome.NEED_MORE
    for digit in inp.digits:
        outcome = await verifier.feed(digit)
    if outcome == Outcome.NEED_MORE:  # fewer than six digits then '#': submit what we have
        outcome = await verifier.feed("#")
    if outcome == Outcome.VERIFIED:
        call.verifier = None
        call.runner.policy.mark_verified()
        return Step(say=[MSG_VERIFIED], goto=menu_for(call))
    if outcome == Outcome.FAILED:
        return Step(say=[MSG_MISMATCH], stay=True)
    if outcome == Outcome.LOCKED:
        return Step(say=[MSG_LOCKED], end=True)
    return Step(say=[MSG_TECHNICAL], end=True)


@node(
    "verify_pin",
    "Please enter your six digit voice PIN on the keypad, then press hash.",
    kind=Kind.DTMF,
    num_digits=6,
    finish_on_key="#",
    sensitive=True,
)
async def verify_pin(call: CallState, inp: Input) -> Step:
    return await _verify(call, inp)


@node(
    "verify_otp",
    "I have sent a six digit code to your phone. Please enter it on the keypad, then press hash.",
    kind=Kind.DTMF,
    num_digits=6,
    finish_on_key="#",
    sensitive=True,
)
async def verify_otp(call: CallState, inp: Input) -> Step:
    return await _verify(call, inp)
