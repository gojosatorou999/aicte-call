"""The dial pad's API: POST /dial/call asks Twilio to ring the person's phone.

Same contract as the earlier Sentinel dial pad (form field `phone`, answer
`{"call_sid", "status"}` or `{"detail"}`), plus `scenario` / `code` fields and
two read-only endpoints the page uses to show progress.
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Form, Header, Request
from fastapi.responses import JSONResponse

from app import dial
from app.config import get_settings
from app.obs import get_logger
from app.utils.hashing import mask_msisdn

router = APIRouter(prefix="/dial", tags=["dial"])
log = get_logger(__name__)
POLL_EVERY_SEC = 1.5


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _client_ip(request: Request) -> str:
    # Behind ngrok / a load balancer the real address is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@router.get("/config")
async def dial_config() -> dict[str, object]:
    """What the page should offer: an access-code box, scenario choices."""
    s = get_settings()
    return {
        "enabled": s.dial_pad_enabled,
        "code_required": bool(s.dial_pad_token.get_secret_value()),
        "scenarios": dial.SCENARIOS if s.demo_scenarios else {},
        "allowed_prefixes": s.dial_prefixes,
    }


@router.post("/call")
async def initiate_call(
    request: Request,
    phone: str = Form(...),
    scenario: str = Form("public"),
    code: str = Form(""),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    s = get_settings()
    if not s.dial_pad_enabled:
        return _error(404, "The dial pad is turned off.")

    required = s.dial_pad_token.get_secret_value()
    if required:
        offered = (authorization or "").removeprefix("Bearer ").strip() or code.strip()
        if not hmac.compare_digest(offered.encode(), required.encode()):
            return _error(401, "That access code is not right.")

    dialer: dial.Dialer = request.app.state.dialer
    if (missing := dialer.configured()) is not None:  # before counting this against the limits
        return _error(503, missing)

    try:
        number = dial.normalize_number(phone)
        if scenario not in dial.SCENARIOS:
            raise dial.DialError(400, "Unknown scenario.")
        if scenario != "public" and not s.demo_scenarios:
            raise dial.DialError(403, "Demo scenarios are not enabled on this server.")
        request.app.state.dial_guard.check(number, _client_ip(request))

        store: dial.DialStore = request.app.state.dials
        record = store.create(scenario, mask_msisdn(number))
        sid, status = await dialer.place(
            to=number,
            url=f"{s.public_base}/voice/outbound-twiml?d={record.dial_id}",
            status_callback=f"{s.public_base}/voice/status",
        )
    except dial.DialError as exc:
        return _error(exc.status, exc.detail)

    store.attach_sid(record, sid, status)
    log.info("dial_call_placed", call_sid=sid, to=record.to_masked, scenario=scenario)
    return JSONResponse(
        {"call_sid": sid, "status": status, "message": "Calling you now. Please answer your phone."}
    )


@router.get("/status/{call_sid}")
async def dial_status(request: Request, call_sid: str) -> JSONResponse:
    """queued -> ringing -> in-progress -> completed (or busy / no-answer / failed)."""
    store: dial.DialStore = request.app.state.dials
    record = store.by_sid(call_sid)
    if record is None:
        return _error(404, "Unknown call.")
    dialer: dial.Dialer = request.app.state.dialer
    # Accounts that cannot use status callbacks get progress by asking Twilio (at most every 1.5 s).
    if (
        dialer.polls_status
        and record.status not in dial.TERMINAL_STATUSES
        and time.monotonic() - record.checked >= POLL_EVERY_SEC
    ):
        record.checked = time.monotonic()
        if (fresh := await dialer.fetch_status(call_sid)) is not None:
            store.update_status(call_sid, fresh)
    return JSONResponse({"call_sid": call_sid, "status": record.status})
