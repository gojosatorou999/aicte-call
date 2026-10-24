"""PUBLIC persona: say a college name, hear whether it is approved (app.md 12.3).

The conversation is deliberately minimal:

    "Please say the name of the college."   ->  the caller says a name, any name
    "<College> is approved for 2026-27 ..." ->  the answer; the line stays open for questions
    (silence, or "that's all")              ->  "Have a nice day. Goodbye." and hang up

Questions the caller asks while or after the answer are handled on the open line: about the
college just discussed (courses, intake, where, status), or about the rules, from the approval
handbook. "Complaint" starts a complaint; once its reference number is read out the call ends.
"""

from __future__ import annotations

from typing import Any

from app.graph.engine import MSG_BYE, OFFICER, Input, Kind, Step, handoff, node
from app.graph.nodes.util import POLICY_WORDS, answer_from_handbook, spell, tool_failed, unclear
from app.graph.state import CallState
from app.speech.intent import has_any, is_affirmative, is_negative, pick_number

COMPLAINT_WORDS = ("complaint", "complain", "grievance", "fake", "fraud", "report", "cheating")
FAKE_WORDS = ("fake", "fraud", "unapproved", "bogus", "illegal", "not approved", "without approval")
# Only a clear goodbye ends the call. ("thanks" / "stop" are not goodbyes: people say them mid-call.)
BYE_WORDS = (
    "goodbye",
    "bye",
    "that is all",
    "thats all",
    "nothing else",
    "no more",
    "hang up",
    "end the call",
)
THANKS_WORDS = ("thanks", "thank")
# Words trimmed from the *ends* of what the caller said ("is X approved by AICTE", "check X") so
# that only the college's name goes to the search. Never removed from the middle: "Sri Sai
# College of Engineering" keeps its "College".
FILLER = frozenset(
    [
        "is",
        "are",
        "was",
        "am",
        "please",
        "tell",
        "me",
        "can",
        "you",
        "could",
        "check",
        "if",
        "whether",
        "approved",
        "approval",
        "by",
        "aicte",
        "recognised",
        "recognized",
        "valid",
        "status",
        "the",
        "a",
        "an",
        "for",
        "i",
        "want",
        "to",
        "know",
        "and",
        "also",
        "does",
        "it",
        "have",
        "verify",
        "affiliated",
        "another",
        "other",
        "different",
        "something",
        "one",
        "about",
        "find",
        "search",
        "look",
        "up",
        "details",
        "information",
    ]
)
MAX_COURSES_SPOKEN = 3
MAX_COURSES_LISTED = 8
MAX_CHOICES = 3
MIN_COMPLAINT_CHARS = 10
MAX_NAME_ATTEMPTS = 2

# Follow-up questions about the college just discussed.
COURSE_WORDS = (
    "course", "courses", "branch", "branches", "program", "programs", "programme", "streams",
)  # fmt: skip
SEAT_WORDS = ("seats", "seat", "intake", "capacity", "admissions", "vacancies")
WHERE_WORDS = ("where", "city", "located", "location", "state", "address")
STATUS_WORDS = ("approved", "approval", "valid", "status", "recognised", "recognized", "affiliated")
OTHER_WORDS = ("another", "other", "different", "new", "next")
# Said on their own these are not a name ("check another college"): ask which one.
GENERIC_NAMES = frozenset(
    {"college", "colleges", "institute", "institution", "university", "school"}
)


def _trim_filler(text: str) -> str:
    """Drop filler only from the two ends of the sentence."""
    words = text.split()

    def filler(w: str) -> bool:
        return w.lower().strip(".,?!") in FILLER

    while words and filler(words[0]):
        words.pop(0)
    while words and filler(words[-1]):
        words.pop()
    return " ".join(words)


def _looks_fake(text: str) -> bool:
    """A complaint about a fake / unapproved institution (vs. an ordinary grievance)."""
    if has_any(text, *FAKE_WORDS):
        return True
    return has_any(
        text, "approval", "approved", "recognition", "recognised", "recognized"
    ) and has_any(text, "without", "no", "not", "never", "lack", "lacks")


def _note_lookup(call: CallState, **fields: Any) -> None:
    """Remember what was asked and what came of it, for the admin dashboard / CSV."""
    call.facts.setdefault("intent", "college_check")
    call.facts.setdefault("lookups", []).append(fields)


# -- the college name ------------------------------------------------------------------------------
@node(
    "pub_menu",
    "Please say the name of the college.",
    open_ended=True,
    hints="college, institute, institution, university, engineering college, complaint",
)
async def pub_menu(call: CallState, inp: Input) -> Step:
    return await _public_input(call, inp.text)


def _more_prompt(call: CallState) -> str:
    """Silent after an answer; only if the caller goes quiet do we gently say we are still here."""
    if call.silence:
        return "You can ask about this college, or say the name of another one."
    return ""


@node(
    "pub_more",
    _more_prompt,  # nothing is asked after an answer: the line just stays open for a question
    open_ended=True,
    hints="courses, intake, seats, where, another college, complaint, goodbye",
)
async def pub_more(call: CallState, inp: Input) -> Step:
    return await _public_input(call, inp.text)


