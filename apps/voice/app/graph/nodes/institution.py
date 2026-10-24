"""INSTITUTION persona: open items, commitments, extension requests, outbound calls.

The commitment flow mirrors the LLM path's rules, in code: the date is parsed
by `speech.dates`, checked by `SessionPolicy.commitment_error` (never in the
past, never beyond the policy limit), read back, and only saved after an
explicit yes. An extension is only ever *requested*, never granted.
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime
from typing import Any

from app import clock
from app.agent.personas import CallPurpose
from app.graph.engine import Input, Kind, Step, fallback, handoff, node
from app.graph.nodes.util import tool_failed, unclear
from app.graph.state import CallState
from app.speech import dates
from app.speech.intent import has_any, is_affirmative, is_negative, pick_number

MENU_FIRST = (
    "You can say: my open items, record a commitment, or ask for an extension. "
    "Or say officer for a call back."
)
MENU_AGAIN = "Is there anything else? You can say open items, commitment, extension, or goodbye."

ITEMS_WORDS = ("items", "pending", "due", "deadlines", "outstanding", "open")
COMMIT_WORDS = ("commit", "commitment", "promise", "submit", "upload")
EXTENSION_WORDS = ("extension", "extend", "more time", "postpone", "delay", "longer")
QUESTION_WORDS = ("question", "rule", "rules", "policy", "guideline", "how", "what")
MAX_ITEMS_SPOKEN = 3
DATE_HELP = "You can say a date, like the 20th of November, or next Friday."


def _menu_prompt(call: CallState) -> str:
    return MENU_AGAIN if call.slots.get("served") else MENU_FIRST


def _item_title(call: CallState) -> str:
    return str(call.slots.get("item", {}).get("title") or "this item")


def _item_ref(call: CallState) -> str:
    return str(call.slots.get("item", {}).get("subject_ref", ""))


def _outbound(call: CallState) -> bool:
    return call.job is not None


def _finish(call: CallState, say: list[str]) -> Step:
    """After a completed flow: end an outbound call, return to the menu on an inbound one."""
    if _outbound(call):
        return Step(say=[*say, "Thank you for your time. Goodbye."], end=True)
    call.slots["served"] = True
    return Step(say=say, goto="inst_menu")


# -- caller identified only by a typed number ------------------------------------------------------
@node(
    "inst_limited",
    "I could not verify this number, so I cannot discuss your records on this call. "
    "I can ask an officer to call you back. Shall I do that? Say yes or no.",
    kind=Kind.BOTH,
    num_digits=1,
)
async def inst_limited(call: CallState, inp: Input) -> Step:
    if is_affirmative(inp.any) or inp.digits == "1":
        return await handoff(
            call,
            "Institution caller identified only by a typed number; needs a verified follow-up.",
        )
    if is_negative(inp.any) or inp.digits == "2":
        return Step(say=["Understood. Thank you for calling. Goodbye."], end=True)
    return await unclear(
        call, "inst_limited", summary="Typed-number institution caller; reply not understood."
    )


# -- inbound menu ---------------------------------------------------------------------------------
@node(
    "inst_menu",
    _menu_prompt,
    hints="open items, commitment, extension, officer, goodbye",
)
async def inst_menu(call: CallState, inp: Input) -> Step:
    t = inp.text
    if has_any(t, *EXTENSION_WORDS):
        return await _select_item(call, "ext")
    if has_any(t, *COMMIT_WORDS):
        return await _select_item(call, "commit")
    if has_any(t, *ITEMS_WORDS):
        return await _list_items(call)
    if has_any(t, *QUESTION_WORDS):
        return await fallback(
            call, "Institution caller has a rules question the menu cannot answer."
        )
    if has_any(t, "no", "nothing", "nope"):
        return Step(say=["Thank you for calling. Goodbye."], end=True)
    return await unclear(
        call, "inst_menu", summary="Institution caller's request was not understood."
    )


async def _fetch_items(call: CallState) -> list[dict[str, Any]] | Step:
    res = await call.tool("get_open_items")
    if not res.ok:
        return await tool_failed(call, res)
    items = list(res.data.get("items") or [])
    call.slots["items"] = items
    return items


async def _list_items(call: CallState) -> Step:
    items = await _fetch_items(call)
    if isinstance(items, Step):
        return items
    if not items:
        call.slots["served"] = True
        return Step(say=["You have no open items."], goto="inst_menu")
    spoken = []
    for item in items[:MAX_ITEMS_SPOKEN]:
        due = f", due {_say_date(item['due_date'])}" if item.get("due_date") else ""
        late = ", overdue" if item.get("overdue") else ""
        spoken.append(f"{item.get('title', 'An item')}{due}{late}")
    head = f"You have {len(items)} open item{'s' if len(items) != 1 else ''}. "
    more = f" And {len(items) - MAX_ITEMS_SPOKEN} more." if len(items) > MAX_ITEMS_SPOKEN else ""
    call.slots["served"] = True
    return Step(say=[head + ". ".join(spoken) + "." + more], goto="inst_menu")


async def _select_item(call: CallState, purpose: str) -> Step:
    """Work out which item the caller means, then start `purpose` ('commit' | 'ext')."""
    items = await _fetch_items(call)
    if isinstance(items, Step):
        return items
    if not items:
        call.slots["served"] = True
        return Step(say=["You have no open items to record a date for."], goto="inst_menu")
    call.slots["purpose"] = purpose
    if len(items) == 1:
        call.slots["item"] = items[0]
        return Step(goto="inst_commit_date" if purpose == "commit" else "inst_ext_date")
    return Step(goto="inst_pick_item")


def _pick_item_prompt(call: CallState) -> str:
    items = call.slots.get("items", [])[:MAX_ITEMS_SPOKEN]
    listing = ". ".join(f"{i}: {it.get('title', 'item')}" for i, it in enumerate(items, start=1))
    return f"Which item? {listing}. Say or press the number."


@node("inst_pick_item", _pick_item_prompt, kind=Kind.BOTH, num_digits=1)
async def inst_pick_item(call: CallState, inp: Input) -> Step:
    items = call.slots.get("items", [])[:MAX_ITEMS_SPOKEN]
    chosen = pick_number(inp.any, len(items))
    if chosen is None:
        return await unclear(
            call, "inst_pick_item", summary="Could not tell which item the caller meant."
        )
    call.clear("inst_pick_item")
    call.slots["item"] = items[chosen - 1]
    return Step(
        goto="inst_commit_date" if call.slots.get("purpose") == "commit" else "inst_ext_date"
    )


# -- commitment -------------------------------------------------------------------------------------
def _commit_date_prompt(call: CallState) -> str:
    return f"By when will you complete {_item_title(call)}? {DATE_HELP}"


@node(
    "inst_commit_date", _commit_date_prompt, hints="tomorrow, next Friday, 20th November, extension"
)
async def inst_commit_date(call: CallState, inp: Input) -> Step:
    t = inp.text
    if has_any(t, *EXTENSION_WORDS) or (call.slots.get("ext_date") and is_affirmative(t)):
        if call.slots.get("ext_date"):  # they asked for a date beyond policy: request that date
            call.slots["req_date"] = call.slots.pop("ext_date")
            return Step(goto="inst_ext_reason")
        return Step(goto="inst_ext_date")
    try:
        chosen = dates.resolve(t, clock.today(), "en")
    except dates.DateParseError:
        return await unclear(
            call, "inst_commit_date", summary="Could not understand the commitment date."
        )
    call.clear("inst_commit_date")

    err = call.runner.policy.commitment_error(chosen.date)
    if err and err["error"] == "date_in_past":
        return Step(
            say=["That date has already passed. Please give a date in the future."], stay=True
        )
    if err:  # beyond policy
        call.slots["ext_date"] = chosen.date
        latest = _say_date(err["max_date"])
        return Step(
            say=[
                "Only the competent authority can grant extensions. "
                f"The latest date I can record is {latest}. "
                "Give me an earlier date, or say extension request and I will log it for officer review."
            ],
            stay=True,
        )
    call.slots.pop("ext_date", None)
    call.slots["commit_date"] = chosen.date
    return Step(goto="inst_commit_reason")


@node(
    "inst_commit_reason",
    "Briefly, what is the reason for the timeline? You can say skip.",
    open_ended=True,
)
async def inst_commit_reason(call: CallState, inp: Input) -> Step:
    call.slots["reason"] = "" if has_any(inp.text, "skip", "none", "nothing") else inp.text.strip()
    return Step(goto="inst_commit_confirm")


def _commit_confirm_prompt(call: CallState) -> str:
    when = dates.resolve(call.slots["commit_date"].isoformat(), clock.today(), "en").spoken_en
    return f"To confirm: you will complete {_item_title(call)} by {when}. Is that correct?"


@node("inst_commit_confirm", _commit_confirm_prompt, kind=Kind.BOTH, num_digits=1)
async def inst_commit_confirm(call: CallState, inp: Input) -> Step:
    if is_negative(inp.any):
        call.clear("commit_date", "reason")
        return Step(say=["Okay."], goto="inst_commit_date")
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(
            call, "inst_commit_confirm", summary="Caller did not confirm the commitment."
        )
    committed: date = call.slots["commit_date"]
    res = await call.tool(
        "record_commitment",
        {
            "subject_ref": _item_ref(call),
            "committed_date": committed.isoformat(),
            "reason": call.slots.get("reason") or "Not stated",
        },
        confirmed=True,
    )
    if not res.ok:
        if res.error in {"date_in_past", "date_beyond_policy"}:
            return Step(
                say=["That date can no longer be recorded. Let us choose another."],
                goto="inst_commit_date",
            )
        return await tool_failed(call, res)
    call.clear("inst_commit_confirm")
    call.slots["recorded"] = committed
    when = dates.resolve(committed.isoformat(), clock.today(), "en").spoken_en
    return Step(say=[f"Recorded. {_item_title(call)} by {when}."], goto="inst_link")


_CHANNELS = (
    ("WHATSAPP", ("whatsapp", "whats app", "watsapp", "whatsap")),
    ("SMS", ("sms", "text", "message")),
    ("EMAIL", ("email", "mail")),
)


@node(
    "inst_link",
    "Shall I send you the upload link? Say WhatsApp, S M S, or email. Or say no.",
    hints="WhatsApp, SMS, email, no",
)
async def inst_link(call: CallState, inp: Input) -> Step:
    t = inp.text
    channel = next((name for name, words in _CHANNELS if has_any(t, *words)), None)
    if channel is None:
        if is_negative(t):
            return _finish(call, ["Okay, no link will be sent."])
        return await unclear(call, "inst_link", summary="Caller's link channel was not understood.")
    res = await call.tool("send_upload_link", {"subject_ref": _item_ref(call), "channel": channel})
    if not res.ok:
        return await tool_failed(call, res)
    call.clear("inst_link")
    spoken = {"WHATSAPP": "WhatsApp", "SMS": "S M S", "EMAIL": "email"}[channel]
    return _finish(call, [f"I have sent the upload link by {spoken}."])


# -- extension request ----------------------------------------------------------------------------------
@node(
    "inst_ext_date",
    lambda c: f"What date do you need for {_item_title(c)}? {DATE_HELP}",
    hints="tomorrow, next Friday, 20th December",
)
async def inst_ext_date(call: CallState, inp: Input) -> Step:
    try:
        chosen = dates.resolve(inp.text, clock.today(), "en")
    except dates.DateParseError:
        return await unclear(
            call, "inst_ext_date", summary="Could not understand the extension date."
        )
    call.clear("inst_ext_date")
    if chosen.date < clock.today():
        return Step(
            say=["That date has already passed. Please give a date in the future."], stay=True
        )
    call.slots["req_date"] = chosen.date
    return Step(goto="inst_ext_reason")


@node("inst_ext_reason", "What is the reason you need more time?", open_ended=True)
async def inst_ext_reason(call: CallState, inp: Input) -> Step:
    call.slots["reason"] = inp.text.strip()
    return Step(goto="inst_ext_confirm")


def _ext_confirm_prompt(call: CallState) -> str:
    when = dates.resolve(call.slots["req_date"].isoformat(), clock.today(), "en").spoken_en
    return (
        f"I will log a request for more time on {_item_title(call)}, until {when}, "
        "for an officer to review. This does not grant an extension. Is that correct?"
    )


@node("inst_ext_confirm", _ext_confirm_prompt, kind=Kind.BOTH, num_digits=1)
async def inst_ext_confirm(call: CallState, inp: Input) -> Step:
    if is_negative(inp.any):
        call.clear("req_date", "reason")
        return Step(say=["Okay, I have not logged anything."], goto="inst_menu")
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(
            call, "inst_ext_confirm", summary="Caller did not confirm the extension request."
        )
    res = await call.tool(
        "log_extension_request",
        {
            "subject_ref": _item_ref(call),
            "requested_date": call.slots["req_date"].isoformat(),
            "reason": call.slots.get("reason") or "Not stated",
        },
        confirmed=True,
    )
    if not res.ok:
        return await tool_failed(call, res)
    call.clear("inst_ext_confirm", "req_date", "reason")
    return _finish(
        call, ["Your request is logged for officer review. An officer will contact you."]
    )


# -- outbound call opening ---------------------------------------------------------------------------------
def purpose_statement(call: CallState) -> str:
    job = call.job
    assert job is not None
    due = _say_date(job.due_date.isoformat()) if job.due_date else None
    committed = _say_date(job.committed_date.isoformat()) if job.committed_date else None
    item = job.item_title
    return {
        CallPurpose.DEFICIENCY_PENDING: f"A deficiency raised on {item} has not been answered yet.",
        CallPurpose.UNDERTAKING_DUE: f"Your undertaking, {item}, is due on {due}.",
        CallPurpose.UNDERTAKING_OVERDUE: f"Your undertaking, {item}, was due on {due} and is now overdue.",
        CallPurpose.DOC_EXPIRY_REMINDER: f"Your document, {item}, expires on {due}.",
        CallPurpose.DOC_EXPIRED: f"Your document, {item}, expired on {due}.",
        CallPurpose.EVC_VISIT_CONFIRM: f"An expert committee visit is scheduled for {due}.",
        CallPurpose.BROKEN_COMMITMENT: f"You committed to complete {item} by {committed}, but it is still pending.",
    }[job.purpose]


def _open_prompt(call: CallState) -> str:
    job = call.job
    assert job is not None
    return (
        f"This is an automated call from AICTE's Approval and Compliance Portal for {job.institution_name}. "
        f"Am I speaking with {job.contact_name}?"
    )


@node("out_open", _open_prompt, kind=Kind.BOTH, num_digits=1, hints="yes, no")
async def out_open(call: CallState, inp: Input) -> Step:
    job = call.job
    assert job is not None
    if is_negative(inp.any) or inp.digits == "2":
        return Step(goto="out_callback")
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(
            call, "out_open", summary="Could not confirm who answered the outbound call."
        )
    call.clear("out_open")
    call.slots["item"] = {"subject_ref": job.subject_ref, "title": job.item_title}
    call.slots["purpose"] = "commit"
    statement = purpose_statement(call)
    if job.purpose == CallPurpose.EVC_VISIT_CONFIRM:
        return Step(say=[statement], goto="out_visit")
    return Step(say=[statement], goto="inst_commit_date")


@node(
    "out_visit",
    "Can you confirm that the campus will be ready on that date? Say yes or no.",
    kind=Kind.BOTH,
    num_digits=1,
)
async def out_visit(call: CallState, inp: Input) -> Step:
    job = call.job
    assert job is not None
    if is_affirmative(inp.any) or inp.digits == "1":
        return Step(say=["Thank you for confirming. Goodbye."], end=True)
    if is_negative(inp.any) or inp.digits == "2":
        due = job.due_date.isoformat() if job.due_date else "the scheduled date"
        done = await handoff(
            call, f"Institution cannot receive the committee visit on {due}; needs rescheduling."
        )
        return Step(say=["Understood. I will not change the date myself.", *done.say], end=True)
    return await unclear(call, "out_visit", summary="Visit confirmation was not understood.")


@node(
    "out_callback",
    "I am sorry to have disturbed you. When would be a good time to call back? For example, tomorrow at 3 PM.",
    open_ended=True,
)
async def out_callback(call: CallState, inp: Input) -> Step:
    """Wrong person answered: collect a date and time for a call back."""
    if "cb_date" not in call.slots:
        with contextlib.suppress(dates.DateParseError):
            call.slots["cb_date"] = dates.resolve(inp.text, clock.today(), "en").date
    if "cb_time" not in call.slots:
        with contextlib.suppress(dates.DateParseError):
            call.slots["cb_time"] = dates.resolve_time(inp.text)
    have_date, have_time = "cb_date" in call.slots, "cb_time" in call.slots
    if have_date and have_time:
        return Step(goto="out_callback_confirm")
    if not have_date and not have_time:
        return await unclear(
            call, "out_callback", summary="Wrong person answered; no callback time given."
        )
    ask = "What time?" if have_date else "On which day?"
    return Step(say=[ask], stay=True)


def _callback_when(call: CallState) -> datetime:
    hour, minute = call.slots["cb_time"]
    return datetime.combine(
        call.slots["cb_date"], datetime.min.time(), tzinfo=clock.now().tzinfo
    ).replace(hour=hour, minute=minute)


def _callback_prompt(call: CallState) -> str:
    when = _callback_when(call)
    day = dates.resolve(when.date().isoformat(), clock.today(), "en").spoken_en
    return (
        f"I will call back on {day} at {dates.spoken_time(when.hour, when.minute)}. Is that right?"
    )


@node("out_callback_confirm", _callback_prompt, kind=Kind.BOTH, num_digits=1)
async def out_callback_confirm(call: CallState, inp: Input) -> Step:
    job = call.job
    assert job is not None
    if is_negative(inp.any):
        call.clear("cb_date", "cb_time")
        return Step(say=["Okay."], goto="out_callback")
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(
            call, "out_callback_confirm", summary="Callback time was not confirmed."
        )
    when = _callback_when(call)
    res = await call.tool(
        "schedule_callback", {"when": when.isoformat(), "contact_name": job.contact_name}
    )
    if res.error == "time_in_past":
        call.clear("cb_date", "cb_time")
        return Step(say=["That time has already passed."], goto="out_callback")
    if not res.ok:
        return await tool_failed(call, res)
    return Step(say=["Thank you. We will call you then. Goodbye."], end=True)


def _say_date(iso: str | date) -> str:
    value = iso.isoformat() if isinstance(iso, date) else str(iso)
    return dates.resolve(value, clock.today(), "en").spoken_en
