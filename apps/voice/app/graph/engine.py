"""The conversation graph: nodes, steps and the turn loop.

A *node* asks something (a prompt, played inside a Twilio <Gather>) and has
a *handler* that decides what happens with the caller's answer. A handler
returns a *Step*: what to say, and where to go next. Twilio posts each answer
to `/voice/g/turn?n=<node>`; `run_turn` finds the node, applies the global
rules (repeat, menu, goodbye, officer, abuse, refuse-to-decide), runs the
handler and returns the next Step. Nothing here calls an LLM.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.agent import guardrails
from app.config import Settings
from app.graph.state import CallState
from app.speech.intent import has_any

MENU_NODE = {
    "PUBLIC": "pub_menu",
    "INSTITUTION": "inst_menu",
    "OFFICER": "off_menu",
    "FACULTY": "fac_menu",
    "EXPERT": "pub_menu",
}


class Kind(StrEnum):
    SPEECH = "speech"
    DTMF = "dtmf"
    BOTH = "speech dtmf"


@dataclass(frozen=True, slots=True)
class Input:
    text: str = ""  # SpeechResult
    digits: str = ""  # Digits

    @property
    def any(self) -> str:
        """What the caller gave, whichever way they gave it."""
        return self.text or self.digits


@dataclass
class Step:
    say: list[str] = field(default_factory=list)
    goto: str | None = None  # next node to prompt
    stay: bool = False  # prompt the current node again
    end: bool = False  # say, then hang up
    relay: bool = False  # hand the call to the LLM relay (hybrid mode)


Handler = Callable[[CallState, Input], Awaitable[Step]]
Prompt = Callable[[CallState], str]


@dataclass(frozen=True)
class Node:
    id: str
    prompt: Prompt
    handler: Handler
    kind: Kind = Kind.SPEECH
    num_digits: int | None = None
    finish_on_key: str | None = None
    hints: str = ""
    open_ended: bool = False  # free text: only the abuse check applies
    sensitive: bool = False  # PIN/OTP: nothing is stored, no global rules


REGISTRY: dict[str, Node] = {}


def node(
    node_id: str,
    prompt: Prompt | str,
    *,
    kind: Kind = Kind.SPEECH,
    num_digits: int | None = None,
    finish_on_key: str | None = None,
    hints: str = "",
    open_ended: bool = False,
    sensitive: bool = False,
) -> Callable[[Handler], Handler]:
    """Register `handler` as the handler of node `node_id`."""

    def register(handler: Handler) -> Handler:
        text = prompt
        REGISTRY[node_id] = Node(
            id=node_id,
            prompt=(lambda _c, _t=text: _t) if isinstance(text, str) else text,
            handler=handler,
            kind=kind,
            num_digits=num_digits,
            finish_on_key=finish_on_key,
            hints=hints,
            open_ended=open_ended,
            sensitive=sensitive,
        )
        return handler

    return register


# -- the turn loop ----------------------------------------------------------------------
GOODBYE = (
    "goodbye",
    "bye",
    "hang up",
    "that is all",
    "thats all",
    "nothing else",
    "no more",
    "stop",
)
MENU = ("main menu", "start over", "go back", "back to menu", "menu")
REPEAT = ("repeat", "say that again", "again", "pardon")
OFFICER = ("officer", "human", "person", "agent", "representative", "operator", "someone")

MSG_NO_INPUT = "Sorry, I did not hear anything."
MSG_GOODBYE = "Thank you for calling. Goodbye."
MSG_SILENCE_END = (
    "I am unable to hear you, so I will end the call. Please call again any time. Goodbye."
)
MSG_TIME_LIMIT = "We have reached the time limit for this call. Please call again if you need more help. Goodbye."
MSG_ABUSE_WARNING = (
    "Please keep the conversation respectful, otherwise I will have to end the call."
)
MSG_ABUSE_END = "I am ending this call now. Goodbye."
MSG_TECHNICAL = "I am having a technical problem right now. Please try again shortly. Goodbye."
MSG_REFUSE = "I cannot approve, reject or change an application."
MSG_HANDOFF_DONE = "I have asked an officer to call you back."
MSG_NO_HANDOFF = "Please contact your regional AICTE office for help."


def menu_for(call: CallState) -> str:
    return MENU_NODE[call.claims.persona.value]


async def handoff(call: CallState, summary: str, *, end: bool = True) -> Step:
    """Ask an officer to follow up; falls back to a pointer to the regional office."""
    if call.runner.allows("handoff_to_officer"):
        res = await call.tool("handoff_to_officer", {"summary": summary})
        if res.ok:
            return Step(say=[MSG_HANDOFF_DONE], end=end, goto=None if end else menu_for(call))
    return Step(say=[MSG_NO_HANDOFF], end=end, goto=None if end else menu_for(call))


async def fallback(call: CallState, summary: str) -> Step:
    """The graph can't help: use the LLM relay if this deployment allows it,
    otherwise arrange an officer callback."""
    if call.hybrid_available:
        return Step(relay=True)
    return await handoff(call, summary)


async def _globals(call: CallState, n: Node, inp: Input) -> Step | None:
    text = inp.text
    if not text:
        return None
    if guardrails.is_abusive(text):
        call.abuse_strikes += 1
        if call.abuse_strikes == 1:
            return Step(say=[MSG_ABUSE_WARNING], stay=True)
        return Step(say=[MSG_ABUSE_END], end=True)
    if n.open_ended or n.sensitive:
        return None
    if guardrails.wants_decision(text):
        done = await handoff(
            call, "Caller asked the assistant to decide on an application; declined.", end=False
        )
        return Step(say=[MSG_REFUSE, *done.say], goto=menu_for(call))
    if has_any(text, *REPEAT) and len(text.split()) <= 4:
        return Step(stay=True)
    if has_any(text, *GOODBYE):
        return Step(say=[MSG_GOODBYE], end=True)
    if has_any(text, *MENU):
        return Step(goto=menu_for(call))
    if has_any(text, *OFFICER):
        return await handoff(call, "Caller asked to speak to an officer.")
    return None


async def run_turn(
    call: CallState, n: Node, inp: Input, *, noinput: bool, settings: Settings
) -> Step:
    """One caller answer (or silence) -> the next Step."""
    limit = (
        settings.max_call_duration_public_sec
        if call.claims.persona.value == "PUBLIC"
        else settings.max_call_duration_default_sec
    )
    if call.elapsed() >= limit:
        return Step(say=[MSG_TIME_LIMIT], end=True)
    if call.core_down:
        return Step(say=[MSG_TECHNICAL], end=True)

    if noinput or not inp.any.strip():
        call.silence += 1
        if call.silence > settings.silence_reprompts:
            return Step(say=[MSG_SILENCE_END], end=True)
        return Step(say=[MSG_NO_INPUT], stay=True)
    call.silence = 0

    if not n.sensitive:
        call.note("caller", inp.text or f"(keypad: {inp.digits})")
    if (step := await _globals(call, n, inp)) is not None:
        return step
    return await n.handler(call, inp)
