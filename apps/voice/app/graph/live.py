"""The conversation graph over a ConversationRelay WebSocket ("live" mode).

Same graph, same rules, same tools as the Gather path - but Twilio streams the
caller's speech to text (so there is no wait for a <Gather> to time out) and
speaks our replies with a neural voice, with barge-in. No LLM is involved: the
text we send back comes from the graph's fixed prompts.

  Twilio -> us:  setup | prompt (caller's words) | dtmf (one key) | interrupt | error
  us -> Twilio:  text (what to say) | end

Per-turn latency (our share of it) is logged as `live_turn` with the node name only;
what the caller said is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import relay_ws
from app.agent.personas import OutboundJob
from app.calllog import CallLog
from app.config import Settings, get_settings
from app.core_client import CoreClient
from app.dial import DialStore
from app.graph.engine import REGISTRY, Input, Kind, Step, run_turn
from app.graph.render import spoken_lines
from app.graph.start import create_call
from app.graph.state import CallState, StateStore
from app.obs import get_logger
from app.relay_setup import read_setup
from app.speech.speakable import speakable

router = APIRouter()
log = get_logger(__name__)

KEYPAD_REMINDER = "For your security, please use your phone's keypad for this."


@dataclass
class LiveCall:
    ws: WebSocket
    core: CoreClient
    settings: Settings
    store: StateStore
    jobs: dict[str, OutboundJob]
    calllog: CallLog | None = None
    dials: DialStore | None = None  # lets the dial pad learn that the voice really connected
    call: CallState | None = None
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _silence_task: asyncio.Task[None] | None = None
    _digits: str = ""
    _done: bool = False

    # -- lifecycle -------------------------------------------------------------------
    async def run(self) -> None:
        setup = await read_setup(self.ws, self.settings)
        if setup is None:
            return
        claims = setup.claims
        if self.dials and (record := self.dials.by_sid(claims.call_sid)):
            record.ws_connected = True
        structlog.contextvars.bind_contextvars(
            call_sid=claims.call_sid, persona=claims.persona.value, mode="live"
        )
        job = self.jobs.get(claims.job_id) if claims.job_id else None
        # Twilio's welcomeGreeting has already greeted the caller, so no greeting here.
        self.call, first = await create_call(
            claims=claims,
            token=setup.token,
            core=self.core,
            store=self.store,
            settings=self.settings,
            job=job,
            greeting=None,
        )
        log.info("live_connected")
        if self.calllog:
            self.calllog.upsert(self.call)
        try:
            await self._emit(first)
            while not self._done:
                await self._dispatch(json.loads(await self.ws.receive_text()))
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            self._cancel_silence()
            self.store.drop(claims.call_sid)
            log.info("live_closed")
            structlog.contextvars.unbind_contextvars("call_sid", "persona", "mode")

    # -- inbound ---------------------------------------------------------------------------
    async def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "prompt":
            text = str(msg.get("voicePrompt", "")).strip()
            if msg.get("last", True) and text:
                await self._on_speech(text)
        elif kind == "dtmf":
            await self._on_digit(str(msg.get("digit", "")))
        elif kind == "interrupt":
            self._cancel_silence()  # the caller is talking
        elif kind == "error":
            log.error("live_twilio_error", description=str(msg.get("description", ""))[:200])

    async def _on_speech(self, text: str) -> None:
        assert self.call is not None
        self._cancel_silence()
        node = REGISTRY[self.call.node]
        async with self._turn_lock:
            if node.sensitive or node.kind == Kind.DTMF:
                # A PIN/OTP node: what was said is not used, not stored, not logged.
                await self._emit(Step(say=[KEYPAD_REMINDER], stay=True))
                return
            await self._turn(node, Input(text=text))

    async def _on_digit(self, digit: str) -> None:
        assert self.call is not None
        self._cancel_silence()
        node = REGISTRY[self.call.node]
        if node.kind == Kind.SPEECH:
            return  # this question is answered by speaking
        if digit == "*":
            self._digits = ""
            return
        if digit == "#":
            if not self._digits:
                return
        elif digit.isdigit():
            self._digits += digit
            if not node.num_digits or len(self._digits) < node.num_digits:
                self._arm_silence(0)
                return
        else:
            return
        digits, self._digits = self._digits, ""
        async with self._turn_lock:
            await self._turn(node, Input(digits=digits))

    async def _on_silence(self) -> None:
        assert self.call is not None
        async with self._turn_lock:
            await self._turn(REGISTRY[self.call.node], Input(), noinput=True)

    async def _turn(self, node: Any, inp: Input, *, noinput: bool = False) -> None:
        assert self.call is not None
        started = time.perf_counter()
        step = await run_turn(self.call, node, inp, noinput=noinput, settings=self.settings)
        log.info(
            "live_turn",
            node=node.id,
            ms=round((time.perf_counter() - started) * 1000),
            next=step.goto or ("end" if step.end else "same"),
        )
        if self.calllog:
            self.calllog.upsert(self.call, ended=step.end)
        await self._emit(step)

    # -- outbound ----------------------------------------------------------------------------
    async def _emit(self, step: Step) -> None:
        assert self.call is not None
        lines = spoken_lines(self.call, step)
        for line in lines:
            await self._send({"type": "text", "token": speakable(line) + " ", "last": False})
        await self._send({"type": "text", "token": "", "last": True})  # flush to speech
        chars = sum(len(line) for line in lines)
        if step.end:
            await self._end_after(chars)
        else:
            self._arm_silence(chars)

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _end_after(self, spoken_chars: int) -> None:
        """Let the goodbye finish playing, then end the relay session (Twilio gives no
        "finished speaking" signal unless extra events are configured)."""
        wait = spoken_chars / relay_ws.SPEECH_CHARS_PER_SEC + relay_ws.END_PADDING_SEC
        await asyncio.sleep(min(wait, relay_ws.MAX_GOODBYE_WAIT_SEC))
        await self._send({"type": "end"})
        self._done = True

    # -- silence -----------------------------------------------------------------------------
    def _cancel_silence(self) -> None:
        task, self._silence_task = self._silence_task, None
        if task and not task.done():
            task.cancel()

    def _arm_silence(self, spoken_chars: int) -> None:
        """Start the no-answer timer once the agent should have finished speaking."""
        self._cancel_silence()
        wait = (
            spoken_chars / relay_ws.SPEECH_CHARS_PER_SEC + self.settings.no_input_timeout_ms / 1000
        )
        self._silence_task = asyncio.create_task(self._silence_after(wait))

    async def _silence_after(self, wait: float) -> None:
        await asyncio.sleep(wait)
        await self._on_silence()


@router.websocket("/voice/live")
async def live(ws: WebSocket) -> None:
    await ws.accept()
    state = ws.app.state
    await LiveCall(
        ws=ws,
        core=state.core,
        settings=get_settings(),
        store=state.calls,
        jobs=state.jobs,
        dials=state.dials,
        calllog=state.calllog,
    ).run()
