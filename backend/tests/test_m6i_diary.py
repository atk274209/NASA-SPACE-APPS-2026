"""M6i: private diary. AMC talks with the astronaut about the day, colours it, and keeps it private."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.crew_seed import seed_crew
from am.db.models_m6b import ReportFlag
from tests.conftest import sign_in
from tests.test_companion import FakeOllama, answer


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    other = TestClient(client.app)
    a = sign_in(me, role="astronaut", username="ast001").astronaut_id
    sign_in(other, role="astronaut", username="ast002")
    return me, other, a


def _today(me):
    return me.get("/api/v1/diary/today").json()


def _write(me, day_id, text):
    r = me.post(f"/api/v1/diary/days/{day_id}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_am_opens_the_page_and_keeps_asking_offline(client, crew):
    me, _, _ = crew
    page = _today(me)
    assert page["day"]["is_today"] and page["day"]["level"] == "green"
    assert [m["role"] for m in page["messages"]] == ["assistant"]  # AMC starts with a question
    page = _write(me, page["day"]["id"], "Bugün deneyleri bitirdim, güzel bir gündü.")
    assert page["ai_available"] is False  # no model in tests: deterministic follow-up question
    last = page["messages"][-1]
    assert last["role"] == "assistant" and last["content"].endswith("?")
    assert any(ch in last["content"] for ch in "ğüşıöç")  # replies in the astronaut's language


def test_the_diary_never_reaches_the_doctor_feed(client, crew):
    me, _, _ = crew
    page = _write(me, _today(me)["day"]["id"], "I miss my family a lot today and I argued with the commander.")
    with client.app.state.ctx.session_factory() as s:
        assert s.scalars(select(ReportFlag)).all() == []
    assert page["day"]["level"] in ("green", "yellow")


def test_only_its_author_can_read_it_and_the_doctor_waits_for_landing(client, crew):
    me, other, a = crew
    day_id = _today(me)["day"]["id"]
    _write(me, day_id, "A quiet day.")
    assert other.get(f"/api/v1/diary/days/{day_id}").status_code == 404
    assert client.get(f"/api/v1/diary/days/{day_id}").status_code == 403  # doctor: not here
    assert client.get("/api/v1/diary/today").status_code == 403
    assert client.get(f"/api/v1/doctor/diary/{a}").status_code == 423  # sealed until return
    assert me.get(f"/api/v1/doctor/diary/{a}").status_code == 403
    client.post("/api/test/mission/return")
    opened = client.get(f"/api/v1/doctor/diary/{a}").json()
    assert opened[0]["messages"][-2]["content"] == "A quiet day."
    audit = client.get("/api/v1/audit", params={"action_prefix": "diary.read_after_return"}).json()
    assert audit and audit[0]["actor"].startswith("doctor:")


def test_urgent_medical_matter_goes_to_the_doctor_as_one_sentence(client, crew):
    me, _, a = crew
    page = _write(me, _today(me)["day"]["id"],
                  "Spent the morning on the arm. I have chest pain since lunch. Also I miss home.")
    assert page["day"]["level"] == "red"
    fw = page["forwards"]
    assert [(f["symptom_code"], f["status"]) for f in fw] == [("chest_pain", "sent_urgent")]
    assert "medical officer" in page["messages"][-1]["content"]
    with client.app.state.ctx.session_factory() as s:
        flag = s.scalars(select(ReportFlag)).one()
        assert flag.source_type == "diary" and flag.rule_level == "red" and flag.approval_status == "awaiting"
        assert "chest pain" in flag.text.lower() and "miss home" not in flag.text and "arm" not in flag.text


def test_other_symptoms_are_offered_and_the_astronaut_decides(client, crew):
    me, _, _ = crew
    page = _write(me, _today(me)["day"]["id"], "Long day. I have a headache and my back hurts.")
    offered = {f["symptom_code"]: f for f in page["forwards"]}
    assert offered and all(f["status"] == "offered" for f in offered.values())
    assert "send it to your doctor" in page["messages"][-1]["content"]
    with client.app.state.ctx.session_factory() as s:
        assert s.scalars(select(ReportFlag)).all() == []
    head = offered["headache"]
    assert me.post(f"/api/v1/diary/forwards/{head['id']}", json={"send": True}).status_code == 409  # needs severity
    sent = me.post(f"/api/v1/diary/forwards/{head['id']}", json={"send": True, "severity": "moderate"}).json()
    assert sent["status"] == "sent"
    with client.app.state.ctx.session_factory() as s:
        flag = s.scalars(select(ReportFlag)).one()
        assert flag.source_type == "symptom_report" and "Long day" not in flag.text
    if "back_pain" in offered:
        kept = me.post(f"/api/v1/diary/forwards/{offered['back_pain']['id']}", json={"send": False}).json()
        assert kept["status"] == "kept_private"


def test_ai_may_raise_the_colour_with_a_quote_but_never_lower_it(client, crew):
    me, _, _ = crew
    day_id = _today(me)["day"]["id"]
    _write(me, day_id, "Everything went fine. I just feel so tired of all this, nothing matters anymore.")
    fake = FakeOllama([answer(json.dumps({"level": "red", "reason": "Hopeless wording.",
                                          "quote": "nothing matters anymore",
                                          "summary": "Today felt heavy for you."}))])
    client.app.state.ctx.llm = fake.client()
    day = me.post(f"/api/v1/diary/days/{day_id}/analyse").json()
    assert day["ai_status"] == "done" and day["level"] == "red" and day["summary"]

    fake = FakeOllama([answer(json.dumps({"level": "green", "reason": "x", "quote": "", "summary": "Fine."}))])
    client.app.state.ctx.llm = fake.client()
    page2 = _write(me, day_id, "I have chest pain.")  # rule floor red
    assert page2["day"]["level"] == "red"
    day = me.post(f"/api/v1/diary/days/{day_id}/analyse").json()
    assert day["rule_level"] == "red" and day["level"] == "red"  # the model's green cannot lower it


def test_a_made_up_quote_is_rejected(client, crew):
    me, _, _ = crew
    day_id = _today(me)["day"]["id"]
    _write(me, day_id, "Normal day, lots of maintenance.")
    client.app.state.ctx.llm = FakeOllama([answer(json.dumps(
        {"level": "yellow", "reason": "Stress.", "quote": "I am stressed", "summary": "x"}))]).client()
    day = me.post(f"/api/v1/diary/days/{day_id}/analyse").json()
    assert day["ai_status"] == "rejected" and day["level"] == "green"


def test_old_pages_are_readable_but_not_writable(client, crew):
    from datetime import timedelta

    from am.db.base import clock_offset, set_clock_offset

    me, _, _ = crew
    first = _today(me)["day"]["id"]
    _write(me, first, "Day one.")
    set_clock_offset(clock_offset() + timedelta(days=1))
    second = _today(me)["day"]["id"]
    assert second != first
    days = me.get("/api/v1/diary/days").json()
    assert [d["id"] for d in days] == [second, first]
    old = me.get(f"/api/v1/diary/days/{first}").json()
    assert any(m["content"] == "Day one." for m in old["messages"])
    assert me.post(f"/api/v1/diary/days/{first}/messages", json={"content": "late"}).status_code == 422
