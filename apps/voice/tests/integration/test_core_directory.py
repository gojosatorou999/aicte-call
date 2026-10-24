"""The mock core answers from a supplied AICTE list, and from the demo colleges without one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from mock_core import app as core_app
from mock_core.institutions import load_csv

P = "/internal/voice"


@pytest.fixture
def core():
    token = get_settings().internal_service_token.get_secret_value()
    return TestClient(core_app.app, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def fresh_directory(monkeypatch):
    monkeypatch.setattr(core_app, "_directory", None)


def test_without_a_list_only_the_demo_colleges_exist(core):
    found = core.get(f"{P}/public/institutions", params={"name": "Sri"}).json()["results"]
    assert len(found) == 2
    assert core.get(f"{P}/public/institutions", params={"name": "Zorbia"}).json()["results"] == []


def test_with_a_list_any_listed_college_is_found_and_answered(core, tmp_path, monkeypatch):
    f = tmp_path / "list.csv"
    f.write_text(
        "Institute Name,State,District,Academic Year,Course,Intake\n"
        "Zorbia Institute of Technology,Karnataka,Mysuru,2026-27,B.Tech CSE,180\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core_app, "_directory", load_csv(f))
    found = core.get(f"{P}/public/institutions", params={"name": "Zorbea institute"}).json()[
        "results"
    ]
    assert [i["name"] for i in found] == ["Zorbia Institute of Technology"]
    a = core.get(f"{P}/public/institutions/{found[0]['id']}/approval").json()
    assert a["approved"] is True and a["courses"] == [{"name": "B.Tech CSE", "intake": 180}]
    assert core.get(f"{P}/public/institutions/nope/approval").status_code == 404
