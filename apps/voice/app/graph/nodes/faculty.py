"""FACULTY persona (after keypad OTP): record status and grievance."""

from __future__ import annotations

from app.graph.engine import Input, Kind, Step, node
from app.graph.nodes.public import file_grievance, grievance_confirm_prompt
from app.graph.nodes.util import answer_from_handbook, tool_failed, unclear
from app.graph.state import CallState
from app.speech.intent import has_any

MENU_FIRST = (
    "You can say: my status, or file a complaint about my record. Or say officer for a call back."
)
MENU_AGAIN = "Is there anything else? You can say my status, file a complaint, or goodbye."
MIN_COMPLAINT_CHARS = 10


def _menu_prompt(call: CallState) -> str:
    return MENU_AGAIN if call.slots.get("served") else MENU_FIRST


@node("fac_menu", _menu_prompt, hints="my status, complaint, officer, goodbye")
async def fac_menu(call: CallState, inp: Input) -> Step:
    t = inp.text
    if has_any(t, "complaint", "complain", "grievance", "dispute", "disagree", "wrong"):
        return Step(goto="fac_gv_desc")
    if has_any(t, "status", "record", "registration", "duplicate", "flag", "flagged", "mine", "my"):
        return await _status(call)
    if has_any(t, "no", "nothing", "nope"):
        return Step(say=["Thank you for calling. Goodbye."], end=True)
    if answer := await answer_from_handbook(call, t):
        return answer
    return await unclear(call, "fac_menu", summary="Faculty caller's request was not understood.")


async def _status(call: CallState) -> Step:
    res = await call.tool("get_faculty_status")  # the faculty id comes from the session
    if not res.ok:
        return await tool_failed(call, res)
    d = res.data
    parts = [f"Your registration status is {str(d.get('registration_status', 'unknown')).lower()}."]
    institutions = list(d.get("institutions") or [])
    if institutions:
        parts.append("You are associated with " + ", ".join(institutions[:3]) + ".")
    if d.get("duplicate_flag"):
        parts.append("Your record has a duplicate flag.")
        if d.get("resolution_steps"):
            parts.append("To resolve it: " + str(d["resolution_steps"]))
        parts.append("If you disagree, say complaint to file a grievance.")
    call.slots["served"] = True
    return Step(say=[" ".join(parts)], goto="fac_menu")


@node(
    "fac_gv_desc",
    "Please describe the problem with your record in a sentence or two.",
    open_ended=True,
)
async def fac_gv_desc(call: CallState, inp: Input) -> Step:
    if len(inp.text.strip()) < MIN_COMPLAINT_CHARS:
        return Step(say=["Please give a little more detail."], stay=True)
    call.slots["gv_desc"] = inp.text.strip()
    return Step(goto="fac_gv_confirm")


@node("fac_gv_confirm", grievance_confirm_prompt, kind=Kind.BOTH, num_digits=1)
async def fac_gv_confirm(call: CallState, inp: Input) -> Step:
    return await file_grievance(call, inp, from_text=False, back="fac_menu")
