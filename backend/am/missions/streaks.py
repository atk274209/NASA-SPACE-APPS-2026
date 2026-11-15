"""Adherence streaks: consecutive mission days on which a routine was recorded.

Streaks reward ADHERENCE only (logging, check-ins, completing mission tasks):
  * A day counts because a record exists, never because of the value it holds. A short
    exercise session or an unusual heart rate counts exactly like any other entry, so a
    streak can never become a reason to skip, delay or adjust a measurement.
  * Reporting a symptom counts as a check-in. Telling the crew how you feel must never
    cost a streak.
  * No penalties: a day without the record only restarts the current run. The longest
    run and the milestones already reached are kept for the whole mission.

Day 1 = mission start date (UTC), the convention shared with the journal and daily
missions. Streaks are computed on read from stored records and are never stored. The only
write here is `sync_today_tasks`, which applies the Missions page's own data-verified
task completion so both pages agree.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import Date, extract, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from am.db.base import utcnow
from am.db.enums import DataQuality, MeasurementContext
from am.db.models import Astronaut, Measurement, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import DailyTask
from am.missions.service import MissionService
from am.research.series import current_mission_day

# Engagement milestones named in the M5 spec. They mark a routine kept up (adherence);
# they are not health targets and not clinical or behavioural-science thresholds.
ADHERENCE_MILESTONE_DAYS: tuple[int, ...] = (3, 7, 14, 30)
CALENDAR_DAYS = 30
# "Morning" for the morning check means before 12:00 UTC, the same cut-off the daily
# missions use for the morning-check task. A scheduling convention, not a physiological window.
MORNING_CUTOFF_HOUR_UTC = 12

RULES = ("A streak counts consecutive mission days (UTC) with the routine recorded, never the values, and a symptom "
         "report always counts as a check-in.")
MILESTONE_NOTE = ("A day without a record only restarts the current run: the longest run and the adherence milestones "
                  "(3, 7, 14 and 30 days in a row) stay. Milestones reward keeping a routine; they are not health "
                  "targets.")

DayState = Literal["done", "missed", "no_data", "pending"]


@dataclass(frozen=True)
class StreakType:
    code: str
    title: str
    rule: str


STREAK_TYPES: tuple[StreakType, ...] = (
    StreakType("checkin", "Daily check-in",
               "A check-in or a symptom report recorded that day. Reporting a symptom always counts."),
    StreakType("morning_check", "Morning health check",
               "A resting heart rate reading taken before 12:00 UTC, whatever its value."),
    StreakType("exercise_logged", "Exercise logged",
               "An exercise duration daily total recorded for the day, whatever its value."),
    StreakType("hydration_logged", "Hydration logged",
               "A water intake daily total recorded for the day, whatever its value."),
    StreakType("missions_complete", "Priority missions complete",
               "Every priority task on the day's mission list done; on a day without priority tasks, every "
               "routine task. Days without a mission list show as no data."),
)


@dataclass
class StreakDay:
    mission_day: int
    day_date: date
    state: DayState
    simulated: bool  # the day counted only because of records from the AMC simulator


@dataclass
class Milestone:
    days: int
    label: str
    reached: bool
    reached_day: int | None  # mission day on which a run first reached this length


@dataclass
class Streak:
    code: str
    title: str
    rule: str
    current: int
    at_risk: bool  # today is still open and a run is waiting to be extended
    today_done: bool
    longest: int
    longest_days: list[int]  # [first, last] mission day of the longest run, or [] if none
    total_done: int
    next_milestone: int | None
    milestones: list[Milestone]
    calendar: list[StreakDay]


@dataclass
class StreakReport:
    astronaut_id: int
    mission_id: int
    mission_start: date
    mission_day: int
    today: date
    simulated: bool
    window_days: int
    rules: str
    milestone_note: str
    streaks: list[Streak]


def _run_ending(done: dict[int, bool], day: int) -> int:
    n = 0
    while day - n >= 1 and (day - n) in done:
        n += 1
    return n


def summarize(kind: StreakType, done: dict[int, bool], data_days: set[int], *, today: int,
              start: date) -> Streak:
    """Turn the days a routine was recorded into runs, a calendar and milestones.

    `done` maps a mission day to True when only simulated records made it count.
    `data_days` are the days AMC holds any relevant record for. A day with records but
    without this routine is 'missed'; a day with no records at all is 'no_data', because
    AMC cannot tell whether the routine happened (for example, data that never synced).
    Today is 'pending' until it is done: an unfinished day is never shown as missed, and
    a reading taken earlier today can still be entered later with its own time.
    """
    longest = longest_end = run = 0
    reached_on: dict[int, int] = {}
    for d in range(1, today + 1):
        run = run + 1 if d in done else 0
        if run and run >= longest:  # >= keeps the most recent of equally long runs
            longest, longest_end = run, d
        if run in ADHERENCE_MILESTONE_DAYS:
            reached_on.setdefault(run, d)

    today_done = today in done
    # An open today does not end the run: the run up to yesterday still counts.
    current = _run_ending(done, today if today_done else today - 1)

    def state(d: int) -> DayState:
        if d in done:
            return "done"
        if d == today:
            return "pending"
        return "missed" if d in data_days else "no_data"

    first = max(1, today - CALENDAR_DAYS + 1)
    return Streak(
        code=kind.code,
        title=kind.title,
        rule=kind.rule,
        current=current,
        at_risk=not today_done and current > 0,
        today_done=today_done,
        longest=longest,
        longest_days=[longest_end - longest + 1, longest_end] if longest else [],
        total_done=len(done),
        next_milestone=next((m for m in ADHERENCE_MILESTONE_DAYS if m > current), None),
        milestones=[Milestone(m, f"{m}-day adherence milestone", m in reached_on, reached_on.get(m))
                    for m in ADHERENCE_MILESTONE_DAYS],
        calendar=[StreakDay(d, start + timedelta(days=d - 1), state(d), done.get(d, False))
                  for d in range(first, today + 1)],
    )


# ---------------------------------------------------------------------- evidence
@dataclass(frozen=True)
class _Scope:
    """One astronaut's mission days 1..today."""

    astronaut_id: int
    start: date
    today: int

    def day_of(self, d: date) -> int:
        return (d - self.start).days + 1

    def bounds(self) -> tuple[datetime, datetime]:
        lo = datetime.combine(self.start, time.min)
        return lo, lo + timedelta(days=self.today)  # [start of day 1, end of today)


