"""Graceful drain state. A SIGTERM flips a process-wide flag; `/voice/inbound`
then returns the fallback TwiML instead of starting a conversation on a worker
that is about to die. In-flight calls are unaffected."""

from __future__ import annotations

_draining: bool = False


def is_draining() -> bool:
    return _draining


def mark_draining() -> None:
    global _draining
    _draining = True


def reset_for_tests() -> None:
    global _draining
    _draining = False
