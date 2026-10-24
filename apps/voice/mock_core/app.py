"""Hand-written stand-in for core's `/internal/voice/*` (app.md 13.13) until
Track P ships the OpenAPI export (P8). Run: `uvicorn mock_core.app:app --port 9000`.

Responses are canned from seeded data and only as realistic as the voice
tests need. It does check the bearer token, like the real thing."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN", "change-me-voice-to-core")

INSTITUTION = {
    "id": "inst-001",
    "name": "Sri Venkateswara Institute of Technology",
    "city": "Anantapur",
    "state": "Andhra Pradesh",
}
INSTITUTION_2 = {
    "id": "inst-002",
    "name": "Sri Sai College of Engineering",
    "city": "Chennai",
    "state": "Tamil Nadu",
}
INSTITUTIONS = [INSTITUTION, INSTITUTION_2]


def _auth(authorization: str = Header(default="")) -> None:
    if authorization != f"Bearer {_TOKEN}":
        raise HTTPException(401, detail={"error": {"code": "UNAUTHORIZED", "params": {}}})


app = FastAPI(title="ACP core (voice mock)", dependencies=[Depends(_auth)])
P = "/internal/voice"


@app.get(f"{P}/callers/lookup")
def lookup(phone: str) -> dict[str, Any]:
    if phone.endswith("0001"):
        return {
            "kind": "INSTITUTION",
            "institution_id": INSTITUTION["id"],
            "contact_name": "Ramesh Kumar",
            "preferred_language": "te",
        }
    if phone.endswith("0002"):
        return {"kind": "OFFICER", "officer_id": "off-001", "preferred_language": "en"}
    if phone.endswith("0003"):
        return {"kind": "FACULTY", "faculty_id": "fac-001", "preferred_language": "kn"}
    return {"kind": "UNKNOWN"}


@app.get(f"{P}/institutions/me/open-items")
def open_items() -> dict[str, Any]:
    return {
        "items": [
            {
                "subject_ref": "UND-2026-0042",
                "title": "Lease registration",
                "due_date": "2026-11-05",
                "overdue": False,
            }
        ]
    }


@app.get(f"{P}/cases/{{app_id}}/status")
def case_status(app_id: str) -> dict[str, Any]:
    return {"app_id": app_id, "stage": "SCRUTINY", "status": "IN_PROGRESS"}


@app.post(f"{P}/commitments")
def commitment(body: dict[str, Any]) -> dict[str, Any]:
    return {"id": "cmt-001", **body}


@app.post(f"{P}/extension-requests")
def extension(body: dict[str, Any]) -> dict[str, Any]:
    return {"id": "ext-001", "status": "PENDING_OFFICER_REVIEW", **body}


@app.post(f"{P}/callbacks")
def callback(body: dict[str, Any]) -> dict[str, Any]:
    return {"id": "cb-001", **body}


@app.get(f"{P}/public/institutions")
def search_institutions(name: str, state: str | None = None) -> dict[str, Any]:
    return {"results": [i for i in INSTITUTIONS if name.lower() in i["name"].lower()]}


@app.get(f"{P}/public/institutions/{{institution_id}}/approval")
def approval(institution_id: str, academic_year: str | None = None) -> dict[str, Any]:
    if institution_id not in {i["id"] for i in INSTITUTIONS}:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "params": {}}})
    return {
        "institution_id": institution_id,
        "academic_year": academic_year or "2026-27",
        "approved": True,
        "courses": [{"name": "B.Tech CSE", "intake": 120}, {"name": "B.Tech ECE", "intake": 60}],
    }


@app.post(f"{P}/grievances")
def grievance(body: dict[str, Any]) -> dict[str, Any]:
    return {"ticket_id": "GRV-2026-0001", **body}


@app.post(f"{P}/calls")
def call_start(body: dict[str, Any]) -> dict[str, Any]:
    return {"call_id": "call-001", **body}


@app.post(f"{P}/calls/{{call_id}}/status")
def call_status(call_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"call_id": call_id, "ok": True}


@app.post(f"{P}/calls/{{call_id}}/outcome")
def call_outcome(call_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"call_id": call_id, "ok": True}


# -- officer / faculty / shared endpoints (V3) --------------------------------
VALID_PIN = "246810"
VALID_OTP = "123456"  # DEMO_FIXED_OTP


@app.post(f"{P}/officers/verify-pin")
def verify_pin(body: dict[str, Any]) -> dict[str, Any]:
    return {"verified": body.get("pin") == VALID_PIN}


@app.post(f"{P}/otp/send")
def otp_send() -> dict[str, Any]:
    return {"sent": True}


@app.post(f"{P}/otp/check")
def otp_check(body: dict[str, Any]) -> dict[str, Any]:
    return {"verified": body.get("code") == VALID_OTP}


@app.get(f"{P}/officers/me/queue-summary")
def queue_summary() -> dict[str, Any]:
    return {"pending": 14, "overdue": 3, "due_this_week": 5}


@app.get(f"{P}/stats/region")
def region_stats(region_code: str | None = None) -> dict[str, Any]:
    return {"region_code": region_code or "SR", "applications_in_progress": 120, "overdue": 9}


@app.get(f"{P}/overdue")
def overdue(kind: str = "STAGE", limit: int = 5) -> dict[str, Any]:
    items = [
        {"subject_ref": f"APP-2026-{400 + i:04d}", "kind": kind, "days_overdue": 3 + i}
        for i in range(limit)
    ]
    return {"count": 3, "items": items[:3]}


@app.post(f"{P}/reports/email")
def email_report(body: dict[str, Any]) -> dict[str, Any]:
    return {"queued": True, **body}


@app.post(f"{P}/reminders")
def reminder(body: dict[str, Any]) -> dict[str, Any]:
    return {"sent": bool(body.get("confirmed")), "subject_ref": body.get("subject_ref")}


@app.post(f"{P}/aph/search")
def aph_search(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "results": [
            {"section": "4.2", "text": "Land documents must be valid for at least 30 years."}
        ]
    }


@app.post(f"{P}/upload-links")
def upload_link(body: dict[str, Any]) -> dict[str, Any]:
    return {"sent": True, "channel": body.get("channel")}


@app.get(f"{P}/faculty/{{faculty_id}}")
def faculty(faculty_id: str) -> dict[str, Any]:
    return {
        "faculty_id": faculty_id,
        "registration_status": "ACTIVE",
        "institutions": ["Sri Venkateswara Institute of Technology"],
        "duplicate_flag": True,
        "resolution_steps": "Upload a no-objection letter from the previous institution.",
    }
