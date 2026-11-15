"""End Day: close the current mission day for the whole crew and start the next one.

See docs/M6_SPEC.md ("Time model", "End Day pipeline"). One shared mission clock; each
astronaut's mission day is counted from their own mission start date.

Idempotency: `day_close_run.close_date` is unique. Asking again for a date that was closed
returns the stored run unchanged. A run that stopped part-way resumes at the first step that
did not finish; finished steps are never repeated. Each step commits on its own, so progress
survives a crash.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from am.audit import audit
from am.baseline.service import BaselineService
from am.clock import advance_clock
from am.db.base import utcnow
from am.db.enums import AlertStatus, DataQuality
from am.db.models import Alert, Astronaut, Measurement, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import AppSetting, SymptomReport
from am.db.models_m6a import DayCloseRun
from am.db.models_research import DailyTask

WAKE_HOUR = 7  # End Day moves mission time to 07:00 of the next day (crew wake-up)
DAY_LOCK_KEY = "day_lock_through"

STEP_ORDER = ("hydration", "check_logs", "lock_day", "baselines", "anomaly_persistence", "correlation", "journal",
              "am_core", "advance_clock", "missions", "streaks", "exams_points")

_run_lock = threading.Lock()  # one End Day at a time in this process


class EndDayConflict(Exception):
    """The request does not match the mission timeline; the message says why."""


@dataclass(frozen=True)
class CrewMemberDay:
    astronaut_id: int
    code: str
    mission_start: date
    mission_day: int  # the mission day that `close_date` falls on (>= 1)


def mission_date() -> date:
    return utcnow().date()


def day_lock_through(s: Session) -> datetime | None:
    row = s.get(AppSetting, DAY_LOCK_KEY)
    return datetime.fromisoformat(row.value) if row is not None and row.value else None


def _mission_of(s: Session, astronaut_id: int) -> Mission | None:
    return s.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                     .order_by(Mission.start_date.desc(), Mission.id.desc())).first()


def crew_on(s: Session, d: date) -> list[CrewMemberDay]:
    """Astronauts whose mission has started by date `d`, with their mission day on `d`."""
    out = []
    for a in s.scalars(select(Astronaut).order_by(Astronaut.id)):
        m = _mission_of(s, a.id)
        if m is None or m.start_date > d:
            continue
        out.append(CrewMemberDay(a.id, a.code, m.start_date, (d - m.start_date).days + 1))
    return out


def _bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


# ------------------------------------------------------------------ steps
StepFn = Callable[[Session, Any, DayCloseRun, list[CrewMemberDay]], dict[str, Any]]


def _per(member: CrewMemberDay) -> str:
    return str(member.astronaut_id)


def step_hydration(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """Store each astronaut's logged drinks as one water_intake daily total; adherence points."""
    from am.nutrition.service import close_day

    return {_per(m): close_day(s, ctx, m.astronaut_id, m.mission_day) for m in crew}


