"""Keypad PIN / OTP verification, run by the relay outside the LLM.

Digits arrive as ConversationRelay `dtmf` messages. They are held in memory
only until submitted, never logged, never added to the transcript and never
shown to the model (CLAUDE.md-style rule: sensitive nodes do not store what
the caller entered).
"""

from __future__ import annotations

from enum import StrEnum

from app.agent.policy import Verification
from app.core_client import CoreClient, CoreError, CoreUnavailable
from app.obs import get_logger

log = get_logger(__name__)

CODE_LENGTH = 6


class Outcome(StrEnum):
    NEED_MORE = "NEED_MORE"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"  # wrong code, attempts remain
    LOCKED = "LOCKED"  # out of attempts
    ERROR = "ERROR"  # core unavailable: not the caller's fault, not counted


class Verifier:
    def __init__(
        self, *, kind: Verification, core: CoreClient, session_jwt: str, max_attempts: int
    ) -> None:
        assert kind != Verification.NONE
        self.kind = kind
        self._core = core
        self._jwt = session_jwt
        self._max = max_attempts
        self._digits = ""
        self.attempts = 0

    async def begin(self) -> bool:
        """OTP needs a code sent first. PIN needs nothing. False = could not start."""
        if self.kind != Verification.OTP:
            return True
        try:
            await self._core.post("/otp/send", session_jwt=self._jwt, json={})
        except CoreError as exc:
            log.warning("otp_send_failed", status=exc.status)
            return False
        return True

    async def feed(self, digit: str) -> Outcome:
        if digit == "*":  # start over
            self._digits = ""
            return Outcome.NEED_MORE
        if digit.isdigit():
            self._digits += digit
        elif digit != "#":
            return Outcome.NEED_MORE
        if digit == "#" and not self._digits:
            return Outcome.NEED_MORE
        if digit != "#" and len(self._digits) < CODE_LENGTH:
            return Outcome.NEED_MORE
        return await self._submit()

    async def _submit(self) -> Outcome:
        code, self._digits = self._digits, ""
        path, body = (
            ("/officers/verify-pin", {"pin": code})
            if self.kind == Verification.PIN
            else ("/otp/check", {"code": code})
        )
        try:
            data = await self._core.post(path, session_jwt=self._jwt, json=body)
            ok = isinstance(data, dict) and data.get("verified") is True
        except CoreUnavailable:
            return Outcome.ERROR
        except CoreError:  # 4xx: rejected
            ok = False
        if ok:
            log.info("verification_ok", kind=self.kind.value)
            return Outcome.VERIFIED
        self.attempts += 1
        log.warning("verification_failed", kind=self.kind.value, attempts=self.attempts)
        return Outcome.LOCKED if self.attempts >= self._max else Outcome.FAILED