async def _public_input(call: CallState, text: str) -> Step:
    """Whatever the caller says on the public line: a name, a question, a complaint or goodbye."""
    if has_any(text, *BYE_WORDS) and len(text.split()) <= 5:
        return Step(say=[MSG_BYE], end=True)
    if has_any(text, *THANKS_WORDS) and len(text.split()) <= 3:
        return Step(say=["You are welcome."], stay=True)  # stays on the line
    if has_any(text, *OFFICER) and len(text.split()) <= 6:
        return await handoff(call, "Caller asked to speak to an officer.")
    if has_any(text, *COMPLAINT_WORDS):
        return Step(goto="pub_gv_inst")
    if (follow_up := _follow_up(call, text)) is not None:  # about the college just discussed
        return follow_up
    if has_any(text, *POLICY_WORDS) and (answer := await answer_from_handbook(call, text)):
        return answer
    name = _trim_filler(text)
    if not name:
        return Step(say=["Sorry, I did not catch that."], stay=True)
    if name.lower().strip(".,?!") in GENERIC_NAMES:
        return Step(goto="pub_menu")  # "which college?" - the prompt asks it again
    return await _find_institution(call, name)


def _follow_up(call: CallState, text: str) -> Step | None:
    inst = call.slots.get("last_inst")
    if not inst or has_any(text, *OTHER_WORDS):
        return None
    name, courses = inst["name"], inst["courses"]
    say: str | None = None
    if has_any(text, *COURSE_WORDS):
        listed = ", ".join(c["name"] for c in courses[:MAX_COURSES_LISTED])
        say = (
            f"{name} is approved for {listed}."
            if listed
            else f"I have no approved courses on record for {name}."
        )
    elif has_any(text, *SEAT_WORDS):
        listed = "; ".join(
            f"{c['name']}, {c['intake']} seats" for c in courses[:MAX_COURSES_LISTED]
        )
        say = (
            f"Approved intake at {name}: {listed}."
            if listed
            else f"I have no intake figures on record for {name}."
        )
    elif has_any(text, *WHERE_WORDS) and (inst.get("city") or inst.get("state")):
        place = ", ".join(p for p in (inst.get("city"), inst.get("state")) if p)
        say = f"{name} is in {place}."
    elif has_any(text, *STATUS_WORDS) and len(text.split()) <= 8:
        say = inst["status_text"]
    if say is None:
        return None
    return Step(say=[say], goto="pub_more")


async def _find_institution(call: CallState, name: str) -> Step:
    res = await call.tool("search_institutions", {"name": name})
    if not res.ok:
        return await tool_failed(call, res)
    results: list[dict[str, Any]] = list(res.data.get("results") or [])
    if not results:
        _note_lookup(call, said=name, matched="", result="not_found")
        tries = call.bump("pub_name")
        say = [f"I could not find a college called {name}."]
        if tries >= MAX_NAME_ATTEMPTS:
            say.append("It may be spelled differently; try the main words, or add the city.")
        # Never hang up here: keep the line open and ask again.
        return Step(say=say, goto="pub_menu")
    call.clear("pub_name")
    if len(results) == 1:
        return await _answer_approval(call, results[0], said=name)
    call.slots["choices"] = results[:MAX_CHOICES]
    call.slots["said"] = name
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
            return Step(say=["Let us try that again."], goto="pub_menu")
        return Step(say=["Sorry, please say or press the number."], stay=True)
    call.clear("pub_pick", "choices")
    return await _answer_approval(call, choices[chosen - 1], said=call.slots.get("said", ""))


async def _answer_approval(call: CallState, inst: dict[str, Any], *, said: str) -> Step:
    res = await call.tool("get_public_approval_status", {"institution_id": inst.get("id", "")})
    if not res.ok:
        return await tool_failed(call, res)
    data = res.data
    name = inst.get("name", "That college")
    year = data.get("academic_year", "the current academic year")
    approved = bool(data.get("approved"))
    courses = list(data.get("courses") or []) if approved else []
    if approved:
        spoken = [
            f"{c.get('name')}, intake {c.get('intake')}" for c in courses[:MAX_COURSES_SPOKEN]
        ]
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
    call.slots["last_inst"] = {
        "name": name,
        "city": inst.get("city"),
        "state": inst.get("state"),
        "courses": [{"name": str(c.get("name")), "intake": c.get("intake")} for c in courses],
        "status_text": text.split(" Approved courses")[0],
    }
    _note_lookup(
        call,
        said=said,
        matched=name,
        result="approved" if approved else "not_approved",
        courses=", ".join(str(c.get("name")) for c in courses),
    )
    return Step(say=[text], goto="pub_more")


# -- complaint ----------------------------------------------------------------------------------
@node("pub_gv_inst", "Which college is the complaint about?", open_ended=True)
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
    ticket = str(res.data.get("ticket_id", ""))
    call.facts.update(
        intent="complaint",
        complaint_college=call.slots.get("gv_inst", ""),
        complaint_ref=ticket,
        complaint_text=desc,
    )
    call.clear("gv_inst", "gv_desc", "gv_confirm")
    say = ["Your complaint is filed."]
    if ticket:
        say[0] += f" Your reference number is {spell(ticket)}."
    # The reference is given once, then the call is over: no "anything else?".
    return Step(say=[*say, MSG_BYE], end=True)
