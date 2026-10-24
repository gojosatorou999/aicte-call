"""Server-side call policy: what a session may do, enforced outside the LLM.

The prompt asks the model to behave; this module makes it so. It decides
which tools the model can even see, refuses or rewrites calls that break
policy, and makes irreversible actions require a spoken "yes".

  * Verification: officers (PIN) and faculty (OTP) see nothing but open tools
    until verified; callers whose persona came from a typed number
    (ENTERED_NUMBER) never get more than open tools. PIN/OTP entry happens
    on the keypad through `verification.Verifier`, never through the model.
  * Confirm-first: record_commitment, log_extension_request,
    trigger_reminder and create_grievance return `needs_confirmation` the
    first time. They run only when the identical call is repeated after the
    caller has said an explicit yes.
  * Commitment dates: parsed, never in the past, never beyond
    COMMITMENT_MAX_DAYS (only the competent authority grants extensions).
  * Public callers: bounded number of registry lookups per call.
  * Identity-bound parameters (the faculty id) are injected, not chosen.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from app import clock
from app.agent.tools import ToolNotAllowed, ToolRunner, load_tool_specs, openai_tools
from app.config import Settings, get_settings
from app.obs import get_logger
from app.session import IdentitySource, Persona, SessionClaims
from app.speech import dates
from app.speech.intent import is_affirmative

log = get_logger(__name__)


class Verification(StrEnum):
    NONE = "NONE"
    PIN = "PIN"
    OTP = "OTP"


# Handled by the keypad verification flow; the model never sees or calls them.
FLOW_ONLY_TOOLS = frozenset({"verify_officer_pin", "send_otp", "check_otp"})
# Callable before verification / by unverified (typed-number) identities.
OPEN_TOOLS = frozenset({"search_aph", "handoff_to_officer"})
CONFIRM_FIRST = frozenset(
    {"record_commitment", "log_extension_request", "trigger_reminder", "create_grievance"}
)
PUBLIC_LOOKUP_TOOLS = frozenset({"search_institutions", "get_public_approval_status"})
# Identity-bound parameters filled from the session, hidden from the model.
BOUND_PARAMS = {"get_faculty_status": {"faculty_id"}}

LOCAL_RESOLVE_DATE = "resolve_date"
_RESOLVE_DATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": LOCAL_RESOLVE_DATE,
        "description": (
            "Convert the caller's own words for a date (e.g. 'next Friday', '20th', 'end of the "
            "month', in any language) into a calendar date. Always use this instead of working "
            "out dates yourself, then read the returned spoken date back to the caller."
        ),
        "parameters": {
            "type": "object",
            "properties": {"phrase": {"type": "string", "description": "the caller's words"}},
            "required": ["phrase"],
        },
    },
}

MAX_OVERDUE_LIST = 10


class SessionPolicy:
    def __init__(self, claims: SessionClaims, settings: Settings | None = None) -> None:
        self.claims = claims
        self._s = settings or get_settings()
        self.verified = claims.verified
        self._utterance_no = 0
        self._last_user = ""
        self._pending: dict[str, tuple[str, int]] = {}  # tool -> (fingerprint, utterance_no)
        self._public_lookups = 0

    # -- verification ----------------------------------------------------------
    def verification_needed(self) -> Verification:
        if self.verified:
            return Verification.NONE
        return {Persona.OFFICER: Verification.PIN, Persona.FACULTY: Verification.OTP}.get(
            self.claims.persona, Verification.NONE
        )

    def mark_verified(self) -> None:
        self.verified = True

    # -- visibility ------------------------------------------------------------
    def visible_tools(self) -> frozenset[str]:
        c = self.claims
        names = {
            t.name for t in load_tool_specs().values() if c.persona in t.personas
        } - FLOW_ONLY_TOOLS
        if self.is_restricted():
            names &= OPEN_TOOLS
        return frozenset(names)

    def is_restricted(self) -> bool:
        """Only open tools until the caller proves who they are."""
        c = self.claims
        return self.verification_needed() != Verification.NONE or (
            c.persona == Persona.INSTITUTION and c.identity_source == IdentitySource.ENTERED_NUMBER
        )

    # -- utterances ------------------------------------------------------------
    def note_user(self, text: str) -> None:
        self._utterance_no += 1
        self._last_user = text

    # -- per-call gate -----------------------------------------------------------
    def prepare(
        self, name: str, args: dict[str, Any], *, skip_confirm: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Returns (args to send, short-circuit result or None).

        `skip_confirm` is for the Gather graph, which does its own explicit
        yes/no step before calling; every other rule still applies.

        A short-circuit result is handed to the model as the tool's output
        without calling core."""
        args = dict(args)
        today = clock.today()

        if name in PUBLIC_LOOKUP_TOOLS:
            if self._public_lookups >= self._s.public_lookups_per_call:
                return args, {"error": "lookup_limit_reached"}
            self._public_lookups += 1

        if name in BOUND_PARAMS:
            if not self.claims.subject_id:
                return args, {"error": "identity_unknown"}
            args["faculty_id"] = self.claims.subject_id

        if name == "record_commitment":
            err = self._normalise_date(args, "committed_date", today)
            if err is None:
                err = self.commitment_error(date.fromisoformat(args["committed_date"]))
            if err:
                return args, err
        elif name == "log_extension_request":
            err = self._normalise_date(args, "requested_date", today)
            if err is None and date.fromisoformat(args["requested_date"]) < today:
                err = {"error": "date_in_past"}
            if err:
                return args, err
        elif name == "schedule_callback":
            err = self._normalise_datetime(args)
            if err:
                return args, err
        elif name == "create_grievance":
            if len(str(args.get("description", "")).strip()) < 10:
                return args, {"error": "description_too_short"}
        elif name == "list_overdue":
            try:
                limit = int(args.get("limit") or 5)
            except (TypeError, ValueError):
                limit = 5
            args["limit"] = max(1, min(limit, MAX_OVERDUE_LIST))

        if name in CONFIRM_FIRST:
            held = None if skip_confirm else self._confirm_first(name, args)
            if held is not None:
                return args, held
            if name == "trigger_reminder":
                args["confirmed"] = True
        return args, None

    def commitment_error(self, chosen: date) -> dict[str, Any] | None:
        """The one rule for "can this date be recorded as a commitment": not in the
        past, and at most COMMITMENT_MAX_DAYS ahead (only the competent authority
        grants extensions). Shared by the LLM tools and the Gather graph."""
        today = clock.today()
        latest = today + timedelta(days=self._s.commitment_max_days)
        if chosen < today:
            return {"error": "date_in_past"}
        if chosen > latest:
            return {
                "error": "date_beyond_policy",
                "max_date": latest.isoformat(),
                "hint": (
                    "Explain that only the competent authority can grant extensions and "
                    "offer to log an extension request for officer review."
                ),
            }
        return None

    # -- helpers -------------------------------------------------------------------
    def _confirm_first(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        # `confirmed` is ours to set, so it must not distinguish preview from confirmation.
        fingerprint = json.dumps(
            {k: v for k, v in args.items() if k != "confirmed"}, sort_keys=True, default=str
        )
        pending = self._pending.get(name)
        if (
            pending is not None
            and pending[0] == fingerprint
            and self._utterance_no > pending[1]
            and is_affirmative(self._last_user)
        ):
            del self._pending[name]
            return None
        self._pending[name] = (fingerprint, self._utterance_no)
        return {
            "status": "needs_confirmation",
            "tool": name,
            "details": args,
            "instruction": (
                "NOT SAVED YET. Read these details back to the caller in their language and wait "
                "for a clear yes. After the caller says yes, call this tool again with exactly the "
                "same arguments. If they correct anything, use the corrected values instead."
            ),
        }

    def _normalise_date(self, args: dict[str, Any], key: str, today: date) -> dict[str, Any] | None:
        raw = str(args.get(key, "")).strip()
        try:
            try:
                args[key] = date.fromisoformat(raw[:10]).isoformat()
            except ValueError:  # not ISO: the model passed the caller's words through
                args[key] = dates.resolve(raw, today, self.claims.language).iso
        except dates.DateParseError:
            return {
                "error": "invalid_date",
                "hint": "Use resolve_date with the caller's own words, or ask for a calendar date.",
            }
        return None

    def _normalise_datetime(self, args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            when = datetime.fromisoformat(str(args.get("when", "")))
        except ValueError:
            return {
                "error": "invalid_datetime",
                "hint": "Use an ISO 8601 date and time, India time.",
            }
        now = clock.now()
        if when.tzinfo is None:
            when = when.replace(tzinfo=now.tzinfo)
        if when <= now:
            return {"error": "time_in_past"}
        args["when"] = when.isoformat()
        return None


class GatedToolRunner:
    """What the brain talks to: `ToolRunner` behind `SessionPolicy`, plus the
    local `resolve_date` tool."""

    def __init__(self, inner: ToolRunner, policy: SessionPolicy) -> None:
        self._inner = inner
        self.policy = policy

    @property
    def _local_tools(self) -> frozenset[str]:
        if self.policy.claims.persona == Persona.INSTITUTION and not self.policy.is_restricted():
            return frozenset({LOCAL_RESOLVE_DATE})
        return frozenset()

    def schemas(self) -> list[dict[str, Any]]:
        out = openai_tools(
            self.policy.claims.persona, only=self.policy.visible_tools(), hide=BOUND_PARAMS
        )
        if LOCAL_RESOLVE_DATE in self._local_tools:
            out.append(_RESOLVE_DATE_SCHEMA)
        return out

    def allows(self, name: str) -> bool:
        return name in self.policy.visible_tools() or name in self._local_tools

    def note_user(self, text: str) -> None:
        self.policy.note_user(text)

    async def run(self, name: str, args: dict[str, Any], *, skip_confirm: bool = False) -> Any:
        if not self.allows(name):
            log.warning("tool_blocked", tool=name, persona=self.policy.claims.persona.value)
            raise ToolNotAllowed(f"{name!r} is not available in this session")
        if name == LOCAL_RESOLVE_DATE:
            return self._resolve_date(str(args.get("phrase", "")))
        send, short_circuit = self.policy.prepare(name, args, skip_confirm=skip_confirm)
        if short_circuit is not None:
            return short_circuit
        return await self._inner.run(name, send)

    def _resolve_date(self, phrase: str) -> dict[str, Any]:
        try:
            r = dates.resolve(phrase, clock.today(), self.policy.claims.language)
        except dates.DateParseError:
            return {
                "error": "unparseable",
                "hint": "Ask the caller for a calendar date, e.g. 'the 20th of November'.",
            }
        return {"date": r.iso, "spoken": r.spoken_en, "spoken_local": r.spoken_local}
