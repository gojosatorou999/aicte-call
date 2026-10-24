"""Preflight check: is everything in place for real phone calls?

    python scripts/check_setup.py

Reads apps/voice/.env, and (read-only, nothing is dialled and nothing is charged) asks Twilio
whether the credentials and the From number are valid, then checks the running service is
reachable through your public (ngrok) address and from the dial pad's web origin. Every
problem is printed with the exact fix. Secrets are never printed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import httpx
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import Settings

SKIP_NGROK_PAGE = {"ngrok-skip-browser-warning": "true"}
PAGES_PATH = "/aicte-call/"


@dataclass
class Report:
    failures: int = 0
    warnings: int = 0
    lines: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        print(f"  [ OK ]  {msg}")

    def warn(self, msg: str, fix: str = "") -> None:
        self.warnings += 1
        print(f"  [warn]  {msg}")
        if fix:
            print(f"          -> {fix}")

    def fail(self, msg: str, fix: str = "") -> None:
        self.failures += 1
        print(f"  [FAIL]  {msg}")
        if fix:
            print(f"          -> {fix}")


def mask(number: str) -> str:
    return "*" * max(len(number) - 4, 0) + number[-4:]


def check_env(s: Settings, r: Report) -> bool:
    print("\n1. Your .env")
    good = True
    if re.fullmatch(r"AC[0-9a-fA-F]{32}", s.twilio_account_sid):
        r.ok("TWILIO_ACCOUNT_SID looks right")
    else:
        good = False
        r.fail(
            "TWILIO_ACCOUNT_SID is missing or malformed",
            "Twilio Console home -> Account SID (starts with AC, 34 characters)",
        )
    if len(s.twilio_auth_token.get_secret_value()) == 32:
        r.ok("TWILIO_AUTH_TOKEN looks right")
    else:
        good = False
        r.fail(
            "TWILIO_AUTH_TOKEN is missing or the wrong length",
            "Twilio Console home -> Auth Token (32 characters)",
        )
    if re.fullmatch(r"\+[1-9]\d{7,14}", s.twilio_voice_number):
        r.ok(f"TWILIO_VOICE_NUMBER is {mask(s.twilio_voice_number)}")
    else:
        good = False
        r.fail(
            "TWILIO_VOICE_NUMBER is missing or not in +E.164 form",
            "e.g. +14155552671 (Phone Numbers -> Active numbers)",
        )
    if s.public_base.startswith("https://") and "localhost" not in s.public_base:
        r.ok(f"PUBLIC_VOICE_URL is {s.public_base}")
    else:
        good = False
        r.fail(
            "PUBLIC_VOICE_URL is not a public https address",
            "run scripts\\start_all.ps1 and use the ngrok address it prints",
        )
    if not s.twilio_signature_validation_enabled:
        r.warn(
            "TWILIO_SIGNATURE_VALIDATION_ENABLED is false",
            "set it to true: with it off anyone can forge Twilio webhooks",
        )
    if s.conversation_mode == "live":
        r.ok("CONVERSATION_MODE=live (streaming speech + human voice, no OpenAI)")
    else:
        r.warn(
            f"CONVERSATION_MODE={s.conversation_mode}",
            "use live for the human-sounding, interruptible voice",
        )
    return good


def check_twilio(s: Settings, r: Report) -> None:
    print("\n2. Twilio (read-only checks; nothing is dialled)")
    client = Client(s.twilio_account_sid, s.twilio_auth_token.get_secret_value())
    try:
        account = client.api.accounts(s.twilio_account_sid).fetch()
    except TwilioRestException as exc:
        r.fail(
            f"Twilio rejected the credentials (code {exc.code})",
            "re-copy the Account SID and Auth Token from the Twilio Console",
        )
        return
    r.ok(f"credentials accepted - account status: {account.status}, type: {account.type}")
    if account.status != "active":
        r.fail(f"the Twilio account is {account.status}", "reactivate it in the Console")

    owned = client.incoming_phone_numbers.list(phone_number=s.twilio_voice_number, limit=1)
    verified_ids = client.outgoing_caller_ids.list(limit=50)
    if owned:
        caps = owned[0].capabilities or {}
        if caps.get("voice"):
            r.ok(f"{mask(s.twilio_voice_number)} is your Twilio number and can make voice calls")
        else:
            r.fail(
                f"{mask(s.twilio_voice_number)} is not voice-capable",
                "buy/choose a number with Voice capability",
            )
    elif any(v.phone_number == s.twilio_voice_number for v in verified_ids):
        r.ok(f"{mask(s.twilio_voice_number)} is one of your verified caller IDs")
    else:
        r.fail(
            f"{mask(s.twilio_voice_number)} is not a number on this Twilio account",
            "set TWILIO_VOICE_NUMBER to a number under Phone Numbers -> Active numbers",
        )

    if account.type == "Trial":
        numbers = ", ".join(mask(v.phone_number) for v in verified_ids) or "none yet"
        r.warn(
            f"Trial account: it can only call numbers you have verified ({numbers})",
            "add the phone you will test with at Console -> Phone Numbers -> Verified Caller IDs",
        )


def check_service(s: Settings, r: Report) -> None:
    print("\n3. The running service")
    try:
        local = httpx.get("http://localhost:8080/health", timeout=5).json()
        r.ok(f"local service is up (core reachable: {local.get('core_reachable')})")
        if not local.get("core_reachable"):
            r.warn(
                "the core API (mock) is not reachable",
                "start it: python -m uvicorn mock_core.app:app --port 9000",
            )
    except Exception:
        r.fail("the voice service is not running on port 8080", "run scripts\\start_all.ps1")
        return
    try:
        public = httpx.get(f"{s.public_base}/health", headers=SKIP_NGROK_PAGE, timeout=10)
        if public.status_code == 200 and public.json().get("service") == "acp-voice":
            r.ok("reachable through your public address (ngrok tunnel works)")
        else:
            r.fail(
                f"{s.public_base}/health answered HTTP {public.status_code}",
                "is ngrok running and pointed at port 8080?",
            )
    except Exception as exc:
        r.fail(
            f"cannot reach {s.public_base} ({type(exc).__name__})",
            "start the tunnel: scripts\\start_all.ps1",
        )
        return

    origin = next((o for o in s.dialpad_origins if o.startswith("https://")), "")
    if origin:
        pre = httpx.options(
            f"{s.public_base}/dial/call",
            headers={
                **SKIP_NGROK_PAGE,
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type,ngrok-skip-browser-warning",
            },
            timeout=10,
        )
        if pre.headers.get("access-control-allow-origin") == origin:
            r.ok(f"the dial pad at {origin} is allowed to call the API (CORS)")
        else:
            r.fail(
                "the browser would block the dial pad (CORS)",
                "DIALPAD_ALLOWED_ORIGINS must include the dial pad's origin",
            )


def check_pages(s: Settings, r: Report) -> None:
    print("\n4. The dial pad web page")
    origin = next((o for o in s.dialpad_origins if o.startswith("https://")), "")
    if not origin:
        return
    url = origin + PAGES_PATH
    try:
        page = httpx.get(url, timeout=10, follow_redirects=True)
        config = httpx.get(url + "config.js", timeout=10)
    except Exception as exc:
        r.warn(f"could not load {url} ({type(exc).__name__})")
        return
    if page.status_code == 200 and "Call me" in page.text:
        r.ok(f"dial pad is live at {url}")
        if s.public_base in config.text:
            r.ok("the page points at your current ngrok address")
        else:
            r.fail(
                "dialpad/config.js points at a different backend address",
                f"set api to {s.public_base} in dialpad/config.js and push",
            )
    else:
        r.warn(
            f"{url} answered HTTP {page.status_code}",
            "GitHub Pages may still be deploying; see the repo's Actions tab",
        )


def main() -> int:
    s = Settings()
    r = Report()
    print("AICTE voice agent - setup check")
    configured = check_env(s, r)
    if configured:
        check_twilio(s, r)
    else:
        print("\n2. Twilio: skipped until section 1 is fixed")
    check_service(s, r)
    check_pages(s, r)
    print()
    if r.failures:
        print(f"{r.failures} problem(s) to fix before calls will work.")
        return 1
    print(
        "Ready. Open the dial pad, enter your number and press 'Call me now'."
        + (f" ({r.warnings} note(s) above.)" if r.warnings else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
