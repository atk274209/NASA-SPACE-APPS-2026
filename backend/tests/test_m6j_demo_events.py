"""M6j: SIMULATED demo space events act on AMC's real subsystems and record AMC's reaction."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.crew_seed import seed_crew
from am.db.base import clock_offset, set_clock_offset
from am.db.models_cabin import EnvControl
from am.db.models_m6b import ReportFlag
from am.demo.events import KINDS, resolve_due
from tests.conftest import sign_in


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    return me, sign_in(me, role="astronaut", username="ast001").astronaut_id


@pytest.mark.parametrize("kind", [k.code for k in KINDS])
def test_every_event_kind_runs_and_says_what_am_did(client, crew, kind):
    ev = client.post("/api/v1/demo/events", json={"kind": kind})
    assert ev.status_code == 201, ev.text
    ev = ev.json()
    assert ev["status"] == "active" and ev["summary"].startswith("SIMULATED") and ev["reactions"]


def test_overheating_pauses_autonomy_raises_an_alert_and_clears_itself(client, crew):
    ev = client.post("/api/v1/demo/events", json={"kind": "quarters_heat"}).json()
    assert any("alert" in r for r in ev["reactions"]) and any("paused" in r for r in ev["reactions"])
    assert any(r["type"] == "env_alert" for r in ev["refs"])
    set_clock_offset(clock_offset() + timedelta(minutes=10))
    with client.app.state.ctx.session_factory() as s:
        done = resolve_due(s)
        s.commit()
        assert [e.id for e in done] == [ev["id"]]
        c = s.scalars(select(EnvControl).where(EnvControl.drift != 0)).first()
        assert c is None
        assert any("resumed" in r for r in done[0].reactions)


def test_urgent_symptom_waits_for_the_doctor(client, crew):
    ev = client.post("/api/v1/demo/events", json={"kind": "urgent_symptom"}).json()
    assert any("approval" in r for r in ev["reactions"])
    with client.app.state.ctx.session_factory() as s:
        flag = s.scalars(select(ReportFlag)).one()
        assert flag.rule_level == "red" and flag.approval_status == "awaiting"


def test_crew_sees_crew_wide_and_own_events_only_and_cannot_trigger(client, crew):
    me, a = crew
    client.post("/api/v1/demo/events", json={"kind": "solar_particle_event"})
    for _ in range(6):
        client.post("/api/v1/demo/events", json={"kind": "symptom_report"})
    seen = me.get("/api/v1/demo/events").json()["events"]
    assert any(e["kind"] == "solar_particle_event" for e in seen)
    assert all(e["astronaut_id"] in (None, a) for e in seen)
    assert me.post("/api/v1/demo/events", json={}).status_code == 403
    assert client.post("/api/v1/demo/events", json={"kind": "nope"}).status_code == 422


def test_overheating_alerts_even_when_the_room_was_set_cool(client, crew):
    from am.cabin.controller import ensure_zones

    with client.app.state.ctx.session_factory() as s:
        ensure_zones(s)
        for c in s.scalars(select(EnvControl).where(EnvControl.variable == "temperature")):
            c.setpoint = 19.0
        s.commit()
    ev = client.post("/api/v1/demo/events", json={"kind": "quarters_heat"}).json()
    assert any("alert" in r for r in ev["reactions"]), ev["reactions"]
