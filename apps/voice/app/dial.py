"""The "call me" feature: validate a number, ask Twilio to ring it, track the call.

Flow (same as the earlier Sentinel dial pad):
    dial pad page --POST /dial/call--> this service --Twilio REST: calls.create--> Twilio
    Twilio rings the phone; when it is answered Twilio fetches
    /voice/outbound-twiml?d=<id> from this service and the conversation starts.

The endpoint is reachable from a public web page, so it defends the Twilio
account: number allow-list by country prefix, per-number / per-address / daily
limits, an optional access code, and CORS limited to the dial pad's origin.
"""

from __future__ import annotations

import asyncio
import hmac
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.rest import Client

from app import clock
from app.agent.personas import CallPurpose, OutboundJob
from app.config import Settings
from app.obs import get_logger
from app.session import Persona
from app.utils.hashing import hash_msisdn

log = get_logger(__name__)

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
DEFAULT_COUNTRY_CODE = "+91"
HOUR = 3600
DAY = 86_400
RECORD_TTL_SEC = 6 * HOUR

# Scenarios offered on the dial pad when DEMO_SCENARIOS is on (mock core only).
SCENARIOS: dict[str, str] = {
    "public": "Public enquiry - check an institution or file a complaint",
    "institution": "Institution - reminder about a pending undertaking",
    "officer": "AICTE officer - queue, overdue items, reports (PIN 246810)",
    "faculty": "Faculty member - record status (code 123456)",
}
_SCENARIO_PERSONA = {
    "public": Persona.PUBLIC,
    "institution": Persona.INSTITUTION,
    "officer": Persona.OFFICER,
    "faculty": Persona.FACULTY,
}
_SCENARIO_SUBJECT = {"institution": "inst-001", "officer": "off-001", "faculty": "fac-001"}


class DialError(Exception):
    """A request the dial pad can show to the person: `status` is the HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize_number(raw: str) -> str:
    """Strip the separators people type and return E.164.

    "98765 43210", "09876543210", "+91 98765-43210", "919876543210" all become
    "+919876543210". Only separators are removed - an invalid number is reported,
    never silently "fixed" into some other number."""
    digits = "".join(ch for ch in raw.strip() if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        number = "+" + digits[1:].replace("+", "")
    elif len(digits) == 10:
        number = DEFAULT_COUNTRY_CODE + digits
    elif len(digits) == 11 and digits.startswith("0"):
        number = DEFAULT_COUNTRY_CODE + digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        number = "+" + digits
    else:
        number = "+" + digits
    if not E164.match(number):
        raise DialError(
            400,
            "That doesn't look like a valid phone number. Include the country code, e.g. +91 98765 43210.",
        )
    return number


class SlidingWindow:
    """At most `limit` events per `window` seconds, per key."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit, self._window = limit, window
        self._events: dict[str, deque[float]] = {}

    def over(self, key: str) -> bool:
        """Is `key` already at its limit? (Does not count as an event.)"""
        q = self._events.get(key)
        if not q:
            return False
        now = time.monotonic()
        while q and now - q[0] > self._window:
            q.popleft()
        return len(q) >= self._limit

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._events.setdefault(key, deque())
        while q and now - q[0] > self._window:
            q.popleft()
        if len(q) >= self._limit:
            return False
        q.append(now)
        return True


@dataclass
class DialRecord:
    dial_id: str
    scenario: str
    to_masked: str
    to_hash: str = ""  # HMAC of the dialled number: lets us check Twilio's `To`
    fetches: int = 0  # how many times Twilio has fetched this call's TwiML
    ws_connected: bool = False  # the live voice WebSocket reached us for this call
    created: float = field(default_factory=time.monotonic)
    call_sid: str = ""
    status: str = "queued"
    checked: float = 0.0  # when status was last fetched from Twilio (monotonic)


