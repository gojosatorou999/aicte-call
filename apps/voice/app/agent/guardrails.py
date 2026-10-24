"""Guardrails (app.md 12.4).

Three layers, none of which trusts the LLM alone:
  1. the system prompt (`build_system_prompt`),
  2. per-persona tool allowlist enforced in `tools.ToolRunner` (and again by core),
  3. deterministic screens here: caller intent that must be refused, reply
     text that must never be spoken, and abuse.

The deterministic screens are deliberately narrow (English + Hindi
patterns for the highest-risk statements). They back up the prompt; they
do not replace it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Final

from app import clock
from app.session import Persona
from app.speech.languages import get_language

_PROMPT_DIR: Final = Path(__file__).parent / "prompts"

# Caller asks the assistant to decide a case ("approve my application").
_DECISION_REQUEST: Final = re.compile(
    r"\b(approve|approval\s+do|reject|cancel|withdraw|grant)\b.{0,40}\b(my|our|the|this)\b.{0,30}"
    r"\b(application|file|case|request|extension)\b"
    r"|\b(application|file|case)\b.{0,30}\b(approve|reject)\b.{0,12}\b(kar|karo|kijiye|do)\b"
    r"|(आवेदन|एप्लिकेशन|फाइल).{0,25}(मंजूर|स्वीकृत|अप्रूव|रिजेक्ट|खारिज).{0,12}(कर|करो|कीजिए|दो)",
    re.IGNORECASE | re.DOTALL,
)

# Statements the assistant must never make (speculation / promises / decisions).
_FORBIDDEN_REPLY: Final = re.compile(
    r"\b(?:your|the)\s+(?:application|request|extension|case)\s+(?:has\s+been|is|was|will\s+be)\s+"
    r"(?:approved|granted|accepted|rejected)\b"
    r"|\bi\s+(?:have\s+|will\s+|can\s+|am\s+going\s+to\s+)?(?:approve|grant|guarantee|promise)\b"
    r"|\b(?:extension|deadline)\s+(?:is|has\s+been)\s+(?:granted|extended)\b"
    r"|\bwill\s+(?:definitely|certainly)\s+be\s+approved\b",
    re.IGNORECASE,
)

_ABUSE: Final = re.compile(
    r"\b(fuck\w*|bastard|bitch|asshole|idiot|stupid|madarchod|behenchod|bhosdi\w*|chutiy\w*|harami\w*|saala|kamina)\b"
    r"|(मादरचोद|बहनचोद|चूतिया|हरामी|कमीना|साला|गधा|बेवकूफ)",
    re.IGNORECASE,
)

_SENTENCE_END: Final = re.compile(r"(?<=[.!?।॥])\s+|(?<=[.!?।॥])$")


@lru_cache(maxsize=16)
def _prompt_file(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def build_system_prompt(persona: Persona, language: str, extra_context: str = "") -> str:
    """Common rules + persona rules + today's date (demo-clock aware) + language."""
    lang = get_language(language)
    today = clock.now()
    parts = [
        _prompt_file("common"),
        _prompt_file(persona.value.lower()),
        f"Today is {today:%A, %d %B %Y}, India time. Resolve relative dates against it.",
        f"Speak to the caller in {lang.name} ({lang.native_name}); write numbers and dates the way "
        f"a {lang.name} speaker would say them aloud.",
    ]
    if extra_context:
        parts.append(extra_context)
    return "\n\n".join(parts)


def wants_decision(text: str) -> bool:
    """Caller is asking the assistant to approve/reject/change a case."""
    return bool(_DECISION_REQUEST.search(text))


def violates_reply_policy(text: str) -> bool:
    return bool(_FORBIDDEN_REPLY.search(text))


def is_abusive(text: str) -> bool:
    return bool(_ABUSE.search(text))


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` into complete sentences and a trailing partial one."""
    pieces = _SENTENCE_END.split(buffer)
    if len(pieces) <= 1:
        return [], buffer
    return [p.strip() for p in pieces[:-1] if p.strip()], pieces[-1]


class SentenceChunker:
    """Turns a token stream into sentences.

    Sentence-level output gives the policy screen a unit to judge before
    anything is spoken, and TTS engines read whole sentences more naturally
    than word fragments. A long run with no punctuation is flushed at a
    comma/space so the first audio is never held up indefinitely."""

    MAX_UNPUNCTUATED = 160

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> Iterator[str]:
        self._buf += token
        done, self._buf = split_sentences(self._buf)
        yield from done
        if len(self._buf) > self.MAX_UNPUNCTUATED:
            cut = max(self._buf.rfind(", "), self._buf.rfind(" "))
            if cut > 0:
                yield self._buf[:cut].strip()
                self._buf = self._buf[cut:].lstrip()

    def flush(self) -> Iterator[str]:
        rest, self._buf = self._buf.strip(), ""
        if rest:
            yield rest
