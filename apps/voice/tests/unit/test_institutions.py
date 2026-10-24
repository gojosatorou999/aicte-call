"""The institution directory: load an AICTE-style CSV, find colleges by what a caller says."""

from __future__ import annotations

import pytest

from mock_core.institutions import load_csv

# Fictional colleges only.
LONG = """Institute Name,State,District,Academic Year,Course,Intake,Status
Zorbia Institute of Technology,Karnataka,Mysuru,2025-26,B.Tech CSE,120,Approved
Zorbia Institute of Technology,Karnataka,Mysuru,2026-27,B.Tech CSE,180,Approved
Zorbia Institute of Technology,Karnataka,Mysuru,2026-27,B.Tech ECE,60,Approved
Zorbia Institute of Technology,Karnataka,Mysuru,2026-27,MBA,60,Closed
Quillmark College of Engineering,Kerala,Kochi,2026-27,B.Tech Civil,60,Approved
Quillmark College of Pharmacy,Kerala,Thrissur,2026-27,B.Pharm,100,Approved
Mythra Gandhi Institute of Science,Telangana,Hyderabad,2026-27,B.Tech IT,90,Approved
Plainview Polytechnic,Punjab,Ludhiana,2026-27,Diploma Mechanical,60,Approved
"""


@pytest.fixture
def directory(tmp_path):
    f = tmp_path / "list.csv"
    f.write_text(LONG, encoding="utf-8-sig")
    return load_csv(f)


def names(d, said):
    return [i.name for i in d.search(said)]


def test_groups_rows_into_institutions_with_latest_year(directory):
    assert len(directory) == 5  # Zorbia, two Quillmarks, Mythra, Plainview
    zorbia = directory.search("Zorbia Institute of Technology")[0]
    a = directory.approval(zorbia.id, None)
    assert a["academic_year"] == "2026-27" and a["approved"] is True
    assert a["courses"] == [
        {"name": "B.Tech CSE", "intake": 180},
        {"name": "B.Tech ECE", "intake": 60},
    ]
    older = directory.approval(zorbia.id, "2025-26")
    assert older["courses"] == [{"name": "B.Tech CSE", "intake": 120}]
    assert (
        directory.approval(zorbia.id, "1999-00")["academic_year"] == "2026-27"
    )  # falls back to latest
    assert directory.approval("missing", None) is None


def test_exact_partial_and_lowercase_names(directory):
    assert names(directory, "Zorbia Institute of Technology") == ["Zorbia Institute of Technology"]
    assert names(directory, "zorbia") == ["Zorbia Institute of Technology"]
    assert names(directory, "plainview polytechnic") == ["Plainview Polytechnic"]


def test_speech_recognition_errors_still_find_the_college(directory):
    assert names(directory, "Zorbea Institute of Technology") == ["Zorbia Institute of Technology"]
    assert names(directory, "Mithra Gandhi Institute") == ["Mythra Gandhi Institute of Science"]
    assert names(directory, "plain view polytechnic") == ["Plainview Polytechnic"]


def test_acronyms_are_understood(directory):
    assert names(directory, "ZIT") == ["Zorbia Institute of Technology"]
    assert names(directory, "M G I S") == ["Mythra Gandhi Institute of Science"]


def test_a_shared_name_returns_every_candidate_and_a_city_narrows_it(directory):
    both = names(directory, "Quillmark")
    assert sorted(both) == ["Quillmark College of Engineering", "Quillmark College of Pharmacy"]
    assert names(directory, "Quillmark college Kochi") == ["Quillmark College of Engineering"]


def test_unknown_or_generic_names_find_nothing(directory):
    assert names(directory, "Nonexistent University") == []
    assert names(directory, "college") == []
    assert names(directory, "") == []
    assert names(directory, "Xyzzy Plugh Institute") == []


def test_a_closed_programme_is_not_listed_as_approved(directory):
    zorbia = directory.search("zorbia")[0]
    assert "MBA" not in [c["name"] for c in directory.approval(zorbia.id, None)["courses"]]


def test_a_list_with_one_row_per_institute_and_aliases(tmp_path):
    f = tmp_path / "small.csv"
    f.write_text(
        "college_name,city,aliases\nVelmora Engineering College,Salem,VEC;Velmora\n",
        encoding="utf-8",
    )
    d = load_csv(f)
    assert names(d, "VEC") == ["Velmora Engineering College"]
    a = d.approval(d.search("velmora")[0].id, None)
    assert a["approved"] is True and a["courses"] == []


def test_a_file_without_a_name_column_is_refused_with_the_accepted_names(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no institute-name column"):
        load_csv(f)


def test_a_big_list_is_searched_quickly(tmp_path):
    import time

    rows = ["Institute Name,State,City,Year,Course,Intake"]
    stems = ["Alder", "Bramble", "Cobalt", "Dunmore", "Elmstead", "Fairlea", "Glenrose", "Hawkmoor"]
    for n in range(14000):
        rows.append(
            f"{stems[n % 8]}{n} College of Engineering,State{n % 30},City{n % 400},2026-27,CSE,60"
        )
    f = tmp_path / "big.csv"
    f.write_text("\n".join(rows), encoding="utf-8")
    d = load_csv(f)
    start = time.perf_counter()
    d.search("Glenrose 7 College of Engineering")
    assert time.perf_counter() - start < 2.0
