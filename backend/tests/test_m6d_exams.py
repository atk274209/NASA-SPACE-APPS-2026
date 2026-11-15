"""M6d: routine examinations, adherence points (Module M)."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from am.crew_seed import seed_crew
from am.db.base import clock_offset, set_clock_offset
from tests.conftest import sign_in


def _crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    return me, sign_in(me, role="astronaut", username="ast001").astronaut_id


def test_exams_are_scheduled_due_and_overdue(client, tmp_path):
    me, a = _crew(client, tmp_path)
    data = me.get("/api/v1/exams", params={"astronaut_id": a}).json()
    assert data["mission_day"] == 1 and "DEMO" in data["label"]
    codes = {x["exam_code"] for x in data["exams"]}
    assert codes == {"blood_test", "urine_test", "reflex_test", "eye_exam"}
    assert all(x["state"] == "upcoming" for x in data["exams"])
    # result before it is due is refused
    early = next(x for x in data["exams"] if x["exam_code"] == "urine_test")
    assert me.post(f"/api/v1/exams/{early['id']}/result", json={"result_text": "x"}).status_code == 409
    set_clock_offset(clock_offset() + timedelta(days=3))  # mission day 4: urine (day 2) overdue, reflex (4) due
    states = {(x["exam_code"], x["due_day"]): x["state"] for x in me.get("/api/v1/exams", params={"astronaut_id": a}).json()["exams"]}
    assert states[("urine_test", 2)] == "overdue" and states[("reflex_test", 4)] == "due"
    assert states[("blood_test", 3)] == "overdue"


def test_entering_results_earns_adherence_points_once(client, tmp_path):
    me, a = _crew(client, tmp_path)
    set_clock_offset(clock_offset() + timedelta(days=3))
    exams = me.get("/api/v1/exams", params={"astronaut_id": a}).json()["exams"]
    reflex = next(x for x in exams if x["exam_code"] == "reflex_test" and x["due_day"] == 4)
    urine = next(x for x in exams if x["exam_code"] == "urine_test" and x["due_day"] == 2)
    r = me.post(f"/api/v1/exams/{reflex['id']}/result", json={"result_text": "Reflexes recorded per procedure: normal"})
    assert r.status_code == 200
    assert me.post(f"/api/v1/exams/{urine['id']}/result", json={"result_text": "Values entered"}).status_code == 200
    assert me.post(f"/api/v1/exams/{urine['id']}/result", json={"result_text": "again"}).status_code == 409
    pts = me.get("/api/v1/points", params={"astronaut_id": a}).json()
    assert pts["points"] == 25 + 10 and pts["level"] == 1  # on time 25, late 10
    assert "never depend on a health value" in pts["rule"]
    other = TestClient(client.app)
    sign_in(other, role="astronaut", username="ast002")
    assert other.post(f"/api/v1/exams/{reflex['id']}/result", json={"result_text": "x"}).status_code == 403
    crew = {c["code"]: c for c in client.get("/api/v1/doctor/crew").json()}
    assert crew["AST-001"]["overdue_exams"] >= 1  # blood test (day 3) still overdue


def test_end_day_awards_points_for_the_closed_day(client, tmp_path):
    me, a = _crew(client, tmp_path)
    assert me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 4}}).status_code == 201
    run = client.post("/api/test/end-day", json={}).json()
    assert run["status"] == "completed"
    assert run["steps"]["exams_points"]["detail"][str(a)]["points_added"] >= 5
    assert me.get("/api/v1/points", params={"astronaut_id": a}).json()["points"] >= 5


# ------------------------------------------------------------------ Module N: wearable feed and explanation
def test_wearable_feed_is_simulated_and_scoped(client, tmp_path):
    from am.db.models import Measurement
    from sqlalchemy import select

    me, a = _crew(client, tmp_path)
    r = me.post("/api/test/wearable", json={"astronaut_id": a, "hours": 2})
    assert r.status_code == 201 and r.json()["readings"] > 10 and r.json()["data_origin"] == "SIMULATED_GENERATOR"
    assert me.post("/api/test/wearable", json={"astronaut_id": a + 1, "hours": 1}).status_code == 403
    assert me.post("/api/test/wearable", json={"astronaut_id": a, "preset": "x"}).status_code == 422
    with client.app.state.ctx.session_factory() as s:
        rows = s.scalars(select(Measurement).where(Measurement.astronaut_id == a)).all()
        assert rows and all(m.is_simulated and m.data_origin == "SIMULATED_GENERATOR" for m in rows)


def test_explanation_lists_rules_and_records(client, tmp_path):
    me, a = _crew(client, tmp_path)
    conv = me.post("/api/v1/companion/conversations", json={"astronaut_id": a}).json()
    me.post(f"/api/v1/companion/conversations/{conv['id']}/messages", json={"content": "Midem bulanıyor ve ateşim var"})
    ex = client.get("/api/v1/explain", params={"astronaut_id": a}).json()
    assert ex["level"] == "yellow" and "Not a probability" in ex["label"]
    rep = [f for f in ex["factors"] if f["kind"] == "report"]
    assert rep and rep[0]["rule_ids"] == ["R-TEXT-SYMPTOM"] and rep[0]["record_refs"][0]["type"] == "message"
    assert "%" not in ex["label"]
    assert me.get("/api/v1/explain", params={"astronaut_id": a}).status_code == 200  # own record
    other = TestClient(client.app)
    sign_in(other, role="astronaut", username="ast002")
    assert other.get("/api/v1/explain", params={"astronaut_id": a}).status_code == 403
