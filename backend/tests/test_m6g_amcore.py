"""M6g: AM Core: sealed daily reports, AM's own decisions, emergency unseal and mission return."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.ai.ollama import ChatReply, LLMUnavailable
from am.amcore import narrative
from am.crew_seed import seed_crew
from am.db.base import clock_offset, set_clock_offset, utcnow
from am.db.models_ai import SelfReport
from am.db.models_amcore import AmDecision, AmReport
from tests.conftest import sign_in


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    return me, sign_in(me, role="astronaut", username="ast001").astronaut_id


def test_end_day_writes_sealed_reports_and_decisions(client, crew):
    me, a = crew
    me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 4}})
    run = client.post("/api/test/end-day", json={}).json()
    assert run["status"] == "completed" and run["steps"]["am_core"]["status"] == "done"
    with client.app.state.ctx.session_factory() as s:
        reports = s.scalars(select(AmReport)).all()
        assert {r.kind for r in reports} == {"astronaut_day", "crew_day"} and len(reports) == 6
        mine = next(r for r in reports if r.astronaut_id == a)
        assert mine.facts["behaviour"]["checked_in"] and mine.narrative and mine.narrative_status == "pending"
        assert "Not a diagnosis" in mine.assessment["label"]
        assert s.scalars(select(AmDecision).where(AmDecision.category == "attention")).first().visible_to_doctor


def test_core_is_sealed_crew_has_no_access_and_doctor_sees_only_visible_decisions(client, crew):
    me, a = crew
    client.post("/api/test/end-day", json={})
    assert client.get("/api/v1/am/core/reports").status_code == 423
    assert client.get("/api/v1/am/core/mission-log").status_code == 423
    visible = client.get("/api/v1/am/core/decisions").json()
    assert visible and all(d["visible_to_doctor"] for d in visible)
    for path in ("/api/v1/am/core/status", "/api/v1/am/core/decisions", "/api/v1/am/core/reports"):
        assert me.get(path).status_code == 403
    assert me.post("/api/v1/am/core/unseal", json={"reason": "I want to read it now"}).status_code == 403


def test_emergency_unseal_is_logged_time_limited_and_told_to_the_crew(client, crew):
    me, a = crew
    client.post("/api/test/end-day", json={})
    assert client.post("/api/v1/am/core/unseal", json={"reason": "short"}).status_code == 422
    st = client.post("/api/v1/am/core/unseal", json={"reason": "Crew member unresponsive in quarters, need context"}).json()
    assert st["open"] and st["open_reason"] == "emergency"
    reports = client.get("/api/v1/am/core/reports").json()
    assert {r["kind"] for r in reports} == {"astronaut_day", "crew_day"}
    all_decisions = client.get("/api/v1/am/core/decisions").json()
    assert any(not d["visible_to_doctor"] for d in all_decisions)  # sealed ones are readable now
    notices = me.get("/api/v1/care/status", params={"astronaut_id": a}).json()["am_core_notices"]
    assert notices[0]["kind"] == "emergency" and "unresponsive" in notices[0]["reason"]
    set_clock_offset(clock_offset() + timedelta(hours=3))  # the window closes by itself
    assert client.get("/api/v1/am/core/reports").status_code == 423


def test_mission_return_opens_the_core_for_good(client, crew):
    client.post("/api/test/end-day", json={})
    me, _ = crew
    assert me.post("/api/test/mission/return").status_code == 403
    st = client.post("/api/test/mission/return").json()
    assert st["open"] and st["open_reason"] == "mission_return"
    set_clock_offset(clock_offset() + timedelta(days=30))
    log = client.get("/api/v1/am/core/mission-log").json()
    assert len(log) == 1 and log[0]["kind"] == "crew_day"
    summary = client.get("/api/v1/am/core/summary").json()
    assert summary["days_logged"] == 1 and "AST-001" in summary["crew"]


def test_declining_self_ratings_raise_attention_and_a_doctor_notice(client, crew):
    me, a = crew
    today = utcnow().date()
    with client.app.state.ctx.session_factory() as s:
        for back, score in ((5, 4), (4, 4), (3, 5), (2, 2), (1, 2), (0, 1)):
            at = datetime.combine(today - timedelta(days=back), time(9, 0))
            for kind in ("mood", "motivation"):
                s.add(SelfReport(astronaut_id=a, checkin_id=f"t{back}{kind}", kind=kind, score=score, recorded_at=at))
        s.commit()
    client.post("/api/test/end-day", json={})
    with client.app.state.ctx.session_factory() as s:
        rep = s.scalars(select(AmReport).where(AmReport.astronaut_id == a)).one()
        assert rep.assessment["attention"] == "priority" and set(rep.assessment["declining"]) == {"mood", "motivation"}
        notice = s.scalars(select(AmDecision).where(AmDecision.astronaut_id == a,
                                                    AmDecision.category == "doctor_notice")).one()
        assert notice.visible_to_doctor and notice.rule_id == "A-DOCTOR"


def test_narratives_come_from_the_model_with_checks_or_stay_on_the_template(client, crew, monkeypatch):
    client.post("/api/test/end-day", json={})
    ctx = client.app.state.ctx

    class Fake:
        calls = 0

        def chat(self, model, messages, **kw):
            Fake.calls += 1
            if Fake.calls == 1:
                return ChatReply(content="## Health\nNothing flagged. Heart rate 999 bpm.", model="fake")
            raise LLMUnavailable("not_running")

    monkeypatch.setattr(ctx, "llm", Fake())
    for _ in range(4):
        narrative.process_pending(ctx, limit=10)
    with ctx.session_factory() as s:
        rows = s.scalars(select(AmReport).order_by(AmReport.id)).all()
        first = rows[0]
        assert first.narrative_status == "llm" and 999 in first.checks["unverified_numbers"]
        assert all(r.narrative_status == "template" for r in rows[1:])  # model offline: template kept