def _record_days(s: Session, scope: _Scope, ts: InstrumentedAttribute[datetime],
                 simulated: InstrumentedAttribute[bool], *where: ColumnElement[bool]) -> dict[int, bool]:
    """Mission days with at least one matching row, grouped in SQL by UTC date.

    Maps each day to True when every matching row that day is simulated.
    """
    lo, hi = scope.bounds()
    day = func.date(ts, type_=Date)
    out: dict[int, bool] = {}
    for d, sim in s.execute(select(day, simulated).where(ts >= lo, ts < hi, *where).group_by(day, simulated)):
        md = scope.day_of(d)
        if 1 <= md <= scope.today:
            out[md] = out.get(md, True) and bool(sim)
    return out


def _merge(*days: dict[int, bool]) -> dict[int, bool]:
    """Union of day maps; a day stays 'simulated only' if every source that counted it was simulated."""
    out: dict[int, bool] = {}
    for m in days:
        for d, simulated in m.items():
            out[d] = out.get(d, True) and simulated
    return out


def _checkin_days(s: Session, scope: _Scope) -> dict[int, bool]:
    """Any self-report (ratings or a note) or any symptom report counts as checking in."""
    return _merge(
        _record_days(s, scope, SelfReport.recorded_at, SelfReport.is_simulated,
                     SelfReport.astronaut_id == scope.astronaut_id),
        _record_days(s, scope, SymptomReport.reported_at, SymptomReport.is_simulated,
                     SymptomReport.astronaut_id == scope.astronaut_id),
    )


