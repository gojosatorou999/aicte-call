"""Admin dashboard: every call, what was asked and what came of it, with CSV export.

    GET /admin               the dashboard page
    GET /admin/api/calls     the rows (JSON) and the summary counters
    GET /admin/api/calls/<id> one call with its full transcript
    GET /admin/export.csv    all calls as a CSV download

Everything is behind HTTP Basic auth (user ADMIN_USER, password ADMIN_PASSWORD). With no
ADMIN_PASSWORD set the dashboard is switched off rather than left open.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hmac
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import clock
from app.calllog import COLUMNS, CallLog
from app.config import get_settings
from app.dial import SlidingWindow

router = APIRouter(prefix="/admin", tags=["admin"])
_PAGE = (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")
_failed_logins = SlidingWindow(limit=10, window=60)  # wrong passwords per address per minute
_LIST_COLUMNS = [(k, h) for k, h in COLUMNS if k != "transcript"]  # the list stays light


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    s = get_settings()
    password = s.admin_password.get_secret_value()
    if not password:
        raise HTTPException(
            status_code=503,
            detail="The admin dashboard is switched off. Set ADMIN_PASSWORD in apps/voice/.env.",
        )
    ip = _client_ip(request)
    if _failed_logins.over(ip):
        raise HTTPException(
            status_code=429, detail="Too many wrong passwords. Try again in a minute."
        )
    user, given = "", ""
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() == "basic":
        with contextlib.suppress(binascii.Error, UnicodeDecodeError):
            user, _, given = base64.b64decode(encoded).decode("utf-8").partition(":")
    ok = hmac.compare_digest(user.encode(), s.admin_user.encode()) & hmac.compare_digest(
        given.encode(), password.encode()
    )
    if not ok:
        _failed_logins.allow(ip)  # records the failure
        raise HTTPException(
            status_code=401,
            detail="Sign in to see the call reports.",
            headers={"WWW-Authenticate": 'Basic realm="AICTE voice admin", charset="UTF-8"'},
        )


def _log(request: Request) -> CallLog:
    return request.app.state.calllog


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})


@router.get("/api/calls", dependencies=[Depends(require_admin)])
async def list_calls(request: Request, limit: int = 500) -> JSONResponse:
    log = _log(request)
    rows = [
        {k: r.get(k) for k, _ in _LIST_COLUMNS} for r in log.rows(limit=max(1, min(limit, 5000)))
    ]
    return JSONResponse(
        {
            "summary": log.summary(),
            "columns": [{"key": k, "heading": h} for k, h in _LIST_COLUMNS],
            "calls": rows,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/calls/{call_id}", dependencies=[Depends(require_admin)])
async def one_call(request: Request, call_id: str) -> JSONResponse:
    row = _log(request).get(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such call.")
    return JSONResponse(row, headers={"Cache-Control": "no-store"})


@router.get("/export.csv", dependencies=[Depends(require_admin)])
async def export_csv(request: Request) -> Response:
    stamp = clock.now().strftime("%Y%m%d-%H%M")
    return Response(
        "﻿" + _log(request).csv_text(),  # BOM so Excel reads the UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="calls-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )
