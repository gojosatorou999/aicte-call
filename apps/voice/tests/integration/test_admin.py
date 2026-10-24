"""The admin dashboard and CSV export, fed by real (simulated) calls."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from pydantic import SecretStr

from app import dial
from app.api import routes_admin
from app.config import get_settings
from tests.phone import ForbiddenLLM, open_graph
from tests.phone import dial as place_call

AUTH = ("admin", "s3cret-pass")


@pytest.fixture(autouse=True)
def fresh_login_limits(monkeypatch):
    monkeypatch.setattr(routes_admin, "_failed_logins", dial.SlidingWindow(limit=10, window=60))


@pytest.fixture
def server():
    opened = []

    def _open(*, password="s3cret-pass", **kw):
        client, core = open_graph(admin_password=SecretStr(password), **kw)
        opened.append(client)
        return client

    ForbiddenLLM.calls = 0
    yield _open
    for c in opened:
        c.__exit__(None, None, None)


def make_calls(client):
    """One college check, one failed lookup, one complaint."""
    phone = place_call(client, "+919999999999", call_sid="CA-check")
    phone.say("Sri Venkateswara")
    phone.say("goodbye")
    phone = place_call(client, "+918888888888", call_sid="CA-missing")
    phone.say("Nonexistent University")
    phone.say("Nonexistent University")
    phone.say("goodbye")
    phone = place_call(client, "+917777777777", call_sid="CA-complaint")
    phone.say("I want to file a complaint")
    phone.say("Green Valley College")
    phone.say("They are running courses without any approval and taking fees")
    phone.say("yes")


# -- access ---------------------------------------------------------------------------------------
def test_the_dashboard_is_off_until_a_password_is_set(server):
    client = server(password="")
    for path in ("/admin", "/admin/api/calls", "/admin/export.csv"):
        r = client.get(path, auth=AUTH)
        assert r.status_code == 503 and "ADMIN_PASSWORD" in r.json()["detail"]


def test_it_asks_for_a_password_and_rejects_wrong_ones(server):
    client = server()
    r = client.get("/admin")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/admin", auth=("admin", "wrong")).status_code == 401
    assert client.get("/admin", auth=("someone", AUTH[1])).status_code == 401
    ok = client.get("/admin", auth=AUTH)
    assert ok.status_code == 200 and "Call reports" in ok.text and "Export CSV" in ok.text


def test_malformed_credentials_are_just_unauthorised(server):
    client = server()
    assert client.get("/admin", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401
    assert client.get("/admin", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_repeated_wrong_passwords_are_throttled(server):
    client = server()
    for _ in range(10):
        assert client.get("/admin", auth=("admin", "nope")).status_code == 401
    assert client.get("/admin", auth=("admin", "nope")).status_code == 429
    assert client.get("/admin", auth=AUTH).status_code == 429  # even the right one waits it out


# -- what it shows ------------------------------------------------------------------------------------
def test_calls_appear_with_what_was_asked_and_the_result(server):
    client = server()
    make_calls(client)
    data = client.get("/admin/api/calls", auth=AUTH).json()
    by_id = {c["call_id"]: c for c in data["calls"]}
    assert set(by_id) == {"CA-check", "CA-missing", "CA-complaint"}

    check = by_id["CA-check"]
    assert check["college_said"] == "Sri Venkateswara"
    assert check["college_matched"] == "Sri Venkateswara Institute of Technology"
    assert check["result"] == "approved" and check["intent"] == "college_check"
    assert check["outcome"] == "completed" and check["direction"] == "inbound"
    assert check["phone_masked"].endswith("9999") and check["phone_masked"].startswith("*")

    assert by_id["CA-missing"]["result"] == "not_found | not_found"
    assert by_id["CA-missing"]["outcome"] == "completed"

    complaint = by_id["CA-complaint"]
    assert complaint["intent"] == "complaint" and complaint["complaint_ref"] == "GRV-2026-0001"
    assert (
        complaint["college_said"] == "Green Valley College"
        and complaint["result"] == "complaint_filed"
    )

    assert data["summary"] == {
        "calls": 3, "college_checks": 2, "approved": 1, "not_approved": 0, "not_found": 1, "complaints": 1,
    }  # fmt: skip
    assert "transcript" not in check  # the list stays light; the detail has it


def test_one_call_shows_its_transcript(server):
    client = server()
    make_calls(client)
    detail = client.get("/admin/api/calls/CA-check", auth=AUTH).json()
    assert "Agent: Please say the name of the college." in detail["transcript"]
    assert "Caller: Sri Venkateswara" in detail["transcript"]
    assert client.get("/admin/api/calls/CA-nope", auth=AUTH).status_code == 404


def test_a_call_in_progress_is_listed_before_it_ends(server):
    client = server()
    place_call(client, call_sid="CA-now").say("Sri Venkateswara")  # still on the line
    [row] = client.get("/admin/api/calls", auth=AUTH).json()["calls"]
    assert row["call_id"] == "CA-now" and row["outcome"] == "in_progress"


# -- export ----------------------------------------------------------------------------------------------
def test_export_downloads_a_csv_with_every_call(server):
    client = server()
    make_calls(client)
    r = client.get("/admin/export.csv", auth=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="calls-')
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    assert (
        header[0] == "Call ID"
        and "College the caller said" in header
        and "Complaint reference" in header
    )
    assert {row[0] for row in body} == {"CA-check", "CA-missing", "CA-complaint"}
    assert any("GRV-2026-0001" in row for row in body)


def test_the_csv_file_on_disk_matches_and_never_holds_a_full_phone_number(server):
    client = server()
    make_calls(client)
    on_disk = Path(get_settings().call_log_csv).read_bytes().decode("utf-8-sig")
    assert on_disk == client.get("/admin/export.csv", auth=AUTH).content.decode(
        "utf-8-sig"
    )  # byte for byte
    for number in ("919999999999", "9999999999", "918888888888", "917777777777"):
        assert number not in on_disk


def test_what_a_caller_says_cannot_become_a_spreadsheet_formula(server):
    client = server()
    phone = place_call(client, call_sid="CA-evil")
    phone.say('=HYPERLINK("http://evil.example","click")')
    body = client.get("/admin/export.csv", auth=AUTH).content.decode("utf-8-sig")
    assert "'=HYPERLINK" in body
    assert ",=HYPERLINK" not in body and "\n=HYPERLINK" not in body


def test_the_dashboard_page_never_writes_caller_text_as_html():
    from app.api.routes_admin import _PAGE

    assert "innerHTML" not in _PAGE  # everything is placed with textContent
