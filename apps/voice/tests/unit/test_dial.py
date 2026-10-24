from __future__ import annotations

import pytest
from twilio.base.exceptions import TwilioRestException

from app import dial
from app.config import get_settings
from tests.helpers import FakeCalls


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+91 98765 43210", "+919876543210"),
        ("98765 43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91-98765-43210", "+919876543210"),
        ("(+91) 98765 43210", "+919876543210"),
        ("+14155552671", "+14155552671"),
    ],
)
def test_normalize_number(raw, expected):
    assert dial.normalize_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12345", "+0123456789", "+91", "98765", "+" + "9" * 20])
def test_invalid_numbers_are_rejected_not_repaired(raw):
    with pytest.raises(dial.DialError) as ei:
        dial.normalize_number(raw)
    assert ei.value.status == 400


def guard(**overrides):
    s = get_settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return dial.DialGuard(s)


def test_only_allowed_country_prefixes_can_be_dialled():
    g = guard()
    g.check("+919876543210", "1.1.1.1")
    with pytest.raises(dial.DialError) as ei:
        g.check("+14155552671", "1.1.1.1")
    assert ei.value.status == 403 and "+91" in ei.value.detail
    guard(dial_allowed_prefixes="").check("+14155552671", "1.1.1.1")  # empty = any country


def test_per_number_limit_then_other_numbers_still_work():
    g = guard(dial_per_number_per_hour=2)
    for _ in range(2):
        g.check("+919876543210", "1.1.1.1")
    with pytest.raises(dial.DialError) as ei:
        g.check("+919876543210", "2.2.2.2")
    assert ei.value.status == 429
    g.check("+919876543211", "2.2.2.2")


def test_per_address_and_global_limits():
    g = guard(dial_per_ip_per_hour=2, dial_per_number_per_hour=50)
    g.check("+919000000001", "9.9.9.9")
    g.check("+919000000002", "9.9.9.9")
    with pytest.raises(dial.DialError) as ei:
        g.check("+919000000003", "9.9.9.9")
    assert "connection" in ei.value.detail
    g2 = guard(dial_global_per_day=1, dial_per_ip_per_hour=50)
    g2.check("+919000000001", "1.1.1.1")
    with pytest.raises(dial.DialError) as ei:
        g2.check("+919000000002", "2.2.2.2")
    assert "daily" in ei.value.detail


def test_agent_cannot_be_asked_to_call_itself():
    g = guard(twilio_voice_number="+919999900000")
    with pytest.raises(dial.DialError):
        g.check("+919999900000", "1.1.1.1")


def dialer_with(calls: FakeCalls, **settings):
    s = get_settings()
    s.twilio_account_sid, s.twilio_voice_number = "ACtest", "+15005550006"
    s.twilio_auth_token = type(s.twilio_auth_token)("tok")
    for k, v in settings.items():
        setattr(s, k, v)
    return dial.Dialer(s, client_factory=lambda sid, tok: type("C", (), {"calls": calls})())


async def test_dialer_places_the_call_with_the_expected_parameters():
    calls = FakeCalls()
    sid, status = await dialer_with(calls).place(
        to="+919876543210",
        url="https://x.ngrok-free.dev/voice/outbound-twiml?d=abc",
        status_callback="https://x.ngrok-free.dev/voice/status",
    )
    assert (sid, status) == ("CAfake1", "queued")
    sent = calls.created[0]
    assert sent["to"] == "+919876543210" and sent["from_"] == "+15005550006"
    assert sent["url"].endswith("/voice/outbound-twiml?d=abc") and sent["method"] == "POST"
    assert "completed" in sent["status_callback_event"] and sent["timeout"] == 30
    assert (
        "machine_detection" not in sent
    )  # a person asked for this call: no answering-machine wait


def test_dialer_reports_what_is_missing():
    s = get_settings()
    s.twilio_account_sid = ""
    msg = dial.Dialer(s).configured()
    assert msg and "TWILIO_ACCOUNT_SID" in msg and "TWILIO_VOICE_NUMBER" in msg
    assert "TWILIO_AUTH_TOKEN" in msg


@pytest.mark.parametrize(
    ("code", "status", "needle"),
    [
        (21608, 403, "isn't verified"),
        (21211, 400, "valid phone number"),
        (21215, 403, "Geo permissions"),
        (20003, 502, "credentials"),
        (99999, 502, "could not place"),
    ],
)
async def test_twilio_errors_become_friendly_messages(code, status, needle):
    err = TwilioRestException(status=400, uri="/Calls", msg="boom", code=code)
    with pytest.raises(dial.DialError) as ei:
        await dialer_with(FakeCalls(error=err)).place(
            to="+919876543210", url="u", status_callback="s"
        )
    assert ei.value.status == status and needle in ei.value.detail
    assert "boom" not in ei.value.detail  # raw Twilio text is never shown to the public


def test_store_tracks_status_by_sid_and_forgets_old_records():
    store = dial.DialStore()
    rec = store.create("public", "******3210")
    store.attach_sid(rec, "CA1", "queued")
    store.update_status("CA1", "ringing")
    assert store.by_sid("CA1").status == "ringing" and store.get(rec.dial_id) is rec
    store.update_status("CA-unknown", "x")  # ignored
    rec.created -= dial.RECORD_TTL_SEC + 1
    store.create("public", "x")
    assert store.get(rec.dial_id) is None and store.by_sid("CA1") is None


async def test_trial_account_refusing_extras_falls_back_to_the_basics_and_remembers():
    calls = FakeCalls(refuse_extras=True)
    d = dialer_with(calls)
    assert d.polls_status is False
    sid, _ = await d.place(
        to="+919876543210",
        url="https://x/voice/outbound-twiml?d=a",
        status_callback="https://x/voice/status",
    )
    assert sid == "CAfake1"
    assert (
        len(calls.attempts) == 2
    )  # the full request was refused (nothing dialled), then the basics
    basics = calls.created[0]
    assert set(basics) == {"to", "from_", "url"}
    assert d.polls_status is True

    await d.place(to="+919876543211", url="https://x/u", status_callback="https://x/s")
    assert len(calls.attempts) == 3  # remembered: one attempt this time, not two


async def test_a_paid_account_keeps_the_status_callbacks():
    calls = FakeCalls()
    d = dialer_with(calls)
    await d.place(to="+919876543210", url="https://x/u", status_callback="https://x/s")
    assert "status_callback" in calls.created[0] and d.polls_status is False


async def test_a_second_restriction_error_is_reported_not_retried_forever():

    class Stubborn(FakeCalls):
        def create(self, **kw):
            self.attempts.append(kw)
            from twilio.base.exceptions import TwilioRestException

            raise TwilioRestException(status=400, uri="/", msg="limited parameter access", code=0)

    d = dialer_with(Stubborn())
    with pytest.raises(dial.DialError) as ei:
        await d.place(to="+919876543210", url="u", status_callback="s")
    assert ei.value.status == 502 and "too limited" in ei.value.detail


async def test_status_is_read_from_twilio():
    calls = FakeCalls()
    calls.statuses["CAx"] = "ringing"
    assert await dialer_with(calls).fetch_status("CAx") == "ringing"