def step_check_logs(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """Warn (never block) when a crew member logged nothing for the day."""
    from am.missions.service import MissionService

    d0, d1 = _bounds(run.close_date)
    out: dict[str, Any] = {}
    for m in crew:
        checkins = s.scalar(select(func.count()).select_from(SelfReport).where(
            SelfReport.astronaut_id == m.astronaut_id, SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1)) or 0
        measurements = s.scalar(select(func.count()).select_from(Measurement).where(
            Measurement.astronaut_id == m.astronaut_id, Measurement.recorded_at >= d0, Measurement.recorded_at < d1,
            Measurement.quality != DataQuality.REJECTED.value)) or 0
        symptoms = s.scalar(select(func.count()).select_from(SymptomReport).where(
            SymptomReport.astronaut_id == m.astronaut_id, SymptomReport.reported_at >= d0,
            SymptomReport.reported_at < d1)) or 0
        MissionService(s).sync_completion(m.astronaut_id, m.mission_day)
        tasks = s.scalars(select(DailyTask).where(DailyTask.astronaut_id == m.astronaut_id,
                                                  DailyTask.mission_day == m.mission_day)).all()
        done = sum(t.status == "done" for t in tasks)
        warnings = []
        if checkins == 0:
            warnings.append("No daily check-in was recorded.")
        if measurements == 0:
            warnings.append("No measurement was recorded.")
        out[_per(m)] = {"checkins": checkins, "measurements": measurements, "symptom_reports": symptoms,
                        "tasks_done": done, "tasks_total": len(tasks), "warnings": warnings}
    return out


def step_lock_day(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    _, end = _bounds(run.close_date)
    row = s.get(AppSetting, DAY_LOCK_KEY)
    if row is None:
        s.add(AppSetting(key=DAY_LOCK_KEY, value=end.isoformat(), updated_by=run.requested_by))
    elif datetime.fromisoformat(row.value) < end:
        row.value, row.updated_by = end.isoformat(), run.requested_by
    return {"locked_through": end.isoformat()}


def step_baselines(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """Re-evaluate every baseline stream under the unchanged Module C safety rules."""
    svc = BaselineService(s, ctx.policy, ctx.thresholds)
    out: dict[str, Any] = {}
    for m in crew:
        streams = s.execute(select(Measurement.metric_code, Measurement.context).where(
            Measurement.astronaut_id == m.astronaut_id).distinct()).all()
        events: dict[str, int] = {}
        for metric, context in streams:
            ev = svc.maybe_update(m.astronaut_id, metric, context)
            events[ev.action] = events.get(ev.action, 0) + 1
        out[_per(m)] = {"streams": len(streams), "events": events}
    return out


def step_anomaly_persistence(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """Alerts are graded on ingest by the deterministic engine; this step lists what is still open."""
    out: dict[str, Any] = {}
    for m in crew:
        alerts = s.scalars(select(Alert).where(Alert.astronaut_id == m.astronaut_id,
                                               Alert.status != AlertStatus.RESOLVED.value).order_by(Alert.id)).all()
        out[_per(m)] = {"open_alerts": [{"id": a.id, "metric": a.metric_code, "context": a.context,
                                         "severity": a.severity, "status": a.status,
                                         "persistence_count": a.persistence_count} for a in alerts]}
    return out


def step_correlation(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    from am.research.service import run_correlation

    out: dict[str, Any] = {}
    for m in crew:
        try:
            with s.begin_nested():
                r = run_correlation(s, m.astronaut_id)
            out[_per(m)] = {"analysis_run_id": r.id}
        except Exception as exc:  # one member's analysis must not stop the crew's day from closing
            out[_per(m)] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def step_journal(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    from am.journal.service import JournalService

    out: dict[str, Any] = {}
    svc = JournalService(s)
    for m in crew:
        entry = svc.generate(m.astronaut_id, m.mission_day)
        out[_per(m)] = {"journal_entry_id": entry.id, "mission_day": m.mission_day}
    return out


def step_am_core(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """AM Core: sealed daily reports and AM's own (non-medical) decisions for the closed date."""
    from am.amcore.engine import run_cycle

    return run_cycle(s, run.close_date, crew)


def step_advance_clock(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    target = datetime.combine(run.close_date + timedelta(days=1), time(WAKE_HOUR))
    before = utcnow()
    if before >= target:
        return {"moved": False, "mission_now": before.isoformat(), "note": "mission time was already past 07:00"}
    now = advance_clock(s, to=target, actor=run.requested_by, reason=f"End Day {run.close_date.isoformat()}")
    return {"moved": True, "from": before.isoformat(), "mission_now": now.isoformat()}


def step_missions(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    from am.missions.service import MissionService

    out: dict[str, Any] = {}
    for m in crew:
        tasks = MissionService(s).ensure_today(m.astronaut_id)
        out[_per(m)] = {"mission_day": m.mission_day + 1, "tasks": len(tasks)}
    return out


def step_streaks(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    from am.missions.streaks import streak_report

    out: dict[str, Any] = {}
    for m in crew:
        rep = streak_report(s, m.astronaut_id)
        out[_per(m)] = {st.code: {"current": st.current, "longest": st.longest} for st in rep.streaks}
    return out


def step_exams_points(s: Session, ctx: Any, run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    """Adherence points for the closed day (Module M) and routine exams now overdue."""
    from am.exams.service import award_day, exams_for

    out: dict[str, Any] = {}
    for m in crew:
        added = award_day(s, m.astronaut_id, m.mission_day)
        _, views = exams_for(s, m.astronaut_id)
        out[_per(m)] = {"points_added": added,
                        "overdue_exams": [{"id": v.exam.id, "exam": v.exam.exam_code, "due_day": v.exam.due_day}
                                          for v in views if v.state == "overdue"]}
    return out


STEPS: dict[str, StepFn] = {
    "hydration": step_hydration,
    "check_logs": step_check_logs,
    "lock_day": step_lock_day,
    "baselines": step_baselines,
    "anomaly_persistence": step_anomaly_persistence,
    "correlation": step_correlation,
    "journal": step_journal,
    "am_core": step_am_core,
    "advance_clock": step_advance_clock,
    "missions": step_missions,
    "streaks": step_streaks,
    "exams_points": step_exams_points,
}
assert tuple(STEPS) == STEP_ORDER


# ------------------------------------------------------------------ orchestration
@dataclass
class EndDayResult:
    run: DayCloseRun
    replayed: bool  # True when the date was already closed and nothing ran


def _open_run(s: Session, close_date: date | None, actor: str) -> tuple[DayCloseRun, bool]:
    today = mission_date()
    target = close_date or today
    run = s.scalars(select(DayCloseRun).where(DayCloseRun.close_date == target)).first()
    if run is not None:
        return run, run.status == "completed"
    if target != today:
        raise EndDayConflict(f"The current mission date is {today.isoformat()}; "
                             f"{target.isoformat()} cannot be closed now.")
    unfinished = s.scalars(select(DayCloseRun).where(DayCloseRun.status != "completed")).first()
    if unfinished is not None:
        raise EndDayConflict(f"The End Day run for {unfinished.close_date.isoformat()} did not finish; "
                             "resume it first.")
    run = DayCloseRun(close_date=target, status="running", steps={}, results={}, requested_by=actor)
    s.add(run)
    try:
        s.flush()
    except IntegrityError as exc:
        s.rollback()
        raise EndDayConflict("Another End Day request for this date is in progress.") from exc
    audit(s, actor=actor, action="endday.started", entity="day_close_run", entity_id=run.id,
          payload={"close_date": target.isoformat()})
    s.commit()
    return run, False


def end_day(s: Session, ctx: Any, *, actor: str, close_date: date | None = None) -> EndDayResult:
    """Close `close_date` (default: the current mission date) for the whole crew."""
    if not _run_lock.acquire(blocking=False):
        raise EndDayConflict("Another End Day request is in progress.")
    try:
        run, completed = _open_run(s, close_date, actor)
        if completed:
            return EndDayResult(run, True)
        crew = crew_on(s, run.close_date)
        run.status, run.error = "running", None
        for name in STEP_ORDER:
            if run.steps.get(name, {}).get("status") == "done":
                continue
            try:
                detail = STEPS[name](s, ctx, run, crew)
                run.steps = {**run.steps, name: {"status": "done", "at": utcnow().isoformat(), "detail": detail}}
                s.commit()
            except Exception as exc:
                s.rollback()
                run = s.get(DayCloseRun, run.id)
                run.steps = {**run.steps, name: {"status": "failed", "at": utcnow().isoformat(),
                                                 "detail": {"error": f"{type(exc).__name__}: {exc}"}}}
                run.status, run.error = "failed", f"step '{name}' failed: {type(exc).__name__}: {exc}"
                audit(s, actor=actor, action="endday.failed", entity="day_close_run", entity_id=run.id,
                      payload={"step": name, "error": str(exc)})
                s.commit()
                return EndDayResult(run, False)
        run.results = _summarize(run, crew)
        run.status, run.finished_at = "completed", utcnow()
        audit(s, actor=actor, action="endday.completed", entity="day_close_run", entity_id=run.id,
              payload={"close_date": run.close_date.isoformat(), "crew": [m.astronaut_id for m in crew]})
        s.commit()
        return EndDayResult(run, False)
    finally:
        _run_lock.release()


def _summarize(run: DayCloseRun, crew: list[CrewMemberDay]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in crew:
        key = _per(m)
        per_step = {name: (run.steps.get(name, {}).get("detail") or {}).get(key) for name in STEP_ORDER}
        logs = per_step.get("check_logs") or {}
        out[key] = {"astronaut_id": m.astronaut_id, "code": m.code, "closed_mission_day": m.mission_day,
                    "warnings": logs.get("warnings", []), "steps": {k: v for k, v in per_step.items() if v is not None}}
    return out
