"""Streaming chat-completion adapter.

The brain talks to the small `ChatStream` protocol; `OpenAIChat` is the real
implementation and tests use a scripted fake. Events are provider-neutral.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from app.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """A complete tool call (arguments already assembled from stream chunks)."""

    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


LLMEvent = TextDelta | ToolCallEvent


class ChatStream(Protocol):
    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]: ...


class OpenAIChat:
    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self._s = s
        # One long-lived client: connection reuse keeps first-token latency down.
        self._client = AsyncOpenAI(
            api_key=s.llm_api_key.get_secret_value() or "unset",
            timeout=s.llm_timeout_ms / 1000,
            max_retries=0,  # a retry would blow the latency budget; the caller handles failure
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMEvent]:
        kwargs: dict[str, Any] = {
            "model": self._s.llm_model,
            "messages": messages,
            "max_tokens": self._s.llm_max_tokens,
            "stream": True,
            "temperature": 0.3,
        }
        if tools:
            kwargs["tools"] = tools
        response = await self._client.chat.completions.create(**kwargs)

        pending: dict[int, dict[str, str]] = {}
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield TextDelta(delta.content)
            for tc in delta.tool_calls or []:
                slot = pending.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        for index in sorted(pending):
            slot = pending[index]
            yield ToolCallEvent(slot["id"], slot["name"], slot["arguments"] or "{}")
