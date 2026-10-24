"""Per-persona call context: outbound call purposes and their scripts (app.md 12.3).

The persona's standing instructions live in `prompts/{persona}.md`; what
changes per call (why we are calling, which item, which dates) is built here
and appended to the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class CallPurpose(StrEnum):
    DEFICIENCY_PENDING = "DEFICIENCY_PENDING"
    UNDERTAKING_DUE = "UNDERTAKING_DUE"
    UNDERTAKING_OVERDUE = "UNDERTAKING_OVERDUE"
    DOC_EXPIRY_REMINDER = "DOC_EXPIRY_REMINDER"
    DOC_EXPIRED = "DOC_EXPIRED"
    EVC_VISIT_CONFIRM = "EVC_VISIT_CONFIRM"
    BROKEN_COMMITMENT = "BROKEN_COMMITMENT"


@dataclass(frozen=True, slots=True)
class OutboundJob:
    """What core tells us about an outbound call (POST /voice/calls context)."""

    job_id: str
    purpose: CallPurpose
    institution_name: str
    contact_name: str
    item_title: str
    subject_ref: str  # the id to pass to record_commitment / send_upload_link ...
    due_date: date | None = None  # due / expiry / visit date, depending on purpose
    committed_date: date | None = None  # BROKEN_COMMITMENT: the date they promised


_COMMON = (
    "Follow the standard outbound script: confirm you are speaking with the named contact (if not, "
    "ask for the nodal officer or a good time and use schedule_callback), state the item and date "
    "factually, ask by when it will be done, confirm that date back, then record it. Offer the "
    "upload link by WhatsApp, SMS or email when a document is needed. Close by repeating the "
    "commitment and next step."
)

_SCRIPTS: dict[CallPurpose, str] = {
    CallPurpose.DEFICIENCY_PENDING: (
        "A deficiency raised on the institution's application has not been answered. Ask when the "
        "response and supporting documents will be submitted. " + _COMMON
    ),
    CallPurpose.UNDERTAKING_DUE: (
        "An undertaking given as a condition of approval falls due on the date below. Remind them "
        "and ask whether it will be met on time; if not, ask by when. " + _COMMON
    ),
    CallPurpose.UNDERTAKING_OVERDUE: (
        "An undertaking is overdue. State this plainly and without threats; do not mention penalties "
        "or outcomes. Ask for the reason and a committed date. " + _COMMON
    ),
    CallPurpose.DOC_EXPIRY_REMINDER: (
        "A document on file will expire on the date below. Ask by when the renewed document will be "
        "uploaded and offer the upload link. " + _COMMON
    ),
    CallPurpose.DOC_EXPIRED: (
        "A document on file has expired. State this factually, ask by when the renewed document will "
        "be uploaded and offer the upload link. " + _COMMON
    ),
    CallPurpose.EVC_VISIT_CONFIRM: (
        "An expert visiting committee visit is scheduled for the date below. Ask the contact to "
        "confirm the date and that the campus will be ready. This call is not about a deadline: do "
        "NOT call record_commitment. If they cannot receive the visit on that date, note the reason "
        "and use handoff_to_officer so an officer can reschedule. Do not promise a new date."
    ),
    CallPurpose.BROKEN_COMMITMENT: (
        "The institution earlier committed to the committed date below but the item has not been "
        "completed. Ask what happened without blame, then for a new date within the policy limit; if "
        "they need longer, log an extension request for officer review instead. " + _COMMON
    ),
}


def outbound_context(job: OutboundJob) -> str:
    lines = [
        "THIS IS AN OUTBOUND CALL. Call details:",
        f"- Purpose: {job.purpose.value}",
        f"- Institution: {job.institution_name}",
        f"- Person to speak to: {job.contact_name}",
        f"- Item: {job.item_title}",
        f"- subject_ref for tool calls: {job.subject_ref}",
    ]
    if job.due_date:
        lines.append(f"- Relevant date (due / expiry / visit): {job.due_date.isoformat()}")
    if job.committed_date:
        lines.append(f"- Earlier committed date: {job.committed_date.isoformat()}")
    return "\n".join(lines) + "\n\n" + _SCRIPTS[job.purpose]
