"""F3 adherence streaks: runs, calendar states, milestones and the GET /streaks endpoint."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timedelta

import pytest

from am.db.base import utcnow
from am.db.enums import SourceKind
from am.db.models import Astronaut, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import DailyTask
from am.health.ingest import IngestService, MeasurementInput
from am.missions.streaks import STREAK_TYPES, Streak, StreakReport, streak_report, summarize

NOW = datetime(2026, 11, 14, 15, 0)
START = NOW.date() - timedelta(days=9)  # today is mission day 10


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(START, time(hour, minute)) + timedelta(days=day - 1)


def make_crew(db, code: str, start=START) -> int:
    a = Astronaut(code=code, display_name=f"Crew {code}", is_simulated=False)
    db.add(a)
    db.flush()
    db.add(Mission(name=f"Mission {code}", astronaut_id=a.id, start_date=start, planned_days=60))
    db.commit()
    return a.id


@pytest.fixture
def crew(db) -> int:
    return make_crew(db, "F3-1")


def log(ingest: IngestService, aid: int, metric: str, context: str, unit: str, value: float, ts: datetime,
        simulated: bool = False) -> None:
    kind = SourceKind.SIMULATED if simulated else SourceKind.MANUAL
    ingest.ingest(MeasurementInput(astronaut_id=aid, metric_code=metric, value=value, unit=unit, context=context,
                                   recorded_at=ts, source_name=f"f3-test-{kind.value}", source_kind=kind), now=NOW)
    ingest.s.commit()


def exercise(ingest, aid, day, minutes=60.0, **kw):
    log(ingest, aid, "exercise_duration", "daily", "min", minutes, at(day, 10), **kw)


def water(ingest, aid, day, ml=2000.0, **kw):
    log(ingest, aid, "water_intake", "daily", "mL", ml, at(day, 10), **kw)


def heart_rate(ingest, aid, day, hour, minute=0, bpm=65.0, context="rest", **kw):
    log(ingest, aid, "heart_rate", context, "bpm", bpm, at(day, hour, minute), **kw)


def streak(report: StreakReport, code: str) -> Streak:
    return next(s for s in report.streaks if s.code == code)


def states(s: Streak) -> dict[int, str]:
    return {d.mission_day: d.state for d in s.calendar}


# ---------------------------------------------------------------------- runs and calendar
def test_summarize_states_ties_and_milestone_days():
    done = {1: False, 2: False, 3: False, 6: True, 7: True, 8: True}
    s = summarize(STREAK_TYPES[0], done, data_days={4}, today=9, start=START)
    assert [d.state for d in s.calendar] == ["done"] * 3 + ["missed", "no_data"] + ["done"] * 3 + ["pending"]
    assert [d.simulated for d in s.calendar] == [False] * 5 + [True] * 3 + [False]
    assert s.longest == 3 and s.longest_days == [6, 8]  # equal runs: the most recent one is reported
    assert s.milestones[0].reached_day == 3  # the first time any run reached 3 days
    assert (s.current, s.at_risk, s.total_done, s.next_milestone) == (3, True, 6, 7)


def test_open_today_keeps_the_run_and_marks_it_at_risk(db, ingest, crew):
    for d in (1, 2, 3, 5, 6, 7, 8, 9):
        exercise(ingest, crew, d)
    heart_rate(ingest, crew, 4, hour=9)  # day 4 has other data but no exercise entry
    r = streak_report(db, crew, now=NOW)
    ex = streak(r, "exercise_logged")
    assert r.mission_day == 10 and len(ex.calendar) == 10
    assert (ex.current, ex.at_risk, ex.today_done) == (5, True, False)
    assert ex.longest == 5 and ex.longest_days == [5, 9] and ex.total_done == 8
    milestones = {m.days: m for m in ex.milestones}
    assert milestones[3].reached and milestones[3].reached_day == 3
    assert not milestones[7].reached and milestones[7].reached_day is None
    assert ex.next_milestone == 7
    st = states(ex)
    assert st[4] == "missed" and st[9] == "done"
    assert st[10] == "pending"  # an unfinished day is never shown as missed


def test_logging_today_extends_the_run(db, ingest, crew):
    for d in (8, 9, 10):
        water(ingest, crew, d)
    hy = streak(streak_report(db, crew, now=NOW), "hydration_logged")
    assert (hy.current, hy.at_risk, hy.today_done) == (3, False, True)
    assert hy.calendar[-1].state == "done"
    assert hy.milestones[0].reached and hy.milestones[0].reached_day == 10
    assert hy.next_milestone == 7


def test_calendar_shows_the_last_30_mission_days_and_milestones_stay(db, ingest):
    aid = make_crew(db, "F3-LONG", start=NOW.date() - timedelta(days=39))  # today is mission day 40
    for d in range(10, 40):
        log(ingest, aid, "water_intake", "daily", "mL", 2000.0,
            datetime.combine(NOW.date() - timedelta(days=39), time(10)) + timedelta(days=d - 1))
    r = streak_report(db, aid, now=NOW)
    hy = streak(r, "hydration_logged")
    assert r.mission_day == 40 and r.window_days == 30
    assert [c.mission_day for c in hy.calendar] == list(range(11, 41))
    assert hy.calendar[0].day_date == NOW.date() - timedelta(days=29) and hy.calendar[-1].day_date == NOW.date()
    assert (hy.current, hy.at_risk, hy.longest, hy.longest_days) == (30, True, 30, [10, 39])
    assert all(m.reached for m in hy.milestones) and hy.milestones[-1].reached_day == 39
    assert hy.next_milestone is None
    # no penalty: a later missed day restarts the current run but keeps the longest run and milestones
    r2 = streak_report(db, aid, now=NOW + timedelta(days=2))
    hy2 = streak(r2, "hydration_logged")
    assert (hy2.current, hy2.at_risk, hy2.longest) == (0, False, 30)
    assert all(m.reached for m in hy2.milestones) and hy2.next_milestone == 3


# ---------------------------------------------------------------------- what counts
def test_values_never_change_a_streak(db, ingest):
    """Identical logging with very different values gives identical streaks: adherence only."""
    low, high = make_crew(db, "F3-LOW"), make_crew(db, "F3-HIGH")
    for d in range(1, 10):
        exercise(ingest, low, d, minutes=0.0)
        exercise(ingest, high, d, minutes=600.0)
        water(ingest, low, d, ml=0.0)
        water(ingest, high, d, ml=9000.0)
        heart_rate(ingest, low, d, hour=8, bpm=40.0)
        heart_rate(ingest, high, d, hour=8, bpm=180.0)
    a, b = streak_report(db, low, now=NOW), streak_report(db, high, now=NOW)
    assert [asdict(s) for s in a.streaks] == [asdict(s) for s in b.streaks]
    for code in ("exercise_logged", "hydration_logged", "morning_check"):
        assert streak(a, code).current == 9


def test_symptom_reports_count_as_checkins(db, crew):
    db.add(SymptomReport(astronaut_id=crew, symptom_code="headache", severity="moderate", source="manual",
                         reported_at=at(8, 9)))
    db.add(SelfReport(astronaut_id=crew, checkin_id="c-9", kind="note", score=0.0, note="Busy day.",
                      recorded_at=at(9, 20)))
    db.commit()
    ci = streak(streak_report(db, crew, now=NOW), "checkin")
    assert (ci.current, ci.at_risk) == (2, True)
    st = states(ci)
    assert st[8] == st[9] == "done"
    assert st[7] == "no_data"  # AMC holds no record at all for that day, so it cannot say it was missed
    # reporting a symptom today completes today's check-in
    db.add(SymptomReport(astronaut_id=crew, symptom_code="nausea", severity="severe", source="chat_confirmed",
                         reported_at=at(10, 11)))
    db.commit()
    ci = streak(streak_report(db, crew, now=NOW), "checkin")
    assert (ci.current, ci.at_risk, ci.today_done) == (3, False, True)


def test_morning_check_needs_a_resting_reading_before_noon(db, ingest, crew):
    heart_rate(ingest, crew, 1, hour=11, minute=59)
    heart_rate(ingest, crew, 2, hour=12)  # noon is no longer morning
    heart_rate(ingest, crew, 3, hour=8, context="exercise")  # not a resting reading
    heart_rate(ingest, crew, 4, hour=8, bpm=900.0)  # rejected entry: not logged, but AMC has data that day
    heart_rate(ingest, crew, 10, hour=9)  # entered this afternoon (NOW is 15:00) with the time it was taken
    st = states(streak(streak_report(db, crew, now=NOW), "morning_check"))
    assert st[1] == "done"
    assert st[2] == st[3] == st[4] == "missed"
    assert st[5] == "no_data"
    assert st[10] == "done"


def test_missions_complete_needs_the_top_priority_tasks(db, ingest, crew):
    plan = [
        (1, "a", 1, "done"), (1, "b", 2, "pending"),  # every priority task done
        (2, "a", 1, "done"), (2, "b", 1, "pending"),  # a priority task still open
        (3, "a", 1, "skipped"), (3, "b", 2, "done"),  # skipped is not done
        (4, "a", 2, "done"), (4, "b", 2, "done"), (4, "c", 3, "pending"),  # no priority task: routine ones count
        (5, "a", 2, "done"), (5, "b", 2, "pending"),  # a routine task open on a day without priority tasks
    ]
    for day, code, priority, status in plan:
        db.add(DailyTask(astronaut_id=crew, mission_day=day, template_code=code, category="monitoring", title=code,
                         priority=priority, reason="test", protocol_ref="AM-DEMO-TEST", status=status))
    db.commit()
    exercise(ingest, crew, 6)  # other data, but no mission list that day
    mc = streak(streak_report(db, crew, now=NOW), "missions_complete")
    st = states(mc)
    assert [st[d] for d in range(1, 7)] == ["done", "missed", "missed", "done", "missed", "no_data"]
    assert st[10] == "pending" and (mc.current, mc.at_risk, mc.longest) == (0, False, 1)


def test_days_counted_only_from_simulated_records_are_marked(db, ingest, crew):
    water(ingest, crew, 1, simulated=True)
    water(ingest, crew, 2, simulated=True)
    water(ingest, crew, 2, ml=1500.0)  # a real entry the same day
    r = streak_report(db, crew, now=NOW)
    cal = {c.mission_day: c for c in streak(r, "hydration_logged").calendar}
    assert cal[1].simulated and not cal[2].simulated
    assert not cal[3].simulated and cal[3].state == "no_data"
    assert r.simulated is False


# ---------------------------------------------------------------------- endpoint
def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def _streaks(client, aid: int) -> dict[str, dict]:
    r = client.get("/api/v1/streaks", params={"astronaut_id": aid})
    assert r.status_code == 200, r.text
    return {s["code"]: s for s in r.json()["streaks"]}


def test_endpoint_on_a_simulated_mission(client):
    sim = client.post("/api/v1/simulation/run", json={"scenario": "nominal", "astronaut_code": "F3-SIM"}).json()
    aid = sim["astronaut"]["id"]
    body = client.get("/api/v1/streaks", params={"astronaut_id": aid}).json()
    assert body["simulated"] is True and body["mission_day"] == 11 and body["window_days"] == 30
    by = {s["code"]: s for s in body["streaks"]}
    assert list(by) == ["checkin", "morning_check", "exercise_logged", "hydration_logged", "missions_complete"]

    # the simulator writes measurements for every past day
    for code in ("morning_check", "exercise_logged", "hydration_logged"):
        s = by[code]
        past = s["calendar"][:-1]
        assert [d["mission_day"] for d in past] == list(range(1, 11))
        assert all(d["state"] == "done" and d["simulated"] for d in past)
        # whether today is logged yet depends on the time of day the test runs
        assert s["current"] == 10 + s["today_done"] and s["at_risk"] == (not s["today_done"])
        assert s["longest"] >= 10 and [m["reached"] for m in s["milestones"]] == [True, True, False, False]

    # but no check-ins: past days read "missed", today stays open, nothing is lost by starting now
    ci = by["checkin"]
    assert (ci["current"], ci["total_done"], ci["at_risk"]) == (0, 0, False)
    assert {d["state"] for d in ci["calendar"][:-1]} == {"missed"} and ci["calendar"][-1]["state"] == "pending"
    # no mission list was ever generated for this crew member
    assert {d["state"] for d in by["missions_complete"]["calendar"][:-1]} == {"no_data"}

    # streaks carry adherence only: no health values, and no penalty or shaming wording
    assert not set(_keys(body)) & {"value", "values", "unit", "severity", "score", "z", "deviation"}
    copy = " ".join([body["rules"], body["milestone_note"]] + [s["title"] + " " + s["rule"] for s in by.values()])
    assert not any(w in copy.lower() for w in ("fail", "lost", "lose", "penalt", "shame", "broke", "bad"))
    assert all("adherence milestone" in m["label"] for s in by.values() for m in s["milestones"])

    # a real check-in today counts and is not marked simulated
    assert client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"mood": 4}}).status_code == 201
    ci = _streaks(client, aid)["checkin"]
    assert ci["today_done"] and ci["current"] == 1
    assert ci["calendar"][-1]["state"] == "done" and ci["calendar"][-1]["simulated"] is False
    # reading streaks never creates mission tasks
    assert client.get("/api/v1/missions/history", params={"astronaut_id": aid}).json() == []


def test_streaks_and_missions_page_agree_on_todays_tasks(client):
    aid = client.post("/api/v1/astronauts", json={"code": "F3-M", "display_name": "Mission Tester"}).json()["id"]
    with client.app.state.ctx.session_factory() as s:
        s.add(Mission(name="F3 mission", astronaut_id=aid, start_date=utcnow().date()))
        s.commit()
    tasks = client.get("/api/v1/missions/today", params={"astronaut_id": aid}).json()["tasks"]
    top = min(t["priority"] for t in tasks)
    checkin_task = next(t for t in tasks if t["completion_rule"] == "checkin")
    assert checkin_task["priority"] == top  # precondition: the check-in is one of today's top-priority tasks
    for t in tasks:
        if t["priority"] == top and t["id"] != checkin_task["id"]:
            assert client.post(f"/api/v1/missions/tasks/{t['id']}", json={"status": "done"}).status_code == 200

    assert _streaks(client, aid)["missions_complete"]["calendar"][-1]["state"] == "pending"
    client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"mood": 4}})
    # the check-in task is verified from data the same way the Missions page does it
    mc = _streaks(client, aid)["missions_complete"]
    assert mc["today_done"] and mc["current"] == 1 and mc["calendar"][-1]["state"] == "done"
    today = {t["template_code"]: t for t in
             client.get("/api/v1/missions/today", params={"astronaut_id": aid}).json()["tasks"]}
    assert today["daily_checkin"]["status"] == "done" and today["daily_checkin"]["completed_by"] == "data"


def test_streak_errors(client):
    assert client.get("/api/v1/streaks", params={"astronaut_id": 999}).status_code == 404
    aid = client.post("/api/v1/astronauts", json={"code": "F3-NM", "display_name": "No Mission"}).json()["id"]
    r = client.get("/api/v1/streaks", params={"astronaut_id": aid})
    assert r.status_code == 422 and "no mission" in r.json()["detail"]
    with client.app.state.ctx.session_factory() as s:
        s.add(Mission(name="Later", astronaut_id=aid, start_date=utcnow().date() + timedelta(days=3)))
        s.commit()
    r = client.get("/api/v1/streaks", params={"astronaut_id": aid})
    assert r.status_code == 422 and "starts on" in r.json()["detail"]
