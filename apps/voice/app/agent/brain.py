"""The conversation brain: streaming LLM with a tool loop, per-session memory.

`Brain.respond(user_text)` is an async generator of *sentences* ready for
TTS. It can be cancelled at any time (caller barge-in): what was spoken so
far is kept in memory, half-finished tool exchanges are discarded so the
message history stays valid for the next turn.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.agent import guardrails
from app.agent.llm import ChatStream, TextDelta, ToolCallEvent
from app.agent.tools import ToolNotAllowed, ToolRunner, openai_tools
from app.config import Settings, get_settings
from app.core_client import CoreError
from app.obs import get_logger
from app.session import Persona
from app.speech.phrases import phrase

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_HISTORY_TURNS = 12
MAX_TOOL_RESULT_CHARS = 4000
_HANDOFF_TOOL = "handoff_to_officer"


class BrainError(Exception):
    """The LLM failed or timed out; the relay tells the caller and ends the call."""


class Brain:
    def __init__(
        self,
        *,
        llm: ChatStream,
        runner: ToolRunner,
        persona: Persona,
        language: str,
        settings: Settings | None = None,
        extra_context: str = "",
    ) -> None:
        self._llm = llm
        self._runner = runner
        self._persona = persona
        self._language = language
        self._s = settings or get_settings()
        self._system = guardrails.build_system_prompt(persona, language, extra_context)
        self._tools = openai_tools(persona)
        self._turns: list[list[dict[str, Any]]] = []  # committed history, one list per turn
        self.turn_count = 0

    # -- memory --------------------------------------------------------------
    def _history(self) -> list[dict[str, Any]]:
        return [m for turn in self._turns for m in turn]

    def _commit(self, turn: list[dict[str, Any]]) -> None:
        self._turns.append(turn)
        del self._turns[:-MAX_HISTORY_TURNS]

    def note_interrupted(self, spoken: str) -> None:
        """Caller barged in: record only what they actually heard of the last reply."""
        spoken = spoken.strip()
        if not spoken or not self._turns:
            return
        last = self._turns[-1][-1]
        if last.get("role") == "assistant" and not last.get("tool_calls"):
            last["content"] = spoken

    # -- turn ----------------------------------------------------------------
    async def respond(self, user_text: str) -> AsyncIterator[str]:
        self.turn_count += 1
        user_msg = {"role": "user", "content": user_text}

        if guardrails.wants_decision(user_text):
            async for sentence in self._refuse_decision(user_msg):
                yield sentence
            return

        turn: list[dict[str, Any]] = [user_msg]
        spoken: list[str] = []
        completed = False
        try:
            async for sentence in self._run_rounds(turn):
                spoken.append(sentence)
                yield sentence
            completed = True
        finally:
            if completed:
                self._commit(turn)
            else:  # cancelled or failed: keep the user line and what was actually said
                partial: list[dict[str, Any]] = [user_msg]
                if spoken:
                    partial.append({"role": "assistant", "content": " ".join(spoken)})
                self._commit(partial)

    async def _run_rounds(self, turn: list[dict[str, Any]]) -> AsyncIterator[str]:
        for _ in range(MAX_TOOL_ROUNDS):
            messages = [{"role": "system", "content": self._system}, *self._history(), *turn]
            chunker = guardrails.SentenceChunker()
            text_parts: list[str] = []
            calls: list[ToolCallEvent] = []

            try:
                async for event in self._llm.stream(messages, self._tools):
                    if isinstance(event, TextDelta):
                        text_parts.append(event.text)
                        for sentence in chunker.feed(event.text):
                            if guardrails.violates_reply_policy(sentence):
                                async for s in self._policy_refusal(turn, sentence):
                                    yield s
                                return
                            yield sentence
                    elif isinstance(event, ToolCallEvent):
                        calls.append(event)
                for sentence in chunker.flush():
                    if guardrails.violates_reply_policy(sentence):
                        async for s in self._policy_refusal(turn, sentence):
                            yield s
                        return
                    yield sentence
            except Exception as exc:  # cancellation / GeneratorExit are BaseException: not caught
                log.error("llm_failed", error=type(exc).__name__)
                raise BrainError(type(exc).__name__) from exc

            content = "".join(text_parts)
            if not calls:
                if content:
                    turn.append({"role": "assistant", "content": content})
                return

            mark = len(turn)
            turn.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in calls
                    ],
                }
            )
            for call in calls:
                try:
                    result = await self._execute(call)
                except ToolNotAllowed:
                    del turn[mark:]  # an unanswered tool_calls message would break the next request
                    async for s in self._policy_refusal(turn, f"disallowed tool {call.name}"):
                        yield s
                    return
                turn.append({"role": "tool", "tool_call_id": call.id, "content": result})

        # Tool loop never converged: don't leave the caller in silence.
        yield phrase("technical_problem", self._language)

    async def _execute(self, call: ToolCallEvent) -> str:
        try:
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            return json.dumps({"error": "invalid_arguments"})
        log.info("tool_call", tool=call.name)  # args are never logged: may hold PIN/OTP/PII
        try:
            data = await self._runner.run(call.name, args)
        except ToolNotAllowed:
            raise
        except CoreError as exc:
            log.warning("tool_failed", tool=call.name, status=exc.status, code=exc.code)
            return json.dumps({"error": exc.code or "core_unavailable"})
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid_arguments"})
        return json.dumps(data, ensure_ascii=False, default=str)[:MAX_TOOL_RESULT_CHARS]

    # -- refusals --------------------------------------------------------------
    async def _handoff(self, summary: str) -> bool:
        if not self._runner.allows(_HANDOFF_TOOL):
            return False
        try:
            await self._runner.run(_HANDOFF_TOOL, {"summary": summary})
        except (CoreError, ToolNotAllowed):
            return False
        return True

    async def _refuse(self, summary: str) -> list[str]:
        out = [phrase("refuse_decision", self._language)]
        if await self._handoff(summary):
            out.append(phrase("handoff_done", self._language))
        return out

    async def _refuse_decision(self, user_msg: dict[str, Any]) -> AsyncIterator[str]:
        sentences = await self._refuse("Caller asked the assistant to decide on an application; declined.")
        self._commit([user_msg, {"role": "assistant", "content": " ".join(sentences)}])
        for s in sentences:
            yield s

    async def _policy_refusal(self, turn: list[dict[str, Any]], reason: str) -> AsyncIterator[str]:
        """The model tried to say or do something forbidden: replace it."""
        log.warning("guardrail_replaced_reply")
        sentences = await self._refuse(f"Assistant reply blocked by policy: {reason[:120]}")
        turn.append({"role": "assistant", "content": " ".join(sentences)})
        for s in sentences:
            yield s
