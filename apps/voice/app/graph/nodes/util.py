"""Helpers shared by the node modules."""

from __future__ import annotations

from app.config import get_settings
from app.graph.engine import MSG_TECHNICAL, Step, fallback, handoff, menu_for
from app.graph.state import CallState, ToolResult

MSG_UNCLEAR = "Sorry, I did not understand that."
MSG_LIMIT = (
    "I can only look up a few institutions per call. Please visit the AICTE website for more."
)


def spell(ref: str) -> str:
    """Read a reference like APP-2026-0401 out character by character."""
    return ", ".join(" ".join(part) for part in ref.split("-") if part)


async def unclear(call: CallState, key: str, *, summary: str) -> Step:
    """The caller said something the node can't use. Re-ask a couple of times,
    then hand over (LLM relay in hybrid mode, otherwise an officer callback)."""
    if call.bump(key) >= get_settings().max_attempts_per_slot:
        call.clear(key)
        return await fallback(call, summary)
    return Step(say=[MSG_UNCLEAR], stay=True)


async def tool_failed(call: CallState, res: ToolResult) -> Step:
    """A tool call failed: explain honestly and offer an officer callback."""
    if res.error == "lookup_limit_reached":
        return Step(say=[MSG_LIMIT], goto=menu_for(call))
    if call.core_down:
        return Step(say=[MSG_TECHNICAL], end=True)
    done = await handoff(call, f"Tool failure during call ({res.error}); caller needs follow-up.")
    return Step(say=["I cannot check that right now.", *done.say], end=True)
