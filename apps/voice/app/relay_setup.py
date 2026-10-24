"""The first message of a ConversationRelay WebSocket: `setup`.

Twilio opens the socket and sends `setup` with the signed session token we put
in a <Parameter>. That token is the only authentication of the socket, so the
connection is closed unless it is present, valid, unexpired and for this call.
Shared by the LLM relay (`/voice/relay`) and the live graph (`/voice/live`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass

import jwt
from fastapi import WebSocket, WebSocketDisconnect

from app.config import Settings
from app.obs import get_logger
from app.session import SessionClaims, verify_session

log = get_logger(__name__)

SETUP_TIMEOUT_SEC = 10.0
WS_UNAUTHORIZED = 4401
WS_MISMATCH = 4403


@dataclass(frozen=True, slots=True)
class Setup:
    claims: SessionClaims
    token: str  # the raw session JWT (forwarded to core as X-Voice-Session)


async def close_quietly(ws: WebSocket, code: int) -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await ws.close(code=code)


async def read_setup(ws: WebSocket, settings: Settings) -> Setup | None:
    """Wait for and authenticate `setup`. On any problem the socket is closed and None returned."""
    try:
        msg = json.loads(await asyncio.wait_for(ws.receive_text(), SETUP_TIMEOUT_SEC))
    except (TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        await close_quietly(ws, WS_UNAUTHORIZED)
        return None
    if msg.get("type") != "setup":
        await close_quietly(ws, WS_UNAUTHORIZED)
        return None
    token = str((msg.get("customParameters") or {}).get("session", ""))
    try:
        claims = verify_session(token, settings)
    except (jwt.InvalidTokenError, KeyError, ValueError):
        log.warning("relay_bad_session")
        await close_quietly(ws, WS_UNAUTHORIZED)
        return None
    if claims.call_sid != msg.get("callSid"):
        log.warning("relay_callsid_mismatch")
        await close_quietly(ws, WS_MISMATCH)
        return None
    return Setup(claims=claims, token=token)
