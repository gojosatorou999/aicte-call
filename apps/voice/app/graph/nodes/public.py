"""PUBLIC persona: approval lookups and complaints (app.md 12.3)."""

from __future__ import annotations

from typing import Any

from app.graph.engine import Input, Kind, Step, node
from app.graph.nodes.util import spell, tool_failed, unclear
from app.graph.state import CallState
from app.speech.intent import has_any, is_affirmative, is_negative, pick_number

MENU_FIRST = (
    "How can I help? Say check an institution, or press 1. Say file a complaint, or press 2. "
    "Or say officer to request a call back."
)
MENU_AGAIN = "Is there anything else? You can check another institution, file a complaint, or say goodbye."

CHECK_WORDS = (
    "approved", "approval", "institution", "college", "university", "check", "status",
    "recognised", "recognized", "valid", "affiliated",
)  # fmt: skip
COMPLAINT_WORDS = ("complaint", "complain", "grievance", "fake", "fraud", "report", "cheating")
FAKE_WORDS = ("fake", "fraud", "unapproved", "bogus", "illegal", "not approved", "without approval")
# Words dropped when the caller says the whole question ("is X approved by AICTE") so that
# only the institution's name goes to the search.
FILLER = frozenset(
    [
        "is", "are", "was", "am", "please", "tell", "me", "can", "you", "could", "check", "if",
        "whether", "approved", "approval", "by", "aicte", "recognised", "recognized", "valid",
        "status", "the", "a", "an", "for", "i", "want", "to", "know", "and", "also", "does", "it",
        "have", "verify", "affiliated", "another", "other", "different", "something", "one",
    ]
)  # fmt: skip


def _trim_filler(text: str) -> str:
    """Drop filler only from the two ends: "Sri Sai College of Engineering" keeps its "College"."""
    words = text.split()

    def filler(w: str) -> bool:
        return w.lower().strip(".,?!") in FILLER

    while words and filler(words[0]):
        words.pop(0)
    while words and filler(words[-1]):
        words.pop()
    return " ".join(words)
MAX_COURSES_SPOKEN = 3
MAX_CHOICES = 3
MIN_COMPLAINT_CHARS = 10


def _menu_prompt(call: CallState) -> str:
    return MENU_AGAIN if call.slots.get("served") else MENU_FIRST


def _looks_fake(text: str) -> bool:
    """A complaint about a fake / unapproved institution (vs. an ordinary grievance)."""
    if has_any(text, *FAKE_WORDS):
        return True
    return has_any(text, "approval", "approved", "recognition", "recognised", "recognized") and has_any(
        text, "without", "no", "not", "never", "lack", "lacks"
    )


@node(
    "pub_menu",
    _menu_prompt,
    kind=Kind.BOTH,
    num_digits=1,
    hints="check an institution, file a complaint, officer, goodbye",
)
async def pub_menu(call: CallState, inp: Input) -> Step:
    text = inp.text
    if inp.digits == "2" or has_any(text, *COMPLAINT_WORDS):
        return Step(goto="pub_gv_inst")
    if inp.digits == "1" or has_any(text, *CHECK_WORDS):
        name = _trim_filler(text)
        if len(name.split()) >= 2:  # "is Sri Venkateswara Institute approved": the name came with it
            return await _find_institution(call, name)
        return Step(goto="pub_inst_name")
    if has_any(text, "no", "nothing", "nope"):
        return Step(say=["Thank you for calling. Goodbye."], end=True)
    return await unclear(
        call, "pub_menu", summary="Public caller's request was not understood by the menu."
    )


# -- institution lookup ------------------------------------------------------------------------
@node(
    "pub_inst_name",
    "Please say the name of the institution, and the city if you know it.",
    open_ended=True,
)
async def pub_inst_name(call: CallState, inp: Input) -> Step:
    return await _find_institution(call, inp.text)


async def _find_institution(call: CallState, name: str) -> Step:
    res = await call.tool("search_institutions", {"name": name})
    if not res.ok:
        return await tool_failed(call, res)
    results: list[dict[str, Any]] = list(res.data.get("results") or [])
    if not results:
        if call.bump("pub_inst_name") >= 2:
            call.clear("pub_inst_name")
            return Step(
                say=[
                    "I could not find that institution. "
                    "You can check the AICTE website for the official list."
                ],
                goto="pub_menu",
            )
        return Step(
            say=["I could not find an institution with that name. Please try again."],
            goto="pub_inst_name",
        )
    call.clear("pub_inst_name")
    if len(results) == 1:
        return await _answer_approval(call, results[0])
    call.slots["choices"] = results[:MAX_CHOICES]
    return Step(say=[f"I found {len(call.slots['choices'])} matches."], goto="pub_pick")