def _measurement_days(s: Session, scope: _Scope, metric: str | None = None, context: str | None = None, *,
                      before_noon: bool = False, include_rejected: bool = False) -> dict[int, bool]:
    """Days with a measurement. Rejected entries (failed validation) do not count as logged,
    matching the data-verified completion of daily mission tasks."""
    where: list[ColumnElement[bool]] = [Measurement.astronaut_id == scope.astronaut_id]
    if metric is not None:
        where.append(Measurement.metric_code == metric)
    if context is not None:
        where.append(Measurement.context == context)
    if not include_rejected:
        where.append(Measurement.quality != DataQuality.REJECTED.value)
    if before_noon:
        where.append(extract("hour", Measurement.recorded_at) < MORNING_CUTOFF_HOUR_UTC)
    return _record_days(s, scope, Measurement.recorded_at, Measurement.is_simulated, *where)


def _mission_task_days(s: Session, scope: _Scope) -> tuple[dict[int, bool], set[int]]:
    """Days whose top-priority tasks are all done, and the days that have a mission list.

    The spec counts a day when every priority-1 task is done. Many days have no
    priority-1 task, and an empty requirement must not count as completion, so on those
    days the highest priority level present (routine tasks) is required instead. Skipped
    tasks are not done. Tasks come from AMC's planner, never from the simulator, so they
    are never marked simulated.
    """
    rows = s.execute(select(DailyTask.mission_day, DailyTask.priority, DailyTask.status).where(
        DailyTask.astronaut_id == scope.astronaut_id, DailyTask.mission_day >= 1,
        DailyTask.mission_day <= scope.today))
    by_day: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for day, priority, status in rows:
        by_day[day].append((priority, status))
    done: dict[int, bool] = {}
    for day, tasks in by_day.items():
        top = min(p for p, _ in tasks)
        if all(status == "done" for p, status in tasks if p == top):
            done[day] = False
    return done, set(by_day)


# ---------------------------------------------------------------------- report
def streak_report(s: Session, astronaut_id: int, now: datetime | None = None) -> StreakReport:
    astronaut = s.get(Astronaut, astronaut_id)
    if astronaut is None:
        raise LookupError("astronaut not found")
    mission = s.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                        .order_by(Mission.id.desc()).limit(1)).first()
    if mission is None:
        raise ValueError("astronaut has no mission; streaks count mission days")
    now = now or utcnow()
    today = (now.date() - mission.start_date).days + 1
    if today < 1:
        raise ValueError(f"the mission starts on {mission.start_date.isoformat()}; streaks begin on mission day 1")
    scope = _Scope(astronaut_id, mission.start_date, today)

    rest, daily = MeasurementContext.REST.value, MeasurementContext.DAILY.value
    checkins = _checkin_days(s, scope)
    task_done, task_days = _mission_task_days(s, scope)
    # Days AMC holds any record for this astronaut; a day outside this set is 'no data'.
    activity = set(_measurement_days(s, scope, include_rejected=True)) | set(checkins) | task_days
    evidence: dict[str, tuple[dict[int, bool], set[int]]] = {
        "checkin": (checkins, activity),
        "morning_check": (_measurement_days(s, scope, "heart_rate", rest, before_noon=True), activity),
        "exercise_logged": (_measurement_days(s, scope, "exercise_duration", daily), activity),
        "hydration_logged": (_measurement_days(s, scope, "water_intake", daily), activity),
        "missions_complete": (task_done, task_days),
    }
    return StreakReport(
        astronaut_id=astronaut_id,
        mission_id=mission.id,
        mission_start=mission.start_date,
        mission_day=today,
        today=now.date(),
        simulated=astronaut.is_simulated or mission.is_simulated,
        window_days=CALENDAR_DAYS,
        rules=RULES,
        milestone_note=MILESTONE_NOTE,
        streaks=[summarize(t, *evidence[t.code], today=today, start=mission.start_date) for t in STREAK_TYPES],
    )


def sync_today_tasks(s: Session, astronaut_id: int) -> None:
    """Apply the Missions page's own data-verified completion to today's existing tasks.

    Without this, a task the Missions page would already show as verified from recorded
    data could still read as pending here until that page refreshes. It never creates
    tasks: a day without a mission list stays 'no data'.
    """
    today = current_mission_day(s, astronaut_id)
    if today is not None and today >= 1:
        MissionService(s).sync_completion(astronaut_id, today)