class DialStore:
    """What the page needs to show progress, plus the scenario the call was placed for."""

    def __init__(self) -> None:
        self._by_id: dict[str, DialRecord] = {}
        self._by_sid: dict[str, DialRecord] = {}

    def create(self, scenario: str, to_masked: str, to_hash: str = "") -> DialRecord:
        self._sweep()
        rec = DialRecord(secrets.token_urlsafe(12), scenario, to_masked, to_hash)
        self._by_id[rec.dial_id] = rec
        return rec

    def attach_sid(self, rec: DialRecord, call_sid: str, status: str) -> None:
        rec.call_sid, rec.status = call_sid, status
        self._by_sid[call_sid] = rec

    def get(self, dial_id: str) -> DialRecord | None:
        return self._by_id.get(dial_id)

    def by_sid(self, call_sid: str) -> DialRecord | None:
        return self._by_sid.get(call_sid)

    def update_status(self, call_sid: str, status: str) -> None:
        if rec := self._by_sid.get(call_sid):
            rec.status = status

    def _sweep(self) -> None:
        for rec in [
            r for r in self._by_id.values() if time.monotonic() - r.created > RECORD_TTL_SEC
        ]:
            self._by_id.pop(rec.dial_id, None)
            self._by_sid.pop(rec.call_sid, None)


class DialGuard:
    """The limits that stand between a public web page and the Twilio bill."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._per_number = SlidingWindow(settings.dial_per_number_per_hour, HOUR)
        self._per_ip = SlidingWindow(settings.dial_per_ip_per_hour, HOUR)
        self._global = SlidingWindow(settings.dial_global_per_day, DAY)

    def check(self, number: str, client_ip: str) -> None:
        prefixes = self._s.dial_prefixes
        if prefixes and not any(number.startswith(p) for p in prefixes):
            raise DialError(
                403, f"Calls are limited to numbers starting with {', '.join(prefixes)}."
            )
        if number == self._s.twilio_voice_number:
            raise DialError(400, "That is the agent's own number.")
        # Cheapest/most specific limit first; the call is only counted once all pass.
        if not self._global.allow("all"):
            raise DialError(
                429, "The daily call limit has been reached. Please try again tomorrow."
            )
        if not self._per_ip.allow(client_ip):
            raise DialError(
                429, "Too many calls requested from your connection. Please try again later."
            )
        if not self._per_number.allow(number):
            raise DialError(
                429, "This number was called recently. Please wait before requesting another call."
            )


# Twilio error code -> (HTTP status, what to tell the person)
_TWILIO_ERRORS: dict[int, tuple[int, str]] = {
    21608: (
        403,
        "This number isn't verified on the Twilio trial account. Verify it in the Twilio console (Verified Caller IDs) or upgrade the account.",
    ),
    21211: (400, "Twilio says that isn't a valid phone number."),
    21214: (400, "Twilio can't call that number."),
    21217: (400, "Twilio can't call that number."),
    21215: (
        403,
        "Calling that country isn't enabled for this Twilio account (Voice > Geo permissions).",
    ),
    21212: (502, "The agent's own phone number (TWILIO_VOICE_NUMBER) isn't a valid Twilio number."),
    21606: (502, "The agent's own phone number (TWILIO_VOICE_NUMBER) can't make calls."),
    20003: (
        502,
        "Twilio rejected the account credentials. Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
    ),
    20404: (502, "Twilio account not found. Check TWILIO_ACCOUNT_SID."),
}


def _is_parameter_restriction(exc: TwilioRestException) -> bool:
    """Trial accounts may not use optional call parameters (status callbacks, timeouts ...)."""
    text = str(exc.msg or "").lower()
    return "limited parameter access" in text or "disallowed parameters" in text


TERMINAL_STATUSES = frozenset({"completed", "busy", "failed", "no-answer", "canceled"})


class Dialer:
    """Places the outbound call through Twilio's REST API.

    It first asks for the full set of options (status callbacks, ring timeout). A Twilio trial
    account refuses those with "limited parameter access"; nothing is dialled when that happens,
    so the dialler retries with only To / From / Url - which every account accepts - and remembers
    it. In that mode call progress is read from Twilio's API instead of from callbacks."""

    def __init__(
        self, settings: Settings, client_factory: Callable[[str, str], Any] | None = None
    ) -> None:
        self._s = settings
        self._client_factory = client_factory or Client
        self._minimal = False  # True once Twilio has said this account cannot use the extras

    @property
    def polls_status(self) -> bool:
        """No status callbacks in minimal mode, so progress must be fetched."""
        return self._minimal

    def _client(self) -> Any:
        return self._client_factory(
            self._s.twilio_account_sid, self._s.twilio_auth_token.get_secret_value()
        )

    def configured(self) -> str | None:
        """None if ready, otherwise what is missing (shown to the operator)."""
        s = self._s
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", s.twilio_account_sid),
                ("TWILIO_AUTH_TOKEN", s.twilio_auth_token.get_secret_value()),
                ("TWILIO_VOICE_NUMBER", s.twilio_voice_number),
            )
            if not value
        ]
        return f"Not configured on the server: {', '.join(missing)}." if missing else None

    async def place(self, *, to: str, url: str, status_callback: str) -> tuple[str, str]:
        full = {
            "method": "POST",
            "status_callback": status_callback,
            "status_callback_method": "POST",
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            # Ring for 30 s then give up. No answering-machine detection: a person asked for this call.
            "timeout": 30,
        }

        def create(extras: dict[str, Any]) -> Any:
            return self._client().calls.create(
                to=to, from_=self._s.twilio_voice_number, url=url, **extras
            )

        attempts = [{}] if self._minimal else [full, {}]
        for n, extras in enumerate(attempts):
            try:
                call = await asyncio.to_thread(create, extras)
                return str(call.sid), str(call.status)
            except TwilioRestException as exc:
                if extras and _is_parameter_restriction(exc) and n + 1 < len(attempts):
                    log.info("dial_trial_minimal_mode", note="optional call parameters refused")
                    self._minimal = True
                    continue
                # The operator needs Twilio's own words; visitors only see the friendly text below.
                log.warning(
                    "dial_twilio_error",
                    code=exc.code,
                    status=exc.status,
                    twilio_message=str(exc.msg)[:400],
                )
                if _is_parameter_restriction(exc):
                    raise DialError(
                        502,
                        "This Twilio trial account is too limited to place the call. "
                        "Upgrade the account in the Twilio console.",
                    ) from exc
                status, message = _TWILIO_ERRORS.get(
                    exc.code or 0,
                    (502, "Twilio could not place the call. Check the server log for details."),
                )
                raise DialError(status, message) from exc
            except TwilioException as exc:  # network / client trouble
                log.warning("dial_twilio_unreachable", error=type(exc).__name__)
                raise DialError(502, "Could not reach Twilio. Please try again.") from exc
        raise DialError(502, "Twilio could not place the call.")  # not reachable

    async def fetch_status(self, call_sid: str) -> str | None:
        """The call's current status straight from Twilio (queued, ringing, in-progress, ...)."""
        try:
            call = await asyncio.to_thread(lambda: self._client().calls(call_sid).fetch())
        except TwilioException:
            return None
        return str(call.status)


