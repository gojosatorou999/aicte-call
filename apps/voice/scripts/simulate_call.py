"""A console telephone for the voice service - no Twilio account needed.

It plays the part of Twilio: it "dials" the service, prints what the service
would say aloud, and sends back whatever you type as the caller's answer.

    python scripts/simulate_call.py --from +910000000002

Type a sentence to *speak* it, `#123456` to press keypad digits, `/silence`
to say nothing, `/quit` to hang up. Start the mock core and the voice service
first (see the README) with TWILIO_SIGNATURE_VALIDATION_ENABLED=false.

Mock-core phone numbers: ...0001 institution, ...0002 officer (PIN 246810),
...0003 faculty (OTP 123456); any other number is a member of the public.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def show(xml: str) -> tuple[str | None, str | None, bool]:
    """Print the call's side of the conversation. Returns (gather action, redirect, hung_up)."""
    root = ET.fromstring(xml)
    for say in root.iter("Say"):
        print(f"  \N{ROBOT FACE}  {say.text}")
    if root.find("Connect/ConversationRelay") is not None:
        print("  [call handed to the LLM relay - not simulated here]")
        return None, None, True
    gather = root.find("Gather")
    redirect = root.find("Redirect")
    hung_up = root.find("Hangup") is not None
    if gather is not None:
        mode = gather.get("input")
        extra = f", {gather.get('numDigits')} digits then # " if gather.get("numDigits") else ""
        print(f"      (waiting for: {mode}{extra})")
    if hung_up:
        print("  \N{BLACK TELEPHONE}  [call ended]")
    return (
        gather.get("action") if gather is not None else None,
        redirect.text if redirect is not None else None,
        hung_up,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://localhost:8080", help="voice service base URL")
    ap.add_argument("--from", dest="caller", default="+919999999999", help="caller phone number")
    ap.add_argument("--sid", default="CASIM0001", help="fake CallSid")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with httpx.Client(base_url=args.url, timeout=30) as http:

        def post(url: str, **form: str) -> str:
            r = http.post(path_of(url), data={"CallSid": args.sid, "From": args.caller, **form})
            r.raise_for_status()
            return r.text

        print(f"Dialling {args.url} as {args.caller} ...\n")
        action, redirect, ended = show(post("/voice/connect?lang=en"))
        while not ended:
            try:
                line = input("you> ").strip()
            except EOFError:
                break
            if line == "/quit":
                break
            if line == "/silence":
                if not redirect:
                    print("      (nothing to wait on)")
                    continue
                xml = post(redirect)
            elif not action:
                print("      (the service is not waiting for an answer)")
                continue
            elif line.startswith("#"):
                xml = post(action, Digits=line[1:])
            else:
                xml = post(action, SpeechResult=line, Confidence="0.9")
            action, redirect, ended = show(xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
