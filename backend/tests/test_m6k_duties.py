"""M6k: AMC shares out the day's duties by each astronaut's energy and morale."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from am.crew_seed import seed_crew
from am.duties.service import STATION_TASKS
from tests.conftest import sign_in
from tests.test_companion import FakeOllama, answer


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    return me, sign_in(me, role="astronaut", username="ast001").astronaut_id


def _checkin(client, a, **ratings):
    assert client.post("/api/test/checkins", json={"astronaut_id": a, "ratings": ratings}).status_code == 201


def _member(roster, a):
    return next(m for m in roster["crew"] if m["astronaut_id"] == a)


def test_every_station_task_is_assigned_once(client, crew):
    roster = client.get("/api/v1/duties/today").json()
    codes = [t["task_code"] for m in roster["crew"] for t in m["assignments"] if t["load"] > 0]
    codes += [t["code"] for t in roster["postponed"]]
    assert sorted(codes) == sorted(t.code for t in STATION_TASKS)
    assert "not a clinical measure" in roster["label"]


def test_rested_astronaut_gets_heavy_work_and_a_low_one_gets_a_light_day(client, crew):
    _, a = crew
    ids = [m["astronaut_id"] for m in client.get("/api/v1/duties/today").json()["crew"]]
    tired = next(i for i in ids if i != a)
    _checkin(client, a, mood=5, motivation=5, stress=1, fatigue=1, sleep_quality=5)
    _checkin(client, tired, mood=1, motivation=2, stress=5, fatigue=5, sleep_quality=1)
    roster = client.post("/api/v1/duties/today/replan").json()
    fresh, low = _member(roster, a), _member(roster, tired)
    assert fresh["band"] == "full" and fresh["energy"] == 100 and fresh["morale"] == 100
    assert any(t["load"] == 3 for t in fresh["assignments"])
    assert low["band"] == "light" and all(t["load"] <= 1 for t in low["assignments"])
    assert {t["task_code"] for t in low["assignments"]} >= {"call_home", "rest_block"}
    assert all(t["reason"] for t in low["assignments"])


def test_severe_symptom_means_light_duties(client, crew):
    _, a = crew
    _checkin(client, a, mood=5, motivation=5, stress=1, fatigue=1, sleep_quality=5)
    client.post("/api/test/symptoms", json={"astronaut_id": a, "symptom_code": "back_pain", "severity": "severe"})
    me = _member(client.post("/api/v1/duties/today/replan").json(), a)
    assert me["band"] == "light" and any("severe" in r for r in me["reasons"])


def test_astronaut_sees_only_own_duties_with_a_note_in_their_shoes(client, crew):
    me, a = crew
    client.get("/api/v1/duties/today")
    client.app.state.ctx.llm = FakeOllama([answer("You slept well, so today you get the robotics session.")]).client()
    mine = me.get("/api/v1/duties/today").json()
    assert [m["astronaut_id"] for m in mine["crew"]] == [a]
    assert mine["crew"][0]["note_by"] == "llm" and "robotics" in mine["crew"][0]["note"]
    assert me.post("/api/v1/duties/today/replan").status_code == 403
    t = mine["crew"][0]["assignments"][0]
    assert me.post(f"/api/v1/duties/assignments/{t['id']}/done").json()["status"] == "done"
    other = next(m for m in client.get("/api/v1/duties/today").json()["crew"] if m["astronaut_id"] != a)
    assert me.post(f"/api/v1/duties/assignments/{other['assignments'][0]['id']}/done").status_code == 404


def test_the_private_diary_is_never_used(client, crew):
    me, a = crew
    day = me.get("/api/v1/diary/today").json()["day"]["id"]
    me.post(f"/api/v1/diary/days/{day}/messages", json={"content": "I feel hopeless and exhausted."})
    m = _member(client.post("/api/v1/duties/today/replan").json(), a)
    assert m["energy"] is None and m["morale"] is None and m["band"] == "steady"


def test_nobody_on_a_light_day_is_overloaded_heavy_work_is_postponed(client, crew):
    for m in client.get("/api/v1/duties/today").json()["crew"]:
        _checkin(client, m["astronaut_id"], mood=1, motivation=1, stress=5, fatigue=5, sleep_quality=1)
    roster = client.post("/api/v1/duties/today/replan").json()
    assert all(m["band"] == "light" for m in roster["crew"])
    assert all(t["load"] <= 1 for m in roster["crew"] for t in m["assignments"])
    assert {t["code"] for t in roster["postponed"]} >= {"eva_prep", "robotics"}
