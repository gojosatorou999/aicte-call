"""The call report: one row per call, for the admin dashboard and a CSV export.

Like the earlier Sentinel agent's reports: every call becomes a record of what was asked and what
came of it. Rows live in a small SQLite file (the dashboard reads it) and the same data is
rewritten to a CSV file after each change, so there is always an up-to-date `calls.csv` on disk.

Privacy: the caller's number is kept only as its last four digits (never in full), and everything
stored from the conversation has already been through `redact_transcript` (phone numbers and
digit runs removed). PINs and one-time codes are never captured at all (see graph/engine.py).
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import clock
from app.graph.state import CallState
from app.obs import get_logger
from app.utils.redact import redact_transcript

log = get_logger(__name__)

# (column key, heading in the dashboard and the CSV)
COLUMNS: list[tuple[str, str]] = [
    ("call_id", "Call ID"),
    ("started_at", "Started"),
    ("last_activity", "Last activity"),
    ("duration_sec", "Duration (s)"),
    ("direction", "Direction"),
    ("persona", "Caller type"),
    ("phone_masked", "Phone (last 4)"),
    ("intent", "About"),
    ("college_said", "College the caller said"),
    ("college_matched", "College matched"),
    ("result", "Result"),
    ("courses", "Approved courses"),
    ("complaint_ref", "Complaint reference"),
    ("complaint_text", "Complaint"),
    ("turns", "Caller turns"),
    ("outcome", "Outcome"),
    ("transcript", "Transcript"),
]
STALE_AFTER = timedelta(minutes=5)  # no activity this long and never finished -> the caller hung up
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Make text safe to open in a spreadsheet: a cell that starts like a formula is prefixed so it
    stays text. (What callers say is untrusted input.)"""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _join(items: list[str]) -> str:
    return " | ".join(i for i in items if i)


def build_row(call: CallState, *, ended: bool) -> dict[str, Any]:
    """The report row for a call, from what the graph recorded about it."""
    now = clock.now()
    facts = call.facts
    lookups: list[dict[str, Any]] = list(facts.get("lookups", []))
    said = _join([str(look.get("said", "")) for look in lookups])
    if not said:
        said = str(facts.get("complaint_college", ""))
    transcript = " | ".join(
        f"{'Agent' if line.role == 'assistant' else 'Caller'}: {line.text}"
        for line in call.transcript
    )
    return {
        "call_id": call.call_sid,
        "started_at": call.started_at.isoformat(timespec="seconds"),
        "last_activity": now.isoformat(timespec="seconds"),
        "duration_sec": max(0, round((now - call.started_at).total_seconds())),
        "direction": call.direction,
        "persona": call.claims.persona.value,
        "phone_masked": call.phone_masked,
        "intent": str(facts.get("intent", "")),
        "college_said": said,
        "college_matched": _join([str(look.get("matched", "")) for look in lookups]),
        "result": _join([str(look.get("result", "")) for look in lookups])
        or ("complaint_filed" if facts.get("complaint_ref") else ""),
        "courses": str(lookups[-1].get("courses", "")) if lookups else "",
        "complaint_ref": str(facts.get("complaint_ref", "")),
        "complaint_text": redact_transcript(str(facts.get("complaint_text", ""))),
        "turns": sum(1 for line in call.transcript if line.role == "caller"),
        "ended": 1 if ended else 0,
        "transcript": transcript,
    }


class CallLog:
    def __init__(self, db_path: str, csv_path: str) -> None:
        self._csv_path = Path(csv_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        cols = ", ".join(
            f"{key} {'INTEGER' if key in ('duration_sec', 'turns') else 'TEXT'}"
            for key, _ in COLUMNS
            if key != "outcome"
        )
        with self._lock, self._db:
            self._db.execute(
                f"CREATE TABLE IF NOT EXISTS calls ({cols}, ended INTEGER DEFAULT 0, PRIMARY KEY (call_id))"
            )

        self._write_csv()  # calls.csv exists (with its header) from the first start

    # -- writing -----------------------------------------------------------------------------
    def upsert(self, call: CallState, *, ended: bool = False) -> None:
        """Create or update the call's row, then refresh calls.csv. Never raises: losing a report
        row must not drop a live call."""
        try:
            row = build_row(call, ended=ended)
            keys = [k for k in row]
            update = ", ".join(
                f"{k}=excluded.{k}" for k in keys if k != "call_id" and k != "started_at"
            )
            with self._lock, self._db:
                # Column names come from fixed code (build_row), never from caller input.
                self._db.execute(
                    f"INSERT INTO calls ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)}) "  # noqa: S608
                    f"ON CONFLICT(call_id) DO UPDATE SET {update}",
                    [row[k] for k in keys],
                )
            self._write_csv()
        except Exception as exc:
            log.warning("calllog_write_failed", error=type(exc).__name__)

    # -- reading -----------------------------------------------------------------------------
    def rows(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            found = self._db.execute(
                "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._present(dict(r)) for r in found]

    def get(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._db.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        return self._present(dict(found)) if found else None

    @staticmethod
    def _present(row: dict[str, Any]) -> dict[str, Any]:
        """Add the outcome, which depends on the time of reading: a call that never finished and has
        been quiet for a while is one the caller hung up."""
        if row.pop("ended", 0):
            row["outcome"] = "completed"
        else:
            idle = clock.now() - datetime.fromisoformat(row["last_activity"])
            row["outcome"] = "hung_up" if idle > STALE_AFTER else "in_progress"
        return row

    def summary(self) -> dict[str, int]:
        rows = self.rows(limit=100_000)
        results = [r["result"] for r in rows]
        return {
            "calls": len(rows),
            "college_checks": sum(1 for r in rows if r["intent"] == "college_check"),
            "approved": sum(1 for x in results if "approved" in x.split(" | ")),
            "not_approved": sum(1 for x in results if "not_approved" in x.split(" | ")),
            "not_found": sum(1 for x in results if "not_found" in x.split(" | ")),
            "complaints": sum(1 for r in rows if r["complaint_ref"]),
        }

    # -- CSV ---------------------------------------------------------------------------------
    def csv_text(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow([heading for _, heading in COLUMNS])
        for row in reversed(self.rows(limit=100_000)):  # oldest first, like a ledger
            writer.writerow([safe_cell(row.get(key)) for key, _ in COLUMNS])
        return out.getvalue()

    def _write_csv(self) -> None:
        """Rewrite calls.csv atomically. If the file is open in a spreadsheet (Windows locks it)
        the rewrite is skipped; the database and the dashboard export are unaffected."""
        tmp = self._csv_path.with_suffix(".csv.tmp")
        try:
            tmp.write_text(
                self.csv_text(), encoding="utf-8-sig", newline=""
            )  # BOM: Excel reads UTF-8
            os.replace(tmp, self._csv_path)
        except OSError as exc:
            log.warning("calls_csv_not_updated", error=type(exc).__name__)
