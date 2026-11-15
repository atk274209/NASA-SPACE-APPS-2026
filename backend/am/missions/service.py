"""Adaptive daily missions (rule-based, deterministic).

Tasks are monitoring, logging and protocol-adherence prompts. AMC never prescribes
medication, changes treatment, or sets exercise/nutrition targets: those come from the
mission's approved protocols, referenced here by placeholder IDs marked DEMO until real
protocols are configured.

Personalised missions: each active symptom report adds that symptom's catalog task templates
(logging, protocol adherence, telling the crew medical officer) for today. A template code
that is already planned is reused and the symptom is appended to its reason, so a symptom
never duplicates a task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.enums import AlertStatus, DataQuality, Severity
from am.db.models import Alert, Astronaut, Measurement
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import DailyTask
from am.research.reference import REFERENCE_DAYS, reference_for
from am.research.series import DailySeries, build_daily_series, current_mission_day, day_bounds, day_of, \
    mission_start
from am.symptoms import catalog as symptom_catalog

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
             "Follow the exercise prescription in your mission protocol; AMC does not change it.",
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

# Categories for the symptom-specific catalog templates. Generic codes (hydration_log,
# exercise_session, ...) keep the category of their mission template above.
SYMPTOM_TASK_CATEGORY = {
    "bowel_log": "monitoring",
    "meal_log": "nutrition",
    "symptom_daily_log": "monitoring",
    "temperature_check": "monitoring",
    "symptom_followup_cmo": "medical",
    "symptom_contact_now": "medical",
    "crew_conflict_note": "mental",
}
CONTACT_NOW = "symptom_contact_now"
# Every task template the catalog defines, by code (the first symptom that defines a code wins).
_CATALOG_TASKS: dict[str, symptom_catalog.TaskTemplate] = {}
for _s in symptom_catalog.SYMPTOMS:
    for _t in _s.tasks:
        _CATALOG_TASKS.setdefault(_t.code, _t)


# Action tasks (exercise, sleep routine, behavioural support) are never added or prioritised because of a
# symptom: that would be an activity recommendation in response to a symptom, i.e. treatment advice
# (review finding R05). A symptom only adds logging and reporting prompts.
_SYMPTOM_ACTION_TASKS = frozenset({"exercise_session", "sleep_prep", "behavioral_support"})


def _from_catalog(t: symptom_catalog.TaskTemplate) -> Template:
    """Reuse the generic mission template when the code exists; otherwise build it from the catalog."""
    if t.code in T:
        return T[t.code]
    return Template(t.code, SYMPTOM_TASK_CATEGORY.get(t.code, "monitoring"), t.title, t.detail, t.protocol_ref,
                    t.completion_rule)


@dataclass
class Plan:
    code: str
    priority: int
    reason: str
    refs: list[dict[str, Any]]
    template: Template | None = field(default=None)  # None: the generic template T[code]


def _recent_label(s: DailySeries | None, upto: int, label: str, need: int = 2, window: int = 3) -> list[int]:
    if s is None:
        return []
    ref = reference_for(s)
    if ref is None:
        return []
    days = [d for d in range(max(REFERENCE_DAYS + 1, upto - window + 1), upto + 1) if d in s.values]
    hits = [d for d in days if ref.label(s.values[d]) == label]
    return hits if len(hits) >= need else []


def _symptom_reason(sym: symptom_catalog.Symptom, r: SymptomReport, start: date | None) -> str:
    """Names the symptom, the astronaut's own severity rating and the day it was reported."""
    when = f"{r.reported_at:%Y-%m-%d}"
    if start is not None and day_of(start, r.reported_at) >= 1:
        when = f"mission day {day_of(start, r.reported_at)} ({when})"
    reason = f"Symptom: {sym.label.lower()}, self-rated {r.severity}, reported on {when}."
    if sym.report_promptly:
        reason += " It is on the DEMO report-promptly list."
    return reason


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

        by_code = {p.code: p for p in plans}
        for p in self._symptom_plans(astronaut_id):
            cur = by_code.get(p.code)
            if cur is None:
                by_code[p.code] = p
            else:  # reuse the planned task: keep the higher priority and add the symptom to the reason
                cur.priority = min(cur.priority, p.priority)
                cur.reason = f"{cur.reason} {p.reason}"
                cur.refs = [*cur.refs, *p.refs]
        return sorted(by_code.values(), key=lambda p: p.priority)

    def _symptom_plans(self, astronaut_id: int) -> list[Plan]:
        """Monitoring and reporting prompts for each active symptom report; never treatment.

        Priority follows the astronaut's own rating: 1 for moderate or severe, 2 for mild. A
        severe symptom, or one on the DEMO report-promptly list, always adds the contact-now
        task at priority 1.
        """
        start = mission_start(self.s, astronaut_id)
        reports = self.s.scalars(select(SymptomReport).where(
            SymptomReport.astronaut_id == astronaut_id, SymptomReport.status == "active")
            .order_by(SymptomReport.reported_at, SymptomReport.id)).all()
        out: list[Plan] = []
        for r in reports:
            sym = symptom_catalog.get(r.symptom_code)
            if sym is None:
                continue
            reason = _symptom_reason(sym, r, start)
            priority = 1 if r.severity in ("moderate", "severe") else 2
            templates = list(sym.tasks)
            if (sym.report_promptly or r.severity == "severe") and all(t.code != CONTACT_NOW for t in templates):
                templates.append(_CATALOG_TASKS[CONTACT_NOW])
            for t in templates:
                if t.code in _SYMPTOM_ACTION_TASKS:  # review finding R05: a symptom never prompts an action
                    continue
                out.append(Plan(t.code, 1 if t.code == CONTACT_NOW else priority, reason,
                                [{"type": "symptom_report", "id": r.id}], _from_catalog(t)))
        return out

    def ensure_today(self, astronaut_id: int) -> list[DailyTask]:
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        today = current_mission_day(self.s, astronaut_id)
        if today is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        existing = {t.template_code: t for t in self.s.scalars(select(DailyTask).where(
            DailyTask.astronaut_id == astronaut_id, DailyTask.mission_day == today))}
        plans = self.plan(astronaut_id)
        for p in plans:
            tpl = p.template or T[p.code]
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
        self._drop_resolved_symptom_tasks(existing, {p.code for p in plans})
        self.s.flush()
        self.sync_completion(astronaut_id, today)
        return sorted(existing.values(), key=lambda t: (t.status == "done", t.priority, t.id))

    def _drop_resolved_symptom_tasks(self, existing: dict[str, DailyTask], planned: set[str]) -> None:
        """Remove open tasks that existed only for symptoms that are no longer active.

        Without this, a resolved symptom would leave its prompts (even "contact the crew medical
        officer now") open for the rest of the day. Completed or skipped tasks stay as history.
        """
        for code, task in list(existing.items()):
            refs = task.reason_refs or []
            if (code not in planned and task.status == "pending" and refs
                    and all(r.get("type") == "symptom_report" for r in refs)):
                self.s.delete(task)
                del existing[code]

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
