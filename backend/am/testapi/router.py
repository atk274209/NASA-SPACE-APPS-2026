"""/api/test/*: controls used only by the separate Test Console (master prompt v1, Module K).

Mounted only when AM_TEST_MODE=1 (Settings.test_mode). The routes pass through the same
authorization guard as /api/v1 (am.auth.guard): sign-in required, and an astronaut can only
write their own records. Every record written here is tagged MANUAL_TEST_ENTRY (typed by a
tester) or SIMULATED_GENERATOR (scenario fixtures).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.checkin import submit_checkin
from am.api.deps import AppContext, get_ctx, get_db
from am.api.v1.core import _to_out
from am.auth.guard import CurrentUser, doctor_only, signed_in
from am.db.base import clock_offset, to_naive_utc, utcnow
from am.db.enums import SourceKind
from am.db.models import Astronaut
from am.db.models_m6a import DataOrigin, DayCloseRun
from am.endday.pipeline import STEP_ORDER, EndDayConflict, crew_on, day_lock_through, end_day, mission_date
from am.health.ingest import IngestService, MeasurementInput
from am.schemas import IngestOut
from am.symptoms.service import SymptomService
from am.testapi.fixtures import FIXTURES, FixtureError, load_fixture

router = APIRouter(prefix="/test", tags=["test-console"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]

TEST_SOURCE = "test-console"


def _check_open_day(db: Session, at: datetime) -> None:
    lock = day_lock_through(db)
    if lock is not None and at < lock:
        raise HTTPException(409, f"Mission days up to {lock.isoformat()} are closed. Entries must fall in the "
                                 "current mission day.")
    if at > utcnow():
        raise HTTPException(422, "The time cannot be in the future (mission time).")


# ------------------------------------------------------------------ state
class CrewDayOut(BaseModel):
    astronaut_id: int
    code: str
    display_name: str
    mission_start: date
    mission_day: int


class RunBriefOut(BaseModel):
    id: int
    close_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None


class StateOut(BaseModel):
    test_mode: bool = True
    simulated_label: str = "SIMULATED TEST DATA"
    mission_now: datetime
    mission_date: date
    clock_offset_seconds: float
    day_lock_through: datetime | None
    crew: list[CrewDayOut]
    last_runs: list[RunBriefOut]


@router.get("/state", response_model=StateOut)
@signed_in
def state(db: DB, principal: CurrentUser):
    today = mission_date()
    crew = []
    for m in crew_on(db, today):
        if not principal.may_access(m.astronaut_id):
            continue
        a = db.get(Astronaut, m.astronaut_id)
        crew.append(CrewDayOut(astronaut_id=m.astronaut_id, code=m.code, display_name=a.display_name,
                               mission_start=m.mission_start, mission_day=m.mission_day))
    runs = db.scalars(select(DayCloseRun).order_by(DayCloseRun.close_date.desc()).limit(10)).all()
    return StateOut(mission_now=utcnow(), mission_date=today, clock_offset_seconds=clock_offset().total_seconds(),
                    day_lock_through=day_lock_through(db), crew=crew,
                    last_runs=[RunBriefOut.model_validate(r, from_attributes=True) for r in runs])


# ------------------------------------------------------------------ live entries
class TestMeasurementIn(BaseModel):
    astronaut_id: int
    metric_code: str
    value: float
    unit: str
    context: str = Field(description="activity context: rest | exercise | sleep | other | daily | fasting | ...")
    recorded_at: datetime | None = Field(default=None, description="defaults to mission now")


@router.post("/measurements", response_model=IngestOut, status_code=201)
def add_measurement(body: TestMeasurementIn, db: DB, ctx: CTX):
    at = to_naive_utc(body.recorded_at) if body.recorded_at else utcnow()
    _check_open_day(db, at)
    svc = IngestService(db, ctx.thresholds, ctx.policy)
    try:
        result = svc.ingest(MeasurementInput(astronaut_id=body.astronaut_id, metric_code=body.metric_code,
                                             value=body.value, unit=body.unit, context=body.context, recorded_at=at,
                                             source_name=TEST_SOURCE, source_kind=SourceKind.MANUAL))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result.measurement.data_origin = DataOrigin.MANUAL_TEST_ENTRY.value
    db.commit()
    return _to_out(result)


class TestSymptomIn(BaseModel):
    astronaut_id: int
    symptom_code: str
    severity: str
    note: str = Field(default="", max_length=1000)
    onset_at: datetime | None = None


class TestSymptomOut(BaseModel):
    id: int
    astronaut_id: int
    symptom_code: str
    severity: str
    status: str
    data_origin: str
    reported_at: datetime


@router.post("/symptoms", response_model=TestSymptomOut, status_code=201)
def add_symptom(body: TestSymptomIn, db: DB):
    _check_open_day(db, utcnow())
    try:
        r = SymptomService(db).report(body.astronaut_id, body.symptom_code, body.severity, note=body.note,
                                      onset_at=body.onset_at, source="manual")
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    r.data_origin = DataOrigin.MANUAL_TEST_ENTRY.value
    db.commit()
    return TestSymptomOut.model_validate(r, from_attributes=True)


class TestCheckinIn(BaseModel):
    astronaut_id: int
    ratings: dict[str, int] = Field(default_factory=dict)
    note: str = Field(default="", max_length=1000)


class TestCheckinOut(BaseModel):
    checkin_id: str
    items: int
    data_origin: str


@router.post("/checkins", response_model=TestCheckinOut, status_code=201)
def add_checkin(body: TestCheckinIn, db: DB):
    _check_open_day(db, utcnow())
    try:
        rows = submit_checkin(db, body.astronaut_id, body.ratings, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for r in rows:
        r.data_origin = DataOrigin.MANUAL_TEST_ENTRY.value
    db.commit()
    return TestCheckinOut(checkin_id=rows[0].checkin_id if rows else "", items=len(rows),
                          data_origin=DataOrigin.MANUAL_TEST_ENTRY.value)


# ------------------------------------------------------------------ scenario fixtures
class FixtureOut(BaseModel):
    code: str
    title: str
    description: str
    days: int
    symptoms: list[str]


@router.get("/scenarios", response_model=list[FixtureOut])
@signed_in
def list_scenarios():
    from am.simulation.generator import SCENARIOS

    return [FixtureOut(code=f.code, title=f.title, description=f.description, days=SCENARIOS[f.scenario].days,
                       symptoms=[f"{s.code} ({s.severity})" for s in f.symptoms]) for f in FIXTURES.values()]


class LoadFixtureIn(BaseModel):
    astronaut_id: int
    scenario: str


class LoadFixtureOut(BaseModel):
    fixture: str
    astronaut_id: int
    mission_start: str
    mission_day: int
    measurements: int
    checkins: int
    symptoms: int
    data_origin: str = DataOrigin.SIMULATED_GENERATOR.value


@router.post("/scenarios/load", response_model=LoadFixtureOut, status_code=201)
def load_scenario(body: LoadFixtureIn, db: DB, ctx: CTX, principal: CurrentUser):
    try:
        rep = load_fixture(db, ctx, astronaut_id=body.astronaut_id, code=body.scenario, actor=principal.username)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except FixtureError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return LoadFixtureOut(**rep.__dict__)


# ------------------------------------------------------------------ End Day
class EndDayIn(BaseModel):
    close_date: date | None = Field(default=None, description="the mission date to close; default: today. "
                                                              "Repeating a closed date returns the stored run.")


class EndDayOut(BaseModel):
    id: int
    close_date: date
    status: str
    replayed: bool
    error: str | None
    steps: dict[str, Any]
    results: dict[str, Any]
    mission_now: datetime
    mission_date: date
    started_at: datetime
    finished_at: datetime | None


HIDDEN_ERROR = "Details are visible to the doctor account only."


def _visible(run: DayCloseRun, principal) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """An astronaut sees only their own part of a crew-wide run; the doctor sees everything.

    A failed step's error text can come from any crew member's records, so an astronaut gets
    only the name of the failed step, never the error itself.
    """
    if principal.is_doctor:
        return run.steps, run.results, run.error
    me = str(principal.astronaut_id)
    steps: dict[str, Any] = {}
    failed: str | None = None
    for name in STEP_ORDER:
        st = run.steps.get(name)
        if st is None:
            continue
        if st.get("status") == "failed":
            failed = failed or name
            steps[name] = {"status": "failed", "at": st.get("at"), "detail": {"error": HIDDEN_ERROR}}
            continue
        detail = st.get("detail") or {}
        mine = detail.get(me) if any(k.isdigit() for k in detail) else detail
        steps[name] = {"status": st.get("status"), "at": st.get("at"), "detail": mine or {}}
    error = f"End Day stopped at step '{failed}'. {HIDDEN_ERROR}" if run.error and failed else (
        HIDDEN_ERROR if run.error else None)
    return steps, ({me: run.results[me]} if me in (run.results or {}) else {}), error


def _end_day_out(run: DayCloseRun, replayed: bool, principal) -> EndDayOut:
    steps, results, error = _visible(run, principal)
    return EndDayOut(id=run.id, close_date=run.close_date, status=run.status, replayed=replayed, error=error,
                     steps=steps, results=results, mission_now=utcnow(), mission_date=mission_date(),
                     started_at=run.started_at, finished_at=run.finished_at)


@router.post("/end-day", response_model=EndDayOut)
@signed_in
def end_day_route(body: EndDayIn, db: DB, ctx: CTX, principal: CurrentUser):
    """Close the mission day for the whole crew (one shared mission clock) and start the next."""
    try:
        res = end_day(db, ctx, actor=principal.username, close_date=body.close_date)
    except EndDayConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _end_day_out(res.run, res.replayed, principal)


@router.get("/end-day/runs", response_model=list[EndDayOut])
@signed_in
def list_runs(db: DB, principal: CurrentUser, limit: int = 20):
    runs = db.scalars(select(DayCloseRun).order_by(DayCloseRun.close_date.desc()).limit(max(1, min(limit, 100))))
    return [_end_day_out(r, False, principal) for r in runs]


# ------------------------------------------------------------------ simulated wearable feed (Module N)
WEARABLE_SOURCE = "simulated wearable (smart shirt)"
WEARABLE_PRESETS = ("nominal", "elevated_heart_rate", "fever")


class WearableIn(BaseModel):
    astronaut_id: int
    hours: int = Field(default=2, ge=1, le=12)
    preset: str = Field(default="nominal", description="nominal | elevated_heart_rate | fever (SIMULATED)")


class WearableOut(BaseModel):
    astronaut_id: int
    readings: int
    alerts_touched: list[int]
    window_start: datetime
    window_end: datetime
    data_origin: str = DataOrigin.SIMULATED_GENERATOR.value


@router.post("/wearable", response_model=WearableOut, status_code=201)
def wearable_feed(body: WearableIn, db: DB, ctx: CTX):
    """Generate SIMULATED wearable readings for the last few hours of the open mission day.

    Values are arbitrary demonstration draws (see am.simulation.generator), not physiological norms.
    """
    import numpy as np

    from datetime import timedelta

    if body.preset not in WEARABLE_PRESETS:
        raise HTTPException(422, f"preset must be one of {list(WEARABLE_PRESETS)}")
    end = utcnow()
    start = end - timedelta(hours=body.hours)
    lock = day_lock_through(db)
    if lock is not None and start < lock:
        start = lock
    if start >= end:
        raise HTTPException(409, "The mission day has just started; there is no time window to fill yet.")
    rng = np.random.default_rng(int(end.timestamp()) % 100000 + body.astronaut_id)
    hr_shift = 25.0 if body.preset == "elevated_heart_rate" else 12.0 if body.preset == "fever" else 0.0
    temp_shift = 1.2 if body.preset == "fever" else 0.0
    svc = IngestService(db, ctx.thresholds, ctx.policy)
    readings, alerts = 0, set()
    t = start + timedelta(minutes=10)
    try:
        while t <= end:
            for metric, unit, mean, sd, dec in (("heart_rate", "bpm", 68 + hr_shift, 2.5, 0),
                                                ("spo2", "%", 97.5, 0.5, 1),
                                                ("body_temperature", "degC", 36.7 + temp_shift, 0.1, 2)):
                if metric != "heart_rate" and t.minute >= 20:  # SpO2 and temperature a few times per hour
                    continue
                value = round(float(rng.normal(mean, sd)), dec) if dec else float(round(rng.normal(mean, sd)))
                res = svc.ingest(MeasurementInput(astronaut_id=body.astronaut_id, metric_code=metric, value=value,
                                                  unit=unit, context="rest", recorded_at=t, source_name=WEARABLE_SOURCE,
                                                  source_kind=SourceKind.SIMULATED, confidence=0.9))
                readings += 1
                if res.alert_id:
                    alerts.add(res.alert_id)
            t += timedelta(minutes=10)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return WearableOut(astronaut_id=body.astronaut_id, readings=readings, alerts_touched=sorted(alerts),
                       window_start=start, window_end=end)


# ------------------------------------------------------------------ cabin simulator controls (Module P)
class CabinFaultIn(BaseModel):
    control_id: int
    fault: str = Field(description="none | sensor | actuator (SIMULATED)")


class CabinDriftIn(BaseModel):
    control_id: int
    offset: float = Field(ge=-10, le=10, description="added to the SIMULATED reading")


class CabinSenseOut(BaseModel):
    alerts_raised: list[int]


@router.post("/cabin/fault", response_model=CabinSenseOut)
def cabin_fault(body: CabinFaultIn, db: DB, principal: CurrentUser):
    from am.cabin import controller as cab
    from am.db.models_cabin import EnvControl

    if body.fault not in ("none", "sensor", "actuator"):
        raise HTTPException(422, "fault must be none, sensor or actuator")
    c = db.get(EnvControl, body.control_id)
    if c is None:
        raise HTTPException(404, "Control not found.")
    c.fault = body.fault
    cab._log(db, c, "FAULT", "P-TEST-FAULT", principal.username, reason=f"Test Console: fault set to {body.fault}")
    alerts = cab.sense(db)
    db.commit()
    return CabinSenseOut(alerts_raised=[a.id for a in alerts])


@router.post("/cabin/drift", response_model=CabinSenseOut)
def cabin_drift(body: CabinDriftIn, db: DB, principal: CurrentUser):
    from am.cabin import controller as cab
    from am.db.models_cabin import EnvControl

    c = db.get(EnvControl, body.control_id)
    if c is None:
        raise HTTPException(404, "Control not found.")
    c.drift = body.offset
    cab._log(db, c, "FAULT", "P-TEST-DRIFT", principal.username, reason=f"Test Console: drift set to {body.offset:+g}")
    alerts = cab.sense(db)
    db.commit()
    return CabinSenseOut(alerts_raised=[a.id for a in alerts])


# ------------------------------------------------------------------ mission return (AM Core opens for good)
@router.post("/mission/return", response_model=dict)
@doctor_only
def mission_return(db: DB, principal: CurrentUser):
    """TEST: mark the mission as returned to Earth. AM Core then stays open to the doctor."""
    from am.amcore import access

    access.mark_mission_returned(db, principal.username)
    db.commit()
    return access.state(db)
