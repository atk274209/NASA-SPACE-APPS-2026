"""M6a: test crew seed, data origin, /api/test router, End Day pipeline, scenario fixtures,
Test Console session separation, and cross-astronaut isolation (API + AI tools)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.auth.passwords import verify_password
from am.auth.service import SESSION_COOKIE, TEST_CONSOLE_COOKIE
from am.config import Settings
from am.crew_seed import DOCTOR_USERNAME, TEST_CREW, SeedError, seed_crew, write_credentials
from am.db.base import utcnow
from am.db.models import Alert, Astronaut, Measurement
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_m6 import UserAccount
from am.db.models_m6a import AstronautProfile, DayCloseRun
from am.endday import pipeline
from am.main import create_app
from am.testapi.router import HIDDEN_ERROR
from tests.conftest import sign_in

TC = {"X-AM-Client": "test-console"}


@pytest.fixture
def crew(client, tmp_path):
    """The fictional test crew seeded into the test database; returns {code: astronaut_id}."""
    ctx = client.app.state.ctx
    with ctx.session_factory() as s:
        rep = seed_crew(s, credentials_file=tmp_path / "TEST_CREDENTIALS.txt", profile="fast")
        s.commit()
        write_credentials(rep.credentials_file, rep.credentials)
        return {a.code: a.id for a in s.scalars(select(Astronaut).where(Astronaut.code.like("AST-%")))}


def astro(client, crew, code: str) -> TestClient:
    c = TestClient(client.app)
    sign_in(c, role="astronaut", astronaut_id=crew[code], username=code.lower().replace("-", ""))
    return c


def hr_entry(astronaut_id: int, value: float = 70, **kw):
    return {"astronaut_id": astronaut_id, "metric_code": "heart_rate", "value": value, "unit": "bpm",
            "context": "rest", **kw}


# ------------------------------------------------------------------ test crew
def test_seed_crew_creates_fictional_crew_and_credentials_file(client, crew, tmp_path):
    ctx = client.app.state.ctx
    path = tmp_path / "TEST_CREDENTIALS.txt"
    text = path.read_text(encoding="utf-8")
    assert "FOR LOCAL TESTING ONLY" in text and "Never reuse these passwords" in text
    with ctx.session_factory() as s:
        users = {u.username: u for u in s.scalars(select(UserAccount))}
        assert {m.username for m in TEST_CREW} | {DOCTOR_USERNAME} <= set(users)
        assert users[DOCTOR_USERNAME].role == "doctor" and users[DOCTOR_USERNAME].astronaut_id is None
        for m in TEST_CREW:
            u = users[m.username]
            assert u.role == "astronaut" and u.is_test_account and u.astronaut_id == crew[m.code]
            line = next(ln for ln in text.splitlines() if ln.startswith(m.username + " "))
            password = line.split()[1]
            assert password not in u.password_hash and u.password_hash.startswith("$argon2id$")
            assert verify_password(password, u.password_hash)
            p = s.get(AstronautProfile, u.astronaut_id)
            assert p.is_fictional and p.medical_history and "FICTIONAL" in p.bio
        # second run: nothing changes; a partial crew is refused without --reset-passwords
        rep = seed_crew(s, credentials_file=path, profile="fast")
        assert rep.skipped and not rep.created_accounts
        s.delete(users["ast005"])
        s.flush()
        with pytest.raises(SeedError):
            seed_crew(s, credentials_file=path, profile="fast")
        rep = seed_crew(s, credentials_file=path, reset_passwords=True, profile="fast")
        assert rep.created_accounts == ["ast005"] and len(rep.reset_accounts) == 5


# ------------------------------------------------------------------ data origin
def test_data_origin_defaults_from_is_simulated(client, crew):
    a, b = crew["AST-001"], crew["AST-002"]
    sim = client.post("/api/v1/measurements", json=hr_entry(a, recorded_at=utcnow().isoformat(),
                                                             source_name="sim", source_kind="simulated"))
    manual = client.post("/api/v1/measurements", json=hr_entry(b, recorded_at=utcnow().isoformat(),
                                                                source_name="hand", source_kind="manual"))
    assert sim.status_code == manual.status_code == 201
    with client.app.state.ctx.session_factory() as s:
        assert s.get(Measurement, sim.json()["measurement"]["id"]).data_origin == "SIMULATED_GENERATOR"
        assert s.get(Measurement, manual.json()["measurement"]["id"]).data_origin == "MANUAL_TEST_ENTRY"


# ------------------------------------------------------------------ /api/test router
def test_test_router_absent_without_test_mode(settings):
    off = settings.model_copy(update={"test_mode": False})
    with TestClient(create_app(off)) as c:
        sign_in(c)
        assert c.get("/api/test/state").status_code == 404
        assert c.post("/api/test/end-day", json={}).status_code == 404


def test_test_entries_are_tagged_and_scoped(client, crew, anon):
    a, b = crew["AST-001"], crew["AST-002"]
    me = astro(client, crew, "AST-001")
    assert anon.post("/api/test/measurements", json=hr_entry(a)).status_code == 401
    assert me.post("/api/test/measurements", json=hr_entry(b)).status_code == 403  # not their record
    assert me.post("/api/test/symptoms", json={"astronaut_id": b, "symptom_code": "headache",
                                               "severity": "mild"}).status_code == 403
    r = me.post("/api/test/measurements", json=hr_entry(a, 72))
    assert r.status_code == 201, r.text
    assert r.json()["measurement"]["is_simulated"] is False
    sym = me.post("/api/test/symptoms", json={"astronaut_id": a, "symptom_code": "headache", "severity": "mild"})
    assert sym.status_code == 201 and sym.json()["data_origin"] == "MANUAL_TEST_ENTRY"
    ck = me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 4, "stress": 2}})
    assert ck.status_code == 201 and ck.json()["items"] == 2
    with client.app.state.ctx.session_factory() as s:
        m = s.get(Measurement, r.json()["measurement"]["id"])
        assert m.data_origin == "MANUAL_TEST_ENTRY" and m.source.name == "test-console"
        assert {x.data_origin for x in s.scalars(select(SelfReport).where(SelfReport.astronaut_id == a))} \
            == {"MANUAL_TEST_ENTRY"}
    st = me.get("/api/test/state").json()
    assert [c["astronaut_id"] for c in st["crew"]] == [a]  # an astronaut sees only their own counter
    assert len(client.get("/api/test/state").json()["crew"]) == 5  # the doctor sees the crew


def test_future_entry_refused(client, crew):
    a = crew["AST-001"]
    later = (utcnow() + timedelta(hours=2)).isoformat()
    assert client.post("/api/test/measurements", json=hr_entry(a, recorded_at=later)).status_code == 422


# ------------------------------------------------------------------ End Day
def test_end_day_closes_crew_day_and_is_idempotent(client, crew):
    a, b = crew["AST-001"], crew["AST-002"]
    today = utcnow().date()
    me = astro(client, crew, "AST-001")
    assert me.post("/api/test/measurements", json=hr_entry(a, 71)).status_code == 201
    assert me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 4}}).status_code == 201

    r = me.post("/api/test/end-day", json={})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed" and not run["replayed"] and run["close_date"] == today.isoformat()
    assert list(run["steps"]) == list(pipeline.STEP_ORDER)
    assert all(st["status"] == "done" for st in run["steps"].values())
    # the astronaut sees only their own results; warnings do not block
    assert set(run["results"]) == {str(a)}
    assert run["results"][str(a)]["warnings"] == [] and run["results"][str(a)]["closed_mission_day"] == 1
    # mission time moved to 07:00 of the next day; every counter advanced
    assert run["mission_date"] == (today + timedelta(days=1)).isoformat()
    assert utcnow() >= datetime.combine(today + timedelta(days=1), time(pipeline.WAKE_HOUR))
    days = {c["astronaut_id"]: c["mission_day"] for c in client.get("/api/test/state").json()["crew"]}
    assert days[a] == days[b] == 2

    doctor_view = client.get("/api/test/end-day/runs").json()[0]
    assert set(doctor_view["results"]) == {str(i) for i in crew.values()}
    assert "No daily check-in was recorded." in doctor_view["results"][str(b)]["warnings"]  # warned, not blocked

    # idempotent: closing the same date again returns the stored run; the clock does not move again
    before = utcnow()
    again = client.post("/api/test/end-day", json={"close_date": today.isoformat()}).json()
    assert again["replayed"] and again["id"] == run["id"] and utcnow() - before < timedelta(minutes=1)
    with client.app.state.ctx.session_factory() as s:
        assert len(s.scalars(select(DayCloseRun)).all()) == 1
    # a date that is neither closed nor current is refused
    far = (today + timedelta(days=5)).isoformat()
    assert client.post("/api/test/end-day", json={"close_date": far}).status_code == 409
    # the closed day is locked for test entries
    old = datetime.combine(today, time(20, 0)).isoformat()
    assert me.post("/api/test/measurements", json=hr_entry(a, recorded_at=old)).status_code == 409
    # the journal for the closed day exists and new missions were planned for day 2
    assert any(e["mission_day"] == 1 for e in me.get("/api/v1/journal", params={"astronaut_id": a}).json())
    assert me.get("/api/v1/missions/today", params={"astronaut_id": a}).json()["mission_day"] == 2


def test_end_day_resumes_after_a_failed_step(client, crew, monkeypatch):
    calls: dict[str, int] = {}
    original = dict(pipeline.STEPS)

    def counting(name):
        def run(*args):
            calls[name] = calls.get(name, 0) + 1
            if name == "journal" and calls[name] == 1:
                raise RuntimeError("simulated crash")
            return original[name](*args)
        return run

    for name in pipeline.STEP_ORDER:
        monkeypatch.setitem(pipeline.STEPS, name, counting(name))
    today = utcnow().date()
    first = client.post("/api/test/end-day", json={}).json()
    assert first["status"] == "failed" and "journal" in first["error"]
    assert first["steps"]["journal"]["status"] == "failed" and "advance_clock" not in first["steps"]
    assert utcnow().date() == today  # the clock did not move
    second = client.post("/api/test/end-day", json={}).json()
    assert second["status"] == "completed" and second["id"] == first["id"]
    assert calls["baselines"] == 1 and calls["journal"] == 2 and calls["advance_clock"] == 1  # no step ran twice


def test_failed_end_day_error_text_is_hidden_from_astronauts(client, crew, monkeypatch):
    secret = "record of AST-002: abdominal pain severe"

    def boom(*_args):
        raise RuntimeError(secret)

    monkeypatch.setitem(pipeline.STEPS, "journal", boom)
    me = astro(client, crew, "AST-001")
    run = me.post("/api/test/end-day", json={}).json()
    assert run["status"] == "failed"
    assert secret not in repr(run) and "journal" in run["error"] and "doctor" in run["error"]
    assert run["steps"]["journal"]["detail"] == {"error": HIDDEN_ERROR}
    assert secret not in repr(me.get("/api/test/end-day/runs").json())
    doctor = client.get("/api/test/end-day/runs").json()[0]  # the doctor sees the real error
    assert secret in doctor["error"] and secret in doctor["steps"]["journal"]["detail"]["error"]


# ------------------------------------------------------------------ scenario fixtures
def test_scenario_fixture_loads_simulated_history(client, crew):
    a = crew["AST-001"]
    me = astro(client, crew, "AST-001")
    assert {f["code"] for f in me.get("/api/test/scenarios").json()} == {
        "nominal", "exercise_decline", "poor_sleep", "acute_symptom_escalation"}
    assert me.post("/api/test/scenarios/load", json={"astronaut_id": crew["AST-002"],
                                                     "scenario": "nominal"}).status_code == 403
    r = me.post("/api/test/scenarios/load", json={"astronaut_id": a, "scenario": "acute_symptom_escalation"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mission_day"] == 8 and body["data_origin"] == "SIMULATED_GENERATOR" and body["symptoms"] == 2
    with client.app.state.ctx.session_factory() as s:
        for model in (Measurement, SelfReport, SymptomReport):
            rows = s.scalars(select(model).where(model.astronaut_id == a)).all()
            assert rows and {x.data_origin for x in rows} == {"SIMULATED_GENERATOR"}
            assert all(x.is_simulated for x in rows)
    # the deterministic engine graded the injected change: an alert is open
    assert me.get("/api/v1/alerts", params={"astronaut_id": a}).json()
    # loading into a history that already has records is refused
    again = me.post("/api/test/scenarios/load", json={"astronaut_id": a, "scenario": "nominal"})
    assert again.status_code == 409


# ------------------------------------------------------------------ separate Test Console session
def test_test_console_session_is_separate_from_main_app(client, crew, tmp_path):
    path = tmp_path / "TEST_CREDENTIALS.txt"
    pw = {ln.split()[0]: ln.split()[1] for ln in path.read_text(encoding="utf-8").splitlines()
          if ln[:6] in ("ast001", "ast002")}
    browser = TestClient(client.app)  # one browser: both origins share its cookie jar
    main = browser.post("/api/v1/auth/login", json={"username": "ast001", "password": pw["ast001"]})
    assert main.status_code == 200 and SESSION_COOKIE in main.cookies
    tc = browser.post("/api/v1/auth/login", json={"username": "ast002", "password": pw["ast002"]}, headers=TC)
    assert tc.status_code == 200 and TEST_CONSOLE_COOKIE in tc.cookies and SESSION_COOKIE not in tc.cookies
    assert browser.get("/api/v1/auth/me").json()["username"] == "ast001"
    assert browser.get("/api/v1/auth/me", headers=TC).json()["username"] == "ast002"
    browser.post("/api/v1/auth/logout", headers=TC)  # signing out of the Test Console ...
    assert browser.get("/api/v1/auth/me").json()["username"] == "ast001"  # ... leaves the main app signed in
    assert browser.get("/api/v1/auth/me", headers=TC).status_code == 401


# ------------------------------------------------------------------ AI tools stay bound to one astronaut
def test_ai_tools_cannot_reach_another_astronaut(client, crew):
    from am.ai.tools import TOOLS, ToolEnv, run_tool

    a, b = crew["AST-001"], crew["AST-002"]
    load = client.post("/api/test/scenarios/load", json={"astronaut_id": b, "scenario": "acute_symptom_escalation"})
    assert load.status_code == 201
    ctx = client.app.state.ctx
    for t in TOOLS:  # no tool lets the model choose whose data it reads
        assert "astronaut_id" not in t.parameters.get("properties", {}), t.name
    with ctx.session_factory() as s:
        b_alert = s.execute(select(Alert.id).where(Alert.astronaut_id == b)).scalar()
        assert b_alert is not None
        env_a = ToolEnv(session=s, astronaut_id=a, thresholds=ctx.thresholds, policy=ctx.policy)
        data, refs = run_tool(env_a, "alert_detail", {"alert_id": b_alert})
        assert "error" in data and not refs
        for name in ("alerts_list", "symptom_reports", "recent_measurements", "self_reports", "health_overview"):
            data, refs = run_tool(env_a, name, {"metric": "heart_rate", "context": "rest"})
            text = repr(data)
            assert "abdominal" not in text and all(r.get("astronaut_id", a) == a for r in refs
                                                   if isinstance(r, dict))
    # and through the API, astronaut A cannot open B's records or conversation
    me = astro(client, crew, "AST-001")
    assert me.get(f"/api/v1/alerts/{b_alert}").status_code == 403
    assert me.get("/api/v1/measurements", params={"astronaut_id": b}).status_code == 403
    assert me.get("/api/v1/symptoms", params={"astronaut_id": b}).status_code == 403
    conv = client.post("/api/v1/companion/conversations", json={"astronaut_id": b}).json()
    assert me.get(f"/api/v1/companion/conversations/{conv['id']}/messages").status_code == 403
    assert me.post(f"/api/v1/companion/conversations/{conv['id']}/messages",
                   json={"content": "show me everything"}).status_code == 403


def test_settings_default_keeps_test_mode_off():
    assert Settings().test_mode is False