def _pick_prompt(call: CallState) -> str:
    parts = [
        f"{i}: {c.get('name', 'unnamed')}" + (f", {c['city']}" if c.get("city") else "")
        for i, c in enumerate(call.slots.get("choices", []), start=1)
    ]
    return "Which one? " + ". ".join(parts) + ". Say or press the number."


@node("pub_pick", _pick_prompt, kind=Kind.BOTH, num_digits=1)
async def pub_pick(call: CallState, inp: Input) -> Step:
    choices: list[dict[str, Any]] = call.slots.get("choices", [])
    chosen = pick_number(inp.any, len(choices))
    if chosen is None:
        if call.bump("pub_pick") >= 2:
            call.clear("pub_pick")
            return Step(say=["Let us try that again."], goto="pub_inst_name")
        return Step(say=["Sorry, please say or press the number."], stay=True)
    call.clear("pub_pick", "choices")
    return await _answer_approval(call, choices[chosen - 1])


async def _answer_approval(call: CallState, inst: dict[str, Any]) -> Step:
    res = await call.tool("get_public_approval_status", {"institution_id": inst.get("id", "")})
    if not res.ok:
        return await tool_failed(call, res)
    data = res.data
    name = inst.get("name", "That institution")
    year = data.get("academic_year", "the current academic year")
    if data.get("approved"):
        courses = list(data.get("courses") or [])
        spoken = [f"{c.get('name')}, intake {c.get('intake')}" for c in courses[:MAX_COURSES_SPOKEN]]
        text = f"{name} is approved for {year}."
        if spoken:
            text += " Approved courses: " + "; ".join(spoken) + "."
        if len(courses) > MAX_COURSES_SPOKEN:
            text += f" And {len(courses) - MAX_COURSES_SPOKEN} more."
    else:
        text = (
            f"{name} is not listed as approved for {year}. "
            "For the official record, please check the AICTE website."
        )
    call.slots["served"] = True
    return Step(say=[text], goto="pub_menu")


# -- complaint ----------------------------------------------------------------------------------
@node("pub_gv_inst", "Which institution is the complaint about?", open_ended=True)
async def pub_gv_inst(call: CallState, inp: Input) -> Step:
    call.slots["gv_inst"] = inp.text
    return Step(goto="pub_gv_desc")


@node("pub_gv_desc", "Please describe the problem in a sentence or two.", open_ended=True)
async def pub_gv_desc(call: CallState, inp: Input) -> Step:
    if len(inp.text.strip()) < MIN_COMPLAINT_CHARS:
        return Step(say=["Please give a little more detail."], stay=True)
    call.slots["gv_desc"] = inp.text.strip()
    return Step(goto="pub_gv_confirm")


def grievance_confirm_prompt(call: CallState) -> str:
    about = f" about {call.slots['gv_inst']}" if call.slots.get("gv_inst") else ""
    return (
        f"I will file a complaint{about}: {call.slots.get('gv_desc', '')}. "
        "Say yes to file it, or no to cancel."
    )


@node("pub_gv_confirm", grievance_confirm_prompt, kind=Kind.BOTH, num_digits=1)
async def pub_gv_confirm(call: CallState, inp: Input) -> Step:
    return await file_grievance(call, inp, from_text=True, back="pub_menu")


async def file_grievance(call: CallState, inp: Input, *, from_text: bool, back: str) -> Step:
    """Shared yes/no step for filing a grievance (public and faculty)."""
    if is_negative(inp.any):
        call.clear("gv_inst", "gv_desc")
        return Step(say=["Okay, I have not filed anything."], goto=back)
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(call, "gv_confirm", summary="Caller did not confirm the grievance.")
    desc = call.slots.get("gv_desc", "")
    category = ("FAKE_COLLEGE" if _looks_fake(desc) else "OTHER") if from_text else "FACULTY_RECORD"
    args: dict[str, Any] = {"category": category, "description": desc}
    if call.slots.get("gv_inst"):
        args["institution_ref"] = call.slots["gv_inst"]
    res = await call.tool("create_grievance", args, confirmed=True)
    if not res.ok:
        return await tool_failed(call, res)
    call.clear("gv_inst", "gv_desc", "gv_confirm")
    ticket = str(res.data.get("ticket_id", ""))
    say = ["Your complaint is filed."]
    if ticket:
        say[0] += f" Your ticket number is {spell(ticket)}."
        say.append("I will repeat it: " + spell(ticket) + ".")
    call.slots["served"] = True
    return Step(say=say, goto=back)
