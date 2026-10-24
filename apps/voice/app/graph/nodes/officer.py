"""OFFICER persona (after keypad PIN): queue, overdue, region stats, emailed reports, reminders.

Answers are short and numeric; detail goes by email (app.md 12.3).
"""

from __future__ import annotations

from typing import Any

from app.graph.engine import Input, Kind, Step, node
from app.graph.nodes.util import spell, tool_failed, unclear
from app.graph.state import CallState
from app.speech.intent import has_any, is_affirmative, is_negative

MENU_FIRST = "Say: queue, overdue, region statistics, or email report."
MENU_AGAIN = "Anything else? Say queue, overdue, statistics, email report, or goodbye."

KIND_WORDS = {
    "STAGE": ("stage", "stages", "application", "applications", "case", "cases"),
    "UNDERTAKING": ("undertaking", "undertakings", "commitment", "commitments"),
    "DOCUMENT": ("document", "documents", "expiry", "expired"),
    "EVC": ("visit", "visits", "evc", "inspection", "inspections"),
}
OVERDUE_LIMIT = 3
REPORT_WORDS = {"pendency": ("pendency", "pending"), "overdue": ("overdue", "late", "delayed")}


def _menu_prompt(call: CallState) -> str:
    return MENU_AGAIN if call.slots.get("served") else MENU_FIRST


@node("off_menu", _menu_prompt, hints="queue, overdue, statistics, email report, reminder, goodbye")
async def off_menu(call: CallState, inp: Input) -> Step:
    t = inp.text
    if has_any(t, "remind", "reminder"):
        return await _start_reminder(call)
    if has_any(t, "email", "mail", "report"):
        report = _report_in(t)
        return await _send_report(call, report) if report else Step(goto="off_report")
    if has_any(t, "overdue", "late", "delayed", "breach"):
        kind = _kind_in(t)
        return await _list_overdue(call, kind) if kind else Step(goto="off_overdue_kind")
    if has_any(t, "region", "statistics", "stats", "numbers", "regional"):
        return await _region_stats(call)
    if has_any(t, "queue", "summary", "pending", "cases", "mine"):
        return await _queue(call)
    if has_any(t, "no", "nothing", "nope"):
        return Step(say=["Thank you. Goodbye."], end=True)
    return await unclear(call, "off_menu", summary="Officer's request was not understood.")


async def _queue(call: CallState) -> Step:
    res = await call.tool("get_my_queue_summary")
    if not res.ok:
        return await tool_failed(call, res)
    d = res.data
    call.slots["served"] = True
    return Step(
        say=[
            f"You have {d.get('pending', 0)} pending, {d.get('overdue', 0)} overdue, "
            f"and {d.get('due_this_week', 0)} due this week."
        ],
        goto="off_menu",
    )


async def _region_stats(call: CallState) -> Step:
    res = await call.tool("get_region_stats")
    if not res.ok:
        return await tool_failed(call, res)
    d = res.data
    call.slots["served"] = True
    return Step(
        say=[
            f"In your region, {d.get('applications_in_progress', 0)} applications are in progress "
            f"and {d.get('overdue', 0)} are overdue."
        ],
        goto="off_menu",
    )


# -- overdue list ---------------------------------------------------------------------------------
@node(
    "off_overdue_kind",
    "Which overdue items: stages, undertakings, documents, or visits?",
    hints="stages, undertakings, documents, visits",
)
async def off_overdue_kind(call: CallState, inp: Input) -> Step:
    kind = _kind_in(inp.text)
    if kind is None:
        return await unclear(call, "off_overdue_kind", summary="Overdue category was not understood.")
    return await _list_overdue(call, kind)


def _kind_in(text: str) -> str | None:
    return next((k for k, words in KIND_WORDS.items() if has_any(text, *words)), None)


def _report_in(text: str) -> str | None:
    return next((r for r, words in REPORT_WORDS.items() if has_any(text, *words)), None)


async def _list_overdue(call: CallState, kind: str) -> Step:
    call.clear("off_overdue_kind")
    res = await call.tool("list_overdue", {"kind": kind, "limit": OVERDUE_LIMIT})
    if not res.ok:
        return await tool_failed(call, res)
    items: list[dict[str, Any]] = list(res.data.get("items") or [])
    call.slots["served"] = True
    call.slots["last_overdue"] = items
    label = kind.lower() if kind != "EVC" else "visit"
    if not items:
        return Step(say=[f"There are no overdue {label} items."], goto="off_menu")
    count = res.data.get("count", len(items))
    first = items[0]
    return Step(
        say=[
            f"There are {count} overdue {label} items. The first is {spell(str(first.get('subject_ref', '')))}, "
            f"{first.get('days_overdue', 'some')} days overdue. Say remind to send a reminder for it."
        ],
        goto="off_menu",
    )


# -- emailed report ---------------------------------------------------------------------------------
@node("off_report", "Which report: pendency or overdue?", hints="pendency, overdue")
async def off_report(call: CallState, inp: Input) -> Step:
    report = _report_in(inp.text)
    if report is None:
        return await unclear(call, "off_report", summary="Report type was not understood.")
    return await _send_report(call, report)


async def _send_report(call: CallState, report: str) -> Step:
    call.clear("off_report")
    res = await call.tool("email_report", {"report_type": report, "filters": {}})
    if not res.ok:
        return await tool_failed(call, res)
    call.slots["served"] = True
    return Step(say=[f"I have emailed the {report} report to your registered address."], goto="off_menu")


# -- reminder (always confirmed) ---------------------------------------------------------------------
async def _start_reminder(call: CallState) -> Step:
    items = call.slots.get("last_overdue") or []
    if not items:
        return Step(say=["Ask for an overdue list first, then I can send a reminder for its first item."], goto="off_menu")
    call.slots["remind_ref"] = str(items[0].get("subject_ref", ""))
    return Step(goto="off_remind_confirm")


def _remind_prompt(call: CallState) -> str:
    return (
        f"I will send the institution a reminder about {spell(call.slots.get('remind_ref', ''))}. "
        "Say yes to send it, or no to cancel."
    )


@node("off_remind_confirm", _remind_prompt, kind=Kind.BOTH, num_digits=1)
async def off_remind_confirm(call: CallState, inp: Input) -> Step:
    if is_negative(inp.any):
        call.clear("remind_ref")
        return Step(say=["Okay, no reminder sent."], goto="off_menu")
    if not is_affirmative(inp.any) and inp.digits != "1":
        return await unclear(call, "off_remind_confirm", summary="Reminder was not confirmed.")
    res = await call.tool(
        "trigger_reminder", {"subject_ref": call.slots["remind_ref"], "confirmed": True}, confirmed=True
    )
    if not res.ok:
        return await tool_failed(call, res)
    call.clear("remind_ref", "off_remind_confirm")
    call.slots["served"] = True
    return Step(say=["The reminder has been sent."], goto="off_menu")

