"""Routine examinations and adherence points (master prompt Module M).

DEMO intervals from config/routine_exams.demo.yaml. AMC schedules, reminds and stores what the
astronaut enters; it never fabricates or interprets a result. Points reward adherence only (logging,
check-ins, exams on time) and never depend on a health value.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from am.audit import audit
from am.config import REPO_ROOT
from am.db.base import utcnow
from am.db.models_ai import SelfReport
from am.db.models_m6b import PointsEntry, RoutineExam
from am.db.models_research import DailyTask
from am.research.series import current_mission_day, day_bounds, mission_start

CONFIG_PATH = REPO_ROOT / "config" / "routine_exams.demo.yaml"
LOOKAHEAD_DAYS = 7


@lru_cache
def load_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for e in data["exams"]:
        if e["every_days"] < 1 or e["first_day"] < 1:
            raise ValueError(f"exam {e['code']}: days must be >= 1")
    return data


def exam_def(code: str) -> dict[str, Any] | None:
    return next((e for e in load_config()["exams"] if e["code"] == code), None)


def ensure_schedule(s: Session, astronaut_id: int) -> int | None:
    """Create exam slots up to LOOKAHEAD_DAYS ahead. Returns today's mission day (None without a mission)."""
    today = current_mission_day(s, astronaut_id)
    if today is None:
        return None
    have = set(s.execute(select(RoutineExam.exam_code, RoutineExam.due_day)
                         .where(RoutineExam.astronaut_id == astronaut_id)).all())
    for e in load_config()["exams"]:
        day = e["first_day"]
        while day <= today + LOOKAHEAD_DAYS:
            if (e["code"], day) not in have:
                s.add(RoutineExam(astronaut_id=astronaut_id, exam_code=e["code"], due_day=day))
            day += e["every_days"]
    s.flush()
    return today


def state_of(x: RoutineExam, today: int) -> str:
    if x.status == "done":
        return "done"
    if x.due_day < today:
        return "overdue"
    return "due" if x.due_day == today else "upcoming"


@dataclass
class ExamView:
    exam: RoutineExam
    state: str
    title: str
    detail: str
    procedure_ref: str


def exams_for(s: Session, astronaut_id: int) -> tuple[int | None, list[ExamView]]:
    today = ensure_schedule(s, astronaut_id)
    if today is None:
        return None, []
    rows = s.scalars(select(RoutineExam).where(RoutineExam.astronaut_id == astronaut_id)
                     .order_by(RoutineExam.due_day, RoutineExam.exam_code)).all()
    out = []
    for x in rows:
        d = exam_def(x.exam_code) or {"title": x.exam_code, "detail": "", "procedure_ref": ""}
        out.append(ExamView(x, state_of(x, today), d["title"], d["detail"], d["procedure_ref"]))
    return today, out


class ExamError(Exception):
    """Refused; safe to show."""


def record_result(s: Session, exam_id: int, result_text: str, *, actor: str) -> RoutineExam:
    x = s.get(RoutineExam, exam_id)
    if x is None:
        raise LookupError("exam not found")
    if x.status == "done":
        raise ExamError("this exam already has a result")
    text = result_text.strip()
    if not text:
        raise ExamError("enter the result as recorded by the procedure")
    today = current_mission_day(s, x.astronaut_id) or x.due_day
    if x.due_day > today:
        raise ExamError("this exam is not due yet")
    x.status, x.done_day, x.completed_at, x.result_text, x.entered_by = "done", today, utcnow(), text[:4000], actor
    pts = load_config()["points"]
    on_time = today <= x.due_day
    award(s, x.astronaut_id, "exam_on_time" if on_time else "exam_late", f"exam:{x.id}",
          pts["exam_on_time" if on_time else "exam_late"], today)
    audit(s, actor=actor, action="exam.result_entered", entity="routine_exam", entity_id=x.id,
          payload={"exam_code": x.exam_code, "due_day": x.due_day, "done_day": today, "on_time": on_time})
    s.flush()
    return x


def award(s: Session, astronaut_id: int, kind: str, ref: str, points: int, day: int | None) -> bool:
    """Idempotent: an event earns points once."""
    if s.scalar(select(PointsEntry.id).where(PointsEntry.astronaut_id == astronaut_id, PointsEntry.kind == kind,
                                             PointsEntry.ref == ref)) is not None:
        return False
    try:
        with s.begin_nested():
            s.add(PointsEntry(astronaut_id=astronaut_id, kind=kind, ref=ref, points=points, mission_day=day))
    except IntegrityError:
        return False
    return True


def award_day(s: Session, astronaut_id: int, day: int) -> int:
    """Points for one closed mission day: each done daily task and the check-in. Returns points added."""
    pts = load_config()["points"]
    added = 0
    for t in s.scalars(select(DailyTask).where(DailyTask.astronaut_id == astronaut_id, DailyTask.mission_day == day,
                                               DailyTask.status == "done")):
        added += pts["daily_task_done"] * award(s, astronaut_id, "daily_task_done", f"task:{t.id}",
                                                pts["daily_task_done"], day)
    start = mission_start(s, astronaut_id)
    if start is not None:
        d0, d1 = day_bounds(start, day)
        if s.scalar(select(func.count()).select_from(SelfReport).where(
                SelfReport.astronaut_id == astronaut_id, SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1)):
            added += pts["checkin_done"] * award(s, astronaut_id, "checkin_done", f"checkin:{day}",
                                                 pts["checkin_done"], day)
    s.flush()
    return added


def points_summary(s: Session, astronaut_id: int) -> dict[str, Any]:
    total = s.scalar(select(func.coalesce(func.sum(PointsEntry.points), 0))
                     .where(PointsEntry.astronaut_id == astronaut_id)) or 0
    size = load_config()["level_size"]
    recent = s.scalars(select(PointsEntry).where(PointsEntry.astronaut_id == astronaut_id)
                       .order_by(PointsEntry.id.desc()).limit(10)).all()
    return {"points": int(total), "level": int(total) // size + 1, "next_level_at": (int(total) // size + 1) * size,
            "recent": [{"kind": r.kind, "points": r.points, "mission_day": r.mission_day} for r in recent],
            "rule": "Points reward logging, check-ins and exams on time. They never depend on a health value."}
