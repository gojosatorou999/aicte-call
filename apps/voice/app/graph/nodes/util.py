"""Helpers shared by the node modules."""

from __future__ import annotations

from app.config import get_settings
from app.graph.engine import MSG_TECHNICAL, Step, fallback, handoff, menu_for
from app.graph.state import CallState, ToolResult
from app.speech.intent import has_any

MSG_UNCLEAR = "Sorry, I did not understand that."
MSG_LIMIT = (
    "I can only look up a few institutions per call. Please visit the AICTE website for more."
)


QUESTION_STARTS = (
    "what", "how", "when", "where", "why", "who", "which", "is", "are", "can", "could", "do",
    "does", "did", "will", "should", "tell",
)  # fmt: skip
POLICY_WORDS = (
    "rule", "rules", "policy", "norms", "regulation", "requirement", "requirements", "eligibility",
    "guideline", "guidelines", "fee", "fees", "affiliation", "process", "procedure", "documents",
)  # fmt: skip
HANDBOOK_NOTE = "That is general guidance; an officer decides on any application."
MAX_HANDBOOK_CHARS = 320


def looks_like_question(text: str) -> bool:
    words = text.lower().split()
    return "?" in text or bool(words and words[0].strip(",.") in QUESTION_STARTS)


async def answer_from_handbook(call: CallState, text: str) -> Step | None:
    """Answer a rules question from the approval handbook (the core's handbook search), or None.

    Only for things that read as questions or mention policy words, and only when the search
    actually finds something: no match means the caller gets the normal "I did not understand",
    never an invented answer."""
    if not (looks_like_question(text) or has_any(text, *POLICY_WORDS)):
        return None
    res = await call.tool("search_aph", {"query": text, "language": "en"})
    results = list(res.data.get("results") or []) if res.ok else []
    if not results:
        return None
    top = results[0]
    section = f" section {top['section']}" if top.get("section") else ""
    body = str(top.get("text", ""))[:MAX_HANDBOOK_CHARS].strip()
    call.slots["served"] = True
    return Step(
        say=[f"From the approval handbook,{section}: {body}", HANDBOOK_NOTE],
        goto=menu_for(call),
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
    return Step(say=["I cannot check that right now.", *done.say], goto=menu_for(call))
