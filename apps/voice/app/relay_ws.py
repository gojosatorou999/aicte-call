"""ConversationRelay WebSocket handler (app.md 12.1, 20 V2).

Twilio does the speech-to-text and text-to-speech; this socket carries
transcribed caller text in and the assistant's text out. Protocol message
shapes: https://www.twilio.com/docs/voice/conversationrelay/websocket-messages

Inbound : setup, prompt, interrupt, dtmf, error
Outbound: text, end
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import clock
from app.agent import guardrails
from app.agent.brain import Brain, BrainError
from app.agent.llm import ChatStream
from app.agent.personas import OutboundJob, outbound_context
from app.agent.policy import GatedToolRunner, SessionPolicy, Verification
from app.agent.tools import ToolRunner
from app.agent.verification import Outcome, Verifier
from app.config import Settings, get_settings
from app.core_client import CoreClient
from app.obs import get_logger
from app.session import Persona, SessionClaims, verify_session
from app.speech.intent import has_any
from app.speech.phrases import phrase
from app.utils.redact import redact_transcript
from app.utils.transcript import TranscriptLine

router = APIRouter()
log = get_logger(__name__)

SETUP_TIMEOUT_SEC = 10.0
SPEECH_CHARS_PER_SEC = 12.0  # rough TTS pace, used to time silence and goodbye
MAX_GOODBYE_WAIT_SEC = 15.0
END_PADDING_SEC = 1.0  # extra grace after the estimated speech time before ending
WS_UNAUTHORIZED = 4401
WS_MISMATCH = 4403
RETURN_TO_MENU = ("main menu", "go back", "back to menu", "that is all", "thats all", "no thanks")


@dataclass
class RelaySession:
    ws: WebSocket
    core: CoreClient
    llm: ChatStream | None
    settings: Settings
    jobs: dict[str, OutboundJob] = field(default_factory=dict)
    claims: SessionClaims | None = None
    brain: Brain | None = None
    policy: SessionPolicy | None = None
    verifier: Verifier | None = None
    transcript: list[TranscriptLine] = field(default_factory=list)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_task: asyncio.Task[None] | None = None
    _silence_task: asyncio.Task[None] | None = None
    _deadline_task: asyncio.Task[None] | None = None
    _closing: bool = False
    _reprompts: int = 0
    _abuse_strikes: int = 0
    _last_reply_chars: int = 0

    # -- lifecycle -------------------------------------------------------------
    async def run(self) -> None:
        try:
            if not await self._handle_setup():
                return
            while True:
                msg = json.loads(await self.ws.receive_text())
                await self._dispatch(msg)
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            await self._cleanup()

    async def _handle_setup(self) -> bool:
        try:
            raw = await asyncio.wait_for(self.ws.receive_text(), SETUP_TIMEOUT_SEC)
            msg = json.loads(raw)
        except (TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
            await self._close(WS_UNAUTHORIZED)
            return False
        if msg.get("type") != "setup":
            await self._close(WS_UNAUTHORIZED)
            return False
        try:
            token = (msg.get("customParameters") or {}).get("session", "")
            self.claims = verify_session(token, self.settings)
        except (jwt.InvalidTokenError, KeyError, ValueError):
            log.warning("relay_bad_session")
            await self._close(WS_UNAUTHORIZED)
            return False
        if self.claims.call_sid != msg.get("callSid"):
            log.warning("relay_callsid_mismatch")
            await self._close(WS_MISMATCH)
            return False

        c = self.claims
        structlog.contextvars.bind_contextvars(
            call_sid=c.call_sid, persona=c.persona.value, language=c.language
        )
        log.info("relay_connected")
        self._note("assistant", phrase("welcome", c.language))  # spoken by Twilio (welcomeGreeting)

        if self.llm is None:
            log.error("llm_not_configured")
            await self._say(phrase("technical_problem", c.language), end=True)
            return True
        token = token_of(msg)
        self.policy = SessionPolicy(c, self.settings)
        job = self.jobs.get(c.job_id) if c.job_id else None
        self.brain = Brain(
            llm=self.llm,
            runner=GatedToolRunner(ToolRunner(self.core, token, c.persona), self.policy),
            persona=c.persona,
            language=c.language,
            settings=self.settings,
            extra_context=outbound_context(job) if job else "",
        )
        self._deadline_task = asyncio.create_task(self._enforce_deadline())

        kind = self.policy.verification_needed()  # NONE when the graph already verified
        if kind != Verification.NONE:
            self.verifier = Verifier(
                kind=kind,
                core=self.core,
                session_jwt=token,
                max_attempts=(
                    self.settings.pin_max_attempts
                    if kind == Verification.PIN
                    else self.settings.otp_max_attempts
                ),
            )
            if not await self.verifier.begin():
                self.verifier = None
                await self._say(phrase("technical_problem", c.language), end=True)
                return True
            await self._say(self._verify_prompt())
            self._arm_silence(len(self._verify_prompt()))
            return True
        self._arm_silence(len(phrase("welcome", c.language)))
        return True

    def _verify_prompt(self) -> str:
        assert self.claims is not None and self.verifier is not None
        key = "pin_prompt" if self.verifier.kind == Verification.PIN else "otp_prompt"
        return phrase(key, self.claims.language)

    async def _cleanup(self) -> None:
        self._closing = True
        for task in (self._turn_task, self._silence_task, self._deadline_task):
            if task and not task.done():
                task.cancel()
        for task in (self._turn_task, self._silence_task, self._deadline_task):
            if task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        log.info("relay_closed", lines=len(self.transcript))

    # -- inbound messages ------------------------------------------------------
    async def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "prompt":
            if msg.get("last", True) and (text := str(msg.get("voicePrompt", "")).strip()):
                await self._on_prompt(text)
        elif kind == "interrupt":
            await self._on_interrupt(str(msg.get("utteranceUntilInterrupt", "")))
        elif kind == "dtmf":
            await self._on_dtmf(str(msg.get("digit", "")))
        elif kind == "error":
            log.error("relay_twilio_error", description=str(msg.get("description", ""))[:200])
        elif kind != "setup":
            log.warning("relay_unknown_message", type=str(kind)[:30])

    async def _on_dtmf(self, digit: str) -> None:
        self._cancel_silence()
        self._reprompts = 0
        if self.verifier is None:
            log.info("relay_dtmf_ignored")  # a digit may belong to a PIN/OTP: never logged
            return
        assert self.claims is not None and self.brain is not None and self.policy is not None
        lang = self.claims.language
        outcome = await self.verifier.feed(digit)
        if outcome == Outcome.NEED_MORE:
            self._arm_silence(0)
        elif outcome == Outcome.VERIFIED:
            self.verifier = None
            self.policy.mark_verified()
            self.brain.add_context(
                "The caller has just been verified by the system with their keypad code. Do not "
                "ask for a PIN or code."
            )
            await self._say(phrase("verified_ok", lang))
            self._arm_silence(self._last_reply_chars)
        elif outcome == Outcome.FAILED:
            await self._say(phrase("verify_failed", lang) + " " + self._verify_prompt())
            self._arm_silence(self._last_reply_chars)
        elif outcome == Outcome.LOCKED:
            await self._say(phrase("verify_locked", lang), end=True)
        else:  # core unavailable
            await self._say(phrase("technical_problem", lang), end=True)

    async def _on_prompt(self, text: str) -> None:
        self._cancel_silence()
        self._reprompts = 0
        await self._cancel_turn()
        if self.verifier is not None:
            # Awaiting keypad entry: what the caller said is not used (it could be
            # the PIN itself), not stored, and not sent to the model.
            assert self.claims is not None
            await self._say(
                phrase("use_keypad", self.claims.language) + " " + self._verify_prompt()
            )
            self._arm_silence(self._last_reply_chars)
            return
        self._note("caller", text)
        self._turn_task = asyncio.create_task(self._turn(text))

    async def _on_interrupt(self, spoken: str) -> None:
        self._cancel_silence()
        await self._cancel_turn()
        if self.brain:
            self.brain.note_interrupted(spoken)
        if spoken.strip() and self.transcript and self.transcript[-1].role == "assistant":
            self.transcript[-1].text = redact_transcript(spoken)

    async def _cancel_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- one assistant turn ----------------------------------------------------
    async def _turn(self, text: str) -> None:
        assert self.brain is not None and self.claims is not None
        lang = self.claims.language

        if guardrails.is_abusive(text):
            self._abuse_strikes += 1
            if self._abuse_strikes == 1:
                await self._say(phrase("abuse_warning", lang))
                self._arm_silence(self._last_reply_chars)
            else:
                await self._say(phrase("goodbye_abuse", lang), end=True)
            return
        if self.claims.via_graph and has_any(text, *RETURN_TO_MENU):
            await self._say(
                "Okay, going back to the menu.", end=True
            )  # Twilio then resumes the graph
            return
        if self.brain.turn_count >= self.settings.max_turns:
            await self._say(phrase("goodbye_limit", lang), end=True)
            return

        spoken: list[str] = []
        try:
            async for sentence in self.brain.respond(text):
                spoken.append(sentence)
                await self._send({"type": "text", "token": sentence + " ", "last": False})
        except BrainError:
            await self._say(phrase("technical_problem", lang), end=True)
            return
        # Close the utterance so Twilio starts synthesis of any buffered text.
        await self._send({"type": "text", "token": "", "last": True})
        reply = " ".join(spoken)
        self._note("assistant", reply)
        self._last_reply_chars = len(reply)
        self._arm_silence(len(reply))

    # -- outbound helpers --------------------------------------------------------
    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closing:
            return
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _say(self, text: str, *, end: bool = False) -> None:
        self._note("assistant", text)
        self._last_reply_chars = len(text)
        await self._send({"type": "text", "token": text, "last": True})
        if end:
            await self._end_after(len(text))

    async def _end_after(self, spoken_chars: int) -> None:
        """Let the farewell finish playing, then end the relay session.

        Twilio gives no "finished speaking" signal without extra `events`
        configuration, so the wait is estimated from the text length."""
        await asyncio.sleep(
            min(spoken_chars / SPEECH_CHARS_PER_SEC + END_PADDING_SEC, MAX_GOODBYE_WAIT_SEC)
        )
        await self._send({"type": "end"})
        self._closing = True

    def _note(self, role: str, text: str) -> None:
        if text.strip():
            self.transcript.append(
                TranscriptLine(role, redact_transcript(text), clock.now().isoformat())
            )

    # -- silence and call limits --------------------------------------------------
    def _cancel_silence(self) -> None:
        task, self._silence_task = self._silence_task, None
        if task and not task.done():
            task.cancel()

    def _arm_silence(self, spoken_chars: int) -> None:
        """Start the no-input timer once the assistant should have finished speaking."""
        self._cancel_silence()
        wait = spoken_chars / SPEECH_CHARS_PER_SEC + self.settings.no_input_timeout_ms / 1000
        self._silence_task = asyncio.create_task(self._on_silence(wait))

    async def _on_silence(self, wait: float) -> None:
        assert self.claims is not None
        await asyncio.sleep(wait)
        lang = self.claims.language
        if self._reprompts >= self.settings.silence_reprompts:
            await self._say(phrase("goodbye_silence", lang), end=True)
            return
        self._reprompts += 1
        text = self._verify_prompt() if self.verifier is not None else phrase("reprompt", lang)
        await self._say(text)
        self._silence_task = asyncio.create_task(
            self._on_silence(
                len(text) / SPEECH_CHARS_PER_SEC + self.settings.no_input_timeout_ms / 1000
            )
        )

    async def _enforce_deadline(self) -> None:
        assert self.claims is not None
        limit = (
            self.settings.max_call_duration_public_sec
            if self.claims.persona == Persona.PUBLIC
            else self.settings.max_call_duration_default_sec
        )
        started = time.monotonic()
        await asyncio.sleep(limit)
        log.info("call_time_limit", seconds=round(time.monotonic() - started))
        await self._cancel_turn()
        self._cancel_silence()
        await self._say(phrase("goodbye_limit", self.claims.language), end=True)

    async def _close(self, code: int) -> None:
        self._closing = True
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await self.ws.close(code=code)


def token_of(setup_msg: dict[str, Any]) -> str:
    return str((setup_msg.get("customParameters") or {}).get("session", ""))


@router.websocket("/voice/relay")
async def relay(ws: WebSocket) -> None:
    await ws.accept()
    app_state = ws.app.state
    session = RelaySession(
        ws=ws,
        core=app_state.core,
        llm=getattr(app_state, "llm", None),
        settings=get_settings(),
        jobs=getattr(app_state, "jobs", {}),
    )
    try:
        await session.run()
    finally:
        structlog.contextvars.unbind_contextvars("call_sid", "persona", "language")
