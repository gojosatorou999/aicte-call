"""Per-number daily call limit for anonymous public callers (app.md 12.3:
10 calls/day per number).

In-memory and per-process: good for one worker (demo). A multi-worker
deployment needs the same interface backed by Redis (V7).
"""

from __future__ import annotations

from datetime import date

from app import clock


class DailyLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._day: date | None = None
        self._counts: dict[str, int] = {}

    def allow(self, key: str) -> bool:
        """Count one call for `key`; False once it has used up today's allowance.

        Blocked attempts are not counted, so a blocked caller is not locked
        out for longer than the day itself."""
        today = clock.today()
        if self._day != today:
            self._day, self._counts = today, {}
        used = self._counts.get(key, 0)
        if used >= self._limit:
            return False
        self._counts[key] = used + 1
        return True
