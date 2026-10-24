from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest

from app import clock
from app.calllog import COLUMNS, STALE_AFTER, CallLog, safe_cell
from app.graph.state import CallState
from app.session import Persona, SessionClaims
from app.utils.transcript import TranscriptLine


@pytest.fixture
def log(tmp_path):
    return CallLog(str(tmp_path / "calls.db"), str(tmp_path / "calls.csv"))


def make_call(sid="CA1", **facts) -> CallState:
    call = CallState(
        call_sid=sid,
        claims=SessionClaims(sid, Persona.PUBLIC, "en", "ref"),
        token="t",
        runner=None,  # type: ignore[arg-type]
        direction="outbound",
        phone_masked="******9019",
    )
    call.facts.update(facts)
    call.transcript.append(TranscriptLine("assistant", "Please say the name of the college.", "t"))
    call.transcript.append(TranscriptLine("caller", "Sri Venkateswara", "t"))
    return call


LOOKUP = {
    "intent": "college_check",
    "lookups": [
        {
            "said": "Sri Venkateswara",
            "matched": "Sri Venkateswara Institute of Technology",
            "result": "approved",
            "courses": "B.Tech CSE, B.Tech ECE",
        }
    ],
}


def test_a_row_records_what_was_asked_and_what_came_of_it(log):
    log.upsert(make_call(**LOOKUP), ended=True)
    [row] = log.rows()
    assert row["call_id"] == "CA1" and row["direction"] == "outbound" and row["persona"] == "PUBLIC"
    assert row["college_said"] == "Sri Venkateswara"
    assert row["college_matched"] == "Sri Venkateswara Institute of Technology"
    assert row["result"] == "approved" and row["courses"] == "B.Tech CSE, B.Tech ECE"
    assert row["phone_masked"] == "******9019" and row["turns"] == 1
    assert "Caller: Sri Venkateswara" in row["transcript"] and row["outcome"] == "completed"


def test_updating_a_call_changes_its_row_and_keeps_its_start_time(log):
    call = make_call()
    log.upsert(call)
    started = log.rows()[0]["started_at"]
    call.facts.update(LOOKUP)
    log.upsert(call, ended=True)
    rows = log.rows()
    assert len(rows) == 1 and rows[0]["started_at"] == started and rows[0]["result"] == "approved"


def test_several_lookups_in_one_call_are_all_kept(log):
    facts = {
        "intent": "college_check",
        "lookups": [
            {"said": "Nowhere College", "matched": "", "result": "not_found"},
            {
                "said": "Sri Sai",
                "matched": "Sri Sai College of Engineering",
                "result": "approved",
                "courses": "",
            },
        ],
    }
    log.upsert(make_call(**facts))
    [row] = log.rows()
    assert row["college_said"] == "Nowhere College | Sri Sai"
    assert row["result"] == "not_found | approved"
    assert row["college_matched"] == "Sri Sai College of Engineering"


def test_a_complaint_is_recorded_with_its_reference_and_redacted_text(log):
    facts = {
        "intent": "complaint",
        "complaint_college": "Green Valley College",
        "complaint_ref": "GRV-2026-0001",
        "complaint_text": "They took fees. Call me on 9876543210 about it",
    }
    log.upsert(make_call(**facts), ended=True)
    [row] = log.rows()
    assert row["intent"] == "complaint" and row["college_said"] == "Green Valley College"
    assert row["complaint_ref"] == "GRV-2026-0001" and row["result"] == "complaint_filed"
    assert "9876543210" not in row["complaint_text"] and "3210" in row["complaint_text"]


def test_outcome_depends_on_whether_the_call_finished_and_how_long_ago(log, monkeypatch):
    log.upsert(make_call("CA-done"), ended=True)
    log.upsert(make_call("CA-live"))
    outcomes = {r["call_id"]: r["outcome"] for r in log.rows()}
    assert outcomes == {"CA-done": "completed", "CA-live": "in_progress"}

    later = clock.now() + STALE_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    outcomes = {r["call_id"]: r["outcome"] for r in log.rows()}
    assert outcomes == {"CA-done": "completed", "CA-live": "hung_up"}  # the caller hung up


def test_summary_counts(log):
    log.upsert(make_call("A", **LOOKUP), ended=True)
    log.upsert(
        make_call(
            "B",
            intent="college_check",
            lookups=[{"said": "x", "matched": "", "result": "not_found"}],
        )
    )
    log.upsert(make_call("C", intent="complaint", complaint_ref="GRV-1"), ended=True)
    s = log.summary()
    assert s == {
        "calls": 3,
        "college_checks": 2,
        "approved": 1,
        "not_approved": 0,
        "not_found": 1,
        "complaints": 1,
    }


def test_csv_file_is_kept_up_to_date_with_every_column_and_excel_friendly(log, tmp_path):
    log.upsert(make_call(**LOOKUP), ended=True)
    raw = (tmp_path / "calls.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM so Excel reads it correctly
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == [heading for _, heading in COLUMNS]
    assert len(rows) == 2 and rows[1][0] == "CA1" and "Sri Venkateswara" in rows[1]


def test_text_that_looks_like_a_formula_is_neutralised_in_the_csv(log):
    for text in ('=HYPERLINK("http://evil","x")', "+1+1", "-2", "@SUM(A1)", "\t=x"):
        assert safe_cell(text).startswith("'")
    assert safe_cell("normal") == "normal" and safe_cell(None) == "" and safe_cell(5) == "5"
    log.upsert(
        make_call(
            intent="college_check", lookups=[{"said": "=cmd|' /C calc'!A0", "result": "not_found"}]
        )
    )
    body = log.csv_text()
    assert "'=cmd" in body and ",=cmd" not in body


def test_the_raw_phone_number_is_never_stored(log, tmp_path):
    log.upsert(make_call(**LOOKUP))
    everything = (tmp_path / "calls.csv").read_text("utf-8-sig") + repr(log.rows())
    assert "919493579019" not in everything and "9493579019" not in everything


def test_a_report_failure_never_raises_into_the_call(tmp_path):
    bad = CallLog(str(tmp_path / "c.db"), str(tmp_path / "calls.csv"))
    (tmp_path / "calls.csv").unlink(missing_ok=True)
    (tmp_path / "calls.csv").mkdir()  # the CSV path is now a directory: writing it fails
    bad.upsert(make_call(**LOOKUP))  # must not raise
    assert len(bad.rows()) == 1  # the database still has the row


def test_calls_csv_exists_with_its_header_before_any_call(tmp_path):
    CallLog(str(tmp_path / "c.db"), str(tmp_path / "calls.csv"))
    first = (tmp_path / "calls.csv").read_text("utf-8-sig").splitlines()[0]
    assert first == ",".join(heading for _, heading in COLUMNS)