MAX_TWIML_FETCHES = 3  # Twilio may retry a fetch; more than this is a replay


def unsigned_fetch_problem(
    record: DialRecord, form: dict[str, str], settings: Settings
) -> str | None:
    """Why an UNSIGNED request for this call's TwiML must be refused (None = fine).

    Without a signature the request is authenticated by what only the real call can present:
    the unguessable id in the URL (that is how `record` was found), our own AccountSid, the
    CallSid Twilio returned when we placed the call, and the number we dialled."""
    if not hmac.compare_digest(form.get("AccountSid", ""), settings.twilio_account_sid):
        return "account does not match"
    if not record.call_sid or not hmac.compare_digest(form.get("CallSid", ""), record.call_sid):
        return "call does not match"
    if record.to_hash and not hmac.compare_digest(
        hash_msisdn(form.get("To", ""), settings.caller_hash_pepper.get_secret_value()),
        record.to_hash,
    ):
        return "dialled number does not match"
    if record.fetches >= MAX_TWIML_FETCHES:
        return "already used"
    return None


def demo_job(dial_id: str) -> OutboundJob:
    """The sample reminder the "institution" scenario calls about."""
    return OutboundJob(
        job_id=dial_id,
        purpose=CallPurpose.UNDERTAKING_DUE,
        institution_name="Sri Venkateswara Institute of Technology",
        contact_name="the nodal officer",
        item_title="Lease registration",
        subject_ref="UND-2026-0042",
        due_date=clock.today() + timedelta(days=7),
    )


def scenario_persona(scenario: str) -> Persona | None:
    return _SCENARIO_PERSONA.get(scenario)


def scenario_subject(scenario: str) -> str | None:
    return _SCENARIO_SUBJECT.get(scenario)
