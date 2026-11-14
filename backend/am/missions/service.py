"""Adaptive daily missions (rule-based, deterministic).

Tasks are monitoring, logging and protocol-adherence prompts. AM never prescribes
medication, changes treatment, or sets exercise/nutrition targets: those come from the
mission's approved protocols, referenced here by placeholder IDs marked DEMO until real
protocols are configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.enums import AlertStatus, DataQuality, Severity
from am.db.models import Alert, Astronaut, Measurement
from am.db.models_ai import SelfReport
from am.db.models_research import DailyTask
from am.research.reference import REFERENCE_DAYS, reference_for
from am.research.series import DailySeries, build_daily_series, current_mission_day, day_bounds, mission_start

PROTOCOL_LABEL = "DEMO protocol placeholder: replace with the mission's approved protocol"


@dataclass(frozen=True)
class Template:
    code: str
    category: str
    title: str
    detail: str
    protocol_ref: str
    completion_rule: str  # manual | checkin | measurement:<metric>:<context>[:before_noon]


T = {t.code: t for t in [
    Template("morning_check", "monitoring", "Morning health check",
             "Record a resting heart rate reading after waking, before exercise.",
             "AM-DEMO-MON-01", "measurement:heart_rate:rest:before_noon"),
    Template("daily_checkin", "mental", "Daily check-in",
             "Rate mood, stress, fatigue, sleep quality and motivation in the companion.",
             "AM-DEMO-BH-01", "checkin"),
    Template("exercise_session", "exercise", "Complete today's scheduled exercise session",
             "Follow the exercise prescription in your mission protocol; AM does not change it.",
             "AM-DEMO-EX-01", "measurement:exercise_duration:daily"),
    Template("hydration_log", "nutrition", "Log water intake",
             "Record today's total water intake.", "AM-DEMO-NUT-01", "measurement:water_intake:daily"),
    Template("sleep_prep", "sleep", "Sleep preparation routine",
             "Follow the mission sleep-hygiene guidance before your scheduled sleep period.",
             "AM-DEMO-SLP-01", "manual"),
    Template("sleep_log", "sleep", "Log last night's sleep",
             "Make sure last night's sleep duration is recorded.", "AM-DEMO-SLP-02",
             "measurement:sleep_duration:daily"),
    Template("alert_review", "medical", "Review open alert with the medical team",
             "Discuss the open alert during your next medical conference.", "AM-DEMO-MED-01", "manual"),
    Template("behavioral_support", "mental", "Use a behavioral-health support option",
             "Choose one of the support options in your mission behavioral-health plan.", "AM-DEMO-BH-02", "manual"),
    Template("daily_reflection", "mental", "Daily reflection",
             "Add a short note about your day in the check-in.", "AM-DEMO-BH-03", "manual"),
]}


@dataclass
class Plan:
    code: str
    priority: int
    reason: str
    refs: list[dict[str, Any]]


def _recent_label(s: DailySeries | None, upto: int, label: str, need: int = 2, window: int = 3) -> list[int]:
    if s is None:
        return []
    ref = reference_for(s)
    if ref is None:
        return []
    days = [d for d in range(max(REFERENCE_DAYS + 1, upto - window + 1), upto + 1) if d in s.values]
    hits = [d for d in days if ref.label(s.values[d]) == label]
    return hits if len(hits) >= need else []


class MissionService:
    def __init__(self, session: Session):
        self.s = session

    def plan(self, astronaut_id: int) -> list[Plan]:
        today = current_mission_day(self.s, astronaut_id)
        if today is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        series = build_daily_series(self.s, astronaut_id, until_day=today - 1)
        last = today - 1
        plans = [
            Plan("morning_check", 2, "Daily monitoring routine.", []),
            Plan("daily_checkin", 2, "Daily self-report.", []),
            Plan("exercise_session", 2, "Scheduled every mission day.", []),
            Plan("hydration_log", 3, "Daily intake record.", []),
        ]

        low_ex = _recent_label(series.get("exercise_duration:daily"), last, "below reference")
        if low_ex:
            plans[2] = Plan("exercise_session", 1,
                            f"Exercise duration was below your mission-start reference on days {low_ex}.",
                            [{"type": "daily_series", "key": "exercise_duration:daily", "days": low_ex}])

        low_sleep = _recent_label(series.get("sleep_duration:daily"), last, "below reference")
        poor_q = [d for d in range(last - 2, last + 1)
                  if series.get("selfreport:sleep_quality") and
                  series["selfreport:sleep_quality"].values.get(d, 99) <= 2]
        if low_sleep or len(poor_q) >= 2:
            why = []
            refs = []
            if low_sleep:
                why.append(f"sleep duration below your mission-start reference on days {low_sleep}")
                refs.append({"type": "daily_series", "key": "sleep_duration:daily", "days": low_sleep})
            if len(poor_q) >= 2:
                why.append(f"poor sleep quality reported on days {poor_q}")
                refs.append({"type": "daily_series", "key": "selfreport:sleep_quality", "days": poor_q})
            reason = "Prioritised because " + " and ".join(why) + "."
            plans += [Plan("sleep_log", 1, reason, refs), Plan("sleep_prep", 1, reason, refs)]
        else:
            plans.append(Plan("sleep_log", 3, "Daily sleep record.", []))

        low_water = _recent_label(series.get("water_intake:daily"), last, "below reference")
        if low_water:
            plans = [p for p in plans if p.code != "hydration_log"]
            plans.append(Plan("hydration_log", 1, f"Water intake was below your mission-start reference on days "
                                                   f"{low_water}.",
                              [{"type": "daily_series", "key": "water_intake:daily", "days": low_water}]))

        alerts = self.s.scalars(select(Alert).where(Alert.astronaut_id == astronaut_id,
                                                    Alert.status != AlertStatus.RESOLVED.value)).all()
        serious = [a for a in alerts if Severity(a.peak_severity).rank >= Severity.WARNING.rank]
        if serious:
            plans.append(Plan("alert_review", 1, f"{len(serious)} open alert(s) at WARNING or above: "
                                                 + ", ".join(f"#{a.id} {a.metric_code.replace('_', ' ')}"
                                                             for a in serious) + ".",
                              [{"type": "alert", "id": a.id} for a in serious]))

        stress = series.get("selfreport:stress")
        high_stress = [d for d in range(last - 2, last + 1) if stress and stress.values.get(d, 0) >= 4]
        if high_stress:
            plans.append(Plan("behavioral_support", 1, f"High stress self-reported on days {high_stress}.",
                              [{"type": "daily_series", "key": "selfreport:stress", "days": high_stress}]))
        plans.append(Plan("daily_reflection", 3, "Daily routine.", []))
        return sorted(plans, key=lambda p: p.priority)

    def ensure_today(self, astronaut_id: int) -> list[DailyTask]:
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        today = current_mission_day(self.s, astronaut_id)
        if today is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        existing = {t.template_code: t for t in self.s.scalars(select(DailyTask).where(
            DailyTask.astronaut_id == astronaut_id, DailyTask.mission_day == today))}
        for p in self.plan(astronaut_id):
            tpl = T[p.code]
            task = existing.get(p.code)
            if task is None:
                task = DailyTask(astronaut_id=astronaut_id, mission_day=today, template_code=tpl.code,
                                 category=tpl.category, title=tpl.title, detail=tpl.detail,
                                 protocol_ref=tpl.protocol_ref, completion_rule=tpl.completion_rule,
                                 priority=p.priority, reason=p.reason, reason_refs=p.refs)
                self.s.add(task)
                existing[p.code] = task
            elif task.status == "pending":  # adapt priority/reason as new data arrives
                task.priority, task.reason, task.reason_refs = p.priority, p.reason, p.refs
        self.s.flush()
        self.sync_completion(astronaut_id, today)
        return sorted(existing.values(), key=lambda t: (t.status == "done", t.priority, t.id))

    def sync_completion(self, astronaut_id: int, day: int) -> None:
        """Mark data-verifiable tasks done when the stored data shows they happened."""
        start = mission_start(self.s, astronaut_id)
        d0, d1 = day_bounds(start, day)
        for t in self.s.scalars(select(DailyTask).where(DailyTask.astronaut_id == astronaut_id,
                                                        DailyTask.mission_day == day,
                                                        DailyTask.status == "pending")):
            rule = t.completion_rule
            ok = False
            if rule == "checkin":
                ok = self.s.scalars(select(SelfReport.id).where(
                    SelfReport.astronaut_id == astronaut_id, SelfReport.recorded_at >= d0,
                    SelfReport.recorded_at < d1).limit(1)).first() is not None
            elif rule.startswith("measurement:"):
                _, metric, ctx, *rest = rule.split(":")
                end = d0.replace(hour=12) if rest == ["before_noon"] else d1
                ok = self.s.scalars(select(Measurement.id).where(
                    Measurement.astronaut_id == astronaut_id, Measurement.metric_code == metric,
                    Measurement.context == ctx, Measurement.quality != DataQuality.REJECTED.value,
                    Measurement.recorded_at >= d0, Measurement.recorded_at < end).limit(1)).first() is not None
            if ok:
                t.status, t.completed_by, t.completed_at = "done", "data", utcnow()
        self.s.flush()

    def set_status(self, task_id: int, status: str) -> DailyTask:
        if status not in ("done", "pending", "skipped"):
            raise ValueError("status must be done, pending or skipped")
        t = self.s.get(DailyTask, task_id)
        if t is None:
            raise LookupError("task not found")
        if t.completed_by == "data" and status != "done":
            raise ValueError("this task was verified from recorded data and cannot be reopened manually")
        t.status = status
        t.completed_by = "astronaut" if status == "done" else None
        t.completed_at = utcnow() if status == "done" else None
        self.s.flush()
        return t
