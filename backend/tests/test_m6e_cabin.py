"""M6e: comfort-aware cabin control (Module P) against the SIMULATED cabin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.cabin import controller as cab
from am.cabin.comfort import ComfortHit
from am.cabin.envelope import load_envelope
from am.crew_seed import seed_crew
from am.db.models_cabin import EnvAction, EnvAlert, EnvControl
from tests.conftest import sign_in


class QuietCabin(cab.SimulatedCabin):
    """No sensor noise, so verification is exact in tests."""

    def read(self, control):
        control.last_reading = round(control.setpoint + (control.drift or 0.0), 2)
        return control.last_reading


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        cab.ensure_zones(s)
        s.commit()
    a, b = TestClient(client.app), TestClient(client.app)
    return (a, sign_in(a, role="astronaut", username="ast001").astronaut_id,
            b, sign_in(b, role="astronaut", username="ast002").astronaut_id)


def _ctl(client, astronaut_id, variable):
    with client.app.state.ctx.session_factory() as s:
        return cab.quarters_control(s, astronaut_id, variable).id


def _signal(client, astronaut_id, variable, direction):
    with client.app.state.ctx.session_factory() as s:
        line = cab.handle_signal(s, astronaut_id, None, ComfortHit(variable, direction, "test"), "en", QuietCabin())
        s.commit()
        return line


# ------------------------------------------------------------------ controller rules
def test_bounded_steps_envelope_and_rate(client, crew):
    _, a, _, _ = crew
    lim = load_envelope().variables["temperature"]
    line = _signal(client, a, "temperature", "up")
    assert line.startswith("It felt cold, so I raised your quarters from 22 to 23 °C")
    with client.app.state.ctx.session_factory() as s:
        c = cab.quarters_control(s, a, "temperature")
        assert c.setpoint == lim.default + lim.step
    assert _signal(client, a, "temperature", "up")  # 24: still within 2 degC per hour
    assert _signal(client, a, "temperature", "up") is None  # rate limit
    with client.app.state.ctx.session_factory() as s:
        kinds = [(x.kind, x.rule_id) for x in s.scalars(select(EnvAction).order_by(EnvAction.id))]
        assert kinds[-1] == ("REFUSED", "P-RATE") and kinds.count(("AUTO", "P-COMFORT-STEP")) == 2
        c = cab.quarters_control(s, a, "temperature")
        c.setpoint = lim.max  # at the limit: no step beyond the envelope
        s.query(EnvAction).delete()
        s.commit()
    assert _signal(client, a, "temperature", "up") is None
    with client.app.state.ctx.session_factory() as s:
        assert s.scalars(select(EnvAction).order_by(EnvAction.id.desc())).first().rule_id == "P-ENVELOPE"


def test_fault_pauses_autonomy_and_raises_an_alert(client, crew):
    _, a, _, _ = crew
    with client.app.state.ctx.session_factory() as s:
        cab.quarters_control(s, a, "lighting").fault = "actuator"
        s.commit()
    assert _signal(client, a, "lighting", "down") is None
    with client.app.state.ctx.session_factory() as s:
        c = cab.quarters_control(s, a, "lighting")
        assert c.mode == "PAUSED" and c.setpoint == load_envelope().variables["lighting"].default
        assert s.scalars(select(EnvAlert).where(EnvAlert.rule_id == "P-FAULT-ACT")).first().severity == "WARNING"
        with pytest.raises(cab.CabinError):  # cannot resume while the fault is active
            cab.set_mode(s, c, "AUTONOMOUS", actor="x")


def test_manual_control_wins_until_autonomy_is_re_enabled(client, crew):
    me, a, _, _ = crew
    cid = _ctl(client, a, "temperature")
    r = me.post(f"/api/v1/cabin/controls/{cid}/set", json={"value": 20})
    assert r.status_code == 200 and r.json()["kind"] == "MANUAL"
    assert _signal(client, a, "temperature", "up") is None  # refused: MANUAL
    with client.app.state.ctx.session_factory() as s:
        assert s.scalars(select(EnvAction).order_by(EnvAction.id.desc())).first().rule_id == "P-MODE"
    assert me.post(f"/api/v1/cabin/controls/{cid}/mode", json={"mode": "AUTONOMOUS"}).status_code == 200
    assert _signal(client, a, "temperature", "up")
    assert me.post(f"/api/v1/cabin/controls/{cid}/set", json={"value": 40}).status_code == 409  # hard limit


def test_pressure_is_doctor_only_and_needs_a_reason_outside_the_band(client, crew):
    me, a, _, _ = crew
    with client.app.state.ctx.session_factory() as s:
        pid = cab.cabin_control(s).id
    assert me.post(f"/api/v1/cabin/controls/{pid}/set", json={"value": 101.2}).status_code in (403, 404)
    lim = load_envelope().variables["pressure"]
    assert client.post(f"/api/v1/cabin/controls/{pid}/set", json={"value": lim.auto_max + 0.5}).status_code == 409
    ok = client.post(f"/api/v1/cabin/controls/{pid}/set", json={"value": lim.auto_max + 0.5,
                                                               "reason": "Approved for a leak-check procedure"})
    assert ok.status_code == 200 and ok.json()["kind"] == "EXCEPTION" and ok.json()["reason"].startswith("Approved")
    # a complaint never moves pressure: it is reported for review
    line = _signal(client, a, "pressure", "report")
    assert "not changed" in line
    with client.app.state.ctx.session_factory() as s:
        assert s.scalars(select(EnvAlert).where(EnvAlert.rule_id == "P-PRESSURE-REPORT")).first() is not None


def test_one_astronaut_cannot_touch_another_astronauts_quarters(client, crew):
    me, a, other, b = crew
    cid = _ctl(client, a, "temperature")
    assert other.post(f"/api/v1/cabin/controls/{cid}/set", json={"value": 21}).status_code == 403
    assert other.post(f"/api/v1/cabin/controls/{cid}/mode", json={"mode": "PAUSED"}).status_code == 403
    assert other.get("/api/v1/cabin/state", params={"astronaut_id": a}).status_code == 403
    assert me.get("/api/v1/cabin/overview").status_code == 403


# ------------------------------------------------------------------ integration: complaint -> signal -> action -> log
def test_complaint_in_chat_adjusts_quarters_and_can_be_undone(client, crew):
    me, a, _, _ = crew
    conv = me.post("/api/v1/companion/conversations", json={"astronaut_id": a}).json()
    reply = me.post(f"/api/v1/companion/conversations/{conv['id']}/messages", json={"content": "Bu gece çok üşüdüm"}).json()
    assert "Üşüdüğünü söyledin" in reply["assistant_message"]["content"]
    state = me.get("/api/v1/cabin/state", params={"astronaut_id": a}).json()
    temp = next(c for c in state["controls"] if c["variable"] == "temperature" and c["astronaut_id"] == a)
    assert temp["setpoint"] == 23
    act = next(x for x in state["actions"] if x["kind"] == "AUTO")
    assert act["signal_quote"] == "Bu gece çok üşüdüm" and act["rule_id"] == "P-COMFORT-STEP"
    # feedback and undo
    assert me.post(f"/api/v1/cabin/actions/{act['id']}/feedback", json={"helped": True}).json()["feedback"] == "helped"
    undo = me.post(f"/api/v1/cabin/actions/{act['id']}/revert")
    assert undo.status_code == 200 and undo.json()["after"] == 22
    assert me.post(f"/api/v1/cabin/actions/{act['id']}/revert").status_code == 409  # only once
    # the doctor sees all of it on the Environment screen
    ov = client.get("/api/v1/cabin/overview").json()
    assert {x["kind"] for x in ov["actions"]} >= {"AUTO", "REVERT"}


def test_drift_outside_the_envelope_pauses_and_alerts(client, crew):
    _, a, _, _ = crew
    with client.app.state.ctx.session_factory() as s:
        pid = cab.cabin_control(s).id
    r = client.post("/api/test/cabin/drift", json={"control_id": pid, "offset": 2.5})
    assert r.status_code == 200 and r.json()["alerts_raised"]
    with client.app.state.ctx.session_factory() as s:
        c = s.get(EnvControl, pid)
        assert c.mode == "PAUSED"
        al = s.scalars(select(EnvAlert).where(EnvAlert.rule_id == "P-OUT-OF-ENVELOPE")).first()
        assert al.severity in ("WARNING", "CRITICAL")
    ack = client.post(f"/api/v1/cabin/alerts/{al.id}/acknowledge")
    assert ack.json()["status"] == "acknowledged"


def test_test_console_cabin_controls_are_scoped(client, crew):
    """Fault and drift injection follow the same ownership rule as the main cabin API."""
    me, a, other, b = crew
    mine = _ctl(client, a, "temperature")
    with client.app.state.ctx.session_factory() as s:
        pressure = cab.cabin_control(s).id
    assert other.post("/api/test/cabin/fault", json={"control_id": mine, "fault": "sensor"}).status_code == 403
    assert other.post("/api/test/cabin/drift", json={"control_id": mine, "offset": 3}).status_code == 403
    assert me.post("/api/test/cabin/drift", json={"control_id": pressure, "offset": 1}).status_code in (403, 404)
    assert me.post("/api/test/cabin/fault", json={"control_id": mine, "fault": "none"}).status_code == 200
    assert client.post("/api/test/cabin/drift", json={"control_id": pressure, "offset": 0}).status_code == 200


@pytest.mark.parametrize("text, variable, direction", [
    ("Odam çok soğuk", "temperature", "up"),
    ("Odayı ısıtır mısın", "temperature", "up"),
    ("The room is too cold", "temperature", "up"),
    ("Oda çok sıcak, bunaldım", "temperature", "down"),
    ("Burası boğucu", "temperature", "down"),
    ("My room is really hot", "temperature", "down"),
    ("Işık gözümü yakıyor", "lighting", "down"),
    ("Lambalar çok parlak", "lighting", "down"),
    ("The light hurts my eyes", "lighting", "down"),
    ("Can you dim the lights", "lighting", "down"),
    ("Oda çok karanlık", "lighting", "up"),
    ("Işık yetersiz", "lighting", "up"),
    ("It's a bit dark in here", "lighting", "up"),
])
def test_room_discomfort_phrases_map_to_a_room_condition(text, variable, direction):
    from am.cabin.comfort import extract_comfort
    assert [(h.variable, h.direction) for h in extract_comfort(text)] == [(variable, direction)]


@pytest.mark.parametrize("text", ["Soğuk su içtim", "Sıcak çay içtim", "Oda sıcak değil", "The room is not cold",
                                  "Işık gözümü yormuyor", "I'm not cold"])
def test_drinks_and_negations_do_not_change_the_room(text):
    from am.cabin.comfort import extract_comfort
    assert extract_comfort(text) == []
