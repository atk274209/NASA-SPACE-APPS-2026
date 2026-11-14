"""Mission journal: deterministic structured day summaries from stored data.

Every line refers to stored records (daily series keys + days, alerts, tasks, self-reports,
symptom reports). Patterns that persist (>= PATTERN_DAYS consecutive days outside the
mission-start reference) are written to LONG-TERM memory, deterministically.

Each day is also marked red / yellow / green / none by the DEMO rules in
config/day_levels.demo.yaml (am/journal/levels.py), using only records known by the end of
that day. The narrative is written separately (am/journal/narrative.py); regenerating a day
marks its narrative outdated when the facts it was written from have changed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
from scipy.stats import spearmanr, theilslopes
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.db.enums import DataQuality, Severity
from am.db.models import Alert, Astronaut, BaselineAudit, Measurement
from am.db.models_ai import Conversation, MemoryItem, Message, SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import DailyTask, JournalEntry
from am.journal.levels import AlertFact, DayFacts, RatingFact, SymptomFact, assess_day, load_day_level_rules
from am.journal.narrative import refresh_outdated
from am.research.reference import REFERENCE_DAYS, reference_for
from am.research.series import (
    DailySeries,
    build_daily_series,
    current_mission_day,
    day_bounds,
    day_of,
    mission_start,
)
from am.symptoms import catalog

# journal-2: symptoms section, day level, and new-alert severity as of that day (was the alert's
# later peak, which leaked hindsight into historical days)
GENERATOR_VERSION = "journal-2"
PATTERN_DAYS = 5
FLAG_RUN_DAYS = 3
MAX_SYMPTOM_NOTE_CHARS = 300

PHYSICAL = ["exercise_duration:daily", "step_count:daily", "sleep_duration:daily", "heart_rate:rest",
            "heart_rate:sleep", "hrv_rmssd:rest", "spo2:rest", "body_temperature:rest", "water_intake:daily",
            "energy_intake:daily", "blood_glucose:fasting"]
MENTAL = ["selfreport:mood", "selfreport:stress", "selfreport:fatigue", "selfreport:sleep_quality",
          "selfreport:motivation"]


def _run_length(s: DailySeries, day: int, label: str) -> tuple[int, int]:
    """Consecutive days ending at `day` whose value has `label` vs reference. Returns (length, start_day)."""
    ref = reference_for(s)
    if ref is None:
        return 0, day
    n, d = 0, day
    while d in s.values and d > REFERENCE_DAYS and ref.label(s.values[d]) == label:
        n += 1
        d -= 1
    return n, d + 1


def _trend_7d(s: DailySeries, day: int) -> dict[str, Any] | None:
    days = [d for d in range(day - 6, day + 1) if d in s.values]
    if len(days) < 6:
        return None
    vals = [s.values[d] for d in days]
    if np.ptp(vals) == 0:
        return None
    r = spearmanr(days, vals)
    if float(r.pvalue) >= 0.05:
        return None
    slope = float(theilslopes(vals, days).slope)
    return {"rho": round(float(r.statistic), 2), "p": round(float(r.pvalue), 4),
            "slope_per_day": round(slope, 3), "days": [days[0], days[-1]]}


def _resolved_before(r: SymptomReport, t: datetime) -> bool:
    """Whether a symptom report was resolved before `t`.

    A report marked resolved without a timestamp counts as resolved: its time is unknown, and
    showing it as ongoing on every later day would invent a duration.
    """
    if r.resolved_at is not None:
        return r.resolved_at < t
    return r.status == "resolved"


def _symptom_fact(r: SymptomReport, start: date) -> SymptomFact:
    sym = catalog.get(r.symptom_code)
    return SymptomFact(id=r.id, code=r.symptom_code, label=sym.label if sym else r.symptom_code.replace("_", " "),
                       category=sym.category if sym else "unknown", severity=r.severity,
                       report_promptly=bool(sym and sym.report_promptly), reported_at=r.reported_at,
                       reported_day=day_of(start, r.reported_at), resolved_at=r.resolved_at)


def _symptom_record(f: SymptomFact, r: SymptomReport, day_end: datetime) -> dict[str, Any]:
    """Journal copy of a symptom report, with its status as of the end of the day (no hindsight)."""
    resolved = _resolved_before(r, day_end)
    sym = catalog.get(r.symptom_code)
    return {"id": r.id, "code": r.symptom_code, "label": f.label, "category": f.category,
            "body_system": sym.body_system if sym else None, "severity": r.severity,
            "report_promptly": f.report_promptly, "reported_at": r.reported_at.isoformat(),
            "reported_day": f.reported_day, "source": r.source, "note": (r.note or "")[:MAX_SYMPTOM_NOTE_CHARS],
            "status_at_end_of_day": "resolved" if resolved else "active",
            "resolved_at": r.resolved_at.isoformat() if resolved and r.resolved_at else None,
            "is_simulated": r.is_simulated}


class JournalService:
    def __init__(self, session: Session):
        self.s = session
        self.memory = MemoryService(session)

    def day_window(self, astronaut_id: int, day: int) -> tuple[date, int | None, datetime, datetime]:
        """Validate a mission day. Returns (mission start, current mission day, day start, day end)."""
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        start = mission_start(self.s, astronaut_id)
        if start is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        today = current_mission_day(self.s, astronaut_id)
        if day < 1 or (today is not None and day > today):
            raise ValueError(f"mission day must be between 1 and {today}")
        d0, d1 = day_bounds(start, day)
        return start, today, d0, d1

    def generate(self, astronaut_id: int, day: int, series: dict[str, DailySeries] | None = None) -> JournalEntry:
        start, today, d0, d1 = self.day_window(astronaut_id, day)
        a = self.s.get(Astronaut, astronaut_id)
        # Series up to the current day, as generate_range builds it, so a day's entry is identical
        # whichever way it is generated. Values, trends and runs only read days <= `day`; the
        # later days only decide whether the days 1-7 reference exists ("reference period" label).
        series = series if series is not None else build_daily_series(self.s, astronaut_id, until_day=today)
        refs: list[dict[str, Any]] = []

        def item(key: str) -> dict[str, Any] | None:
            s = series.get(key)
            if s is None or day not in s.values:
                return None
            v = s.values[day]
            out: dict[str, Any] = {"key": key, "label": s.label, "value": round(v, 2), "unit": s.unit,
                                   "aggregation": s.aggregation, "n": s.counts.get(day, 0)}
            ref = reference_for(s)
            if ref is not None and day > REFERENCE_DAYS:
                out.update({"reference_median": round(ref.median, 2), "reference_days": list(ref.days),
                            "difference": round(v - ref.median, 2),
                            "percent": round((v - ref.median) / ref.median * 100, 1) if ref.median else None,
                            "status": ref.label(v)})
            elif ref is None:
                out["status"] = "no reference yet"
            else:
                out["status"] = "reference period"
            refs.append({"type": "daily_series", "key": key, "day": day})
            return out

        physical = [x for x in (item(k) for k in PHYSICAL) if x]
        mental = [x for x in (item(k) for k in MENTAL) if x]

        tasks = self.s.scalars(select(DailyTask).where(DailyTask.astronaut_id == astronaut_id,
                                                       DailyTask.mission_day == day)).all()
        task_section = None
        if tasks:
            done = sum(t.status == "done" for t in tasks)
            task_section = {"done": done, "total": len(tasks), "percent": round(done / len(tasks) * 100),
                            "open": [t.title for t in tasks if t.status != "done"]}
            refs += [{"type": "daily_task", "id": t.id} for t in tasks]

        alerts_open = self.s.scalars(select(Alert).where(
            Alert.astronaut_id == astronaut_id, Alert.first_seen_at < d1,
            (Alert.resolved_at.is_(None)) | (Alert.resolved_at >= d1))).all()
        alerts_new = [x for x in alerts_open if d0 <= x.first_seen_at < d1]
        refs += [{"type": "alert", "id": x.id} for x in alerts_open]
        # peak AS OF the end of this day, from the evidence recorded up to then (no hindsight)
        peak_as_of: dict[int, Severity] = {}
        for x in alerts_open:
            levels = [Severity(e.severity_at_time) for e in x.evidence if e.measurement.recorded_at < d1]
            if levels:
                peak_as_of[x.id] = max(levels, key=lambda s: s.rank)

        observations: list[dict[str, Any]] = []
        flags: list[dict[str, Any]] = []
        for key in PHYSICAL + MENTAL:
            s = series.get(key)
            if s is None or day not in s.values:
                continue
            for label in ("below reference", "above reference"):
                n, first = _run_length(s, day, label)
                if n >= FLAG_RUN_DAYS:
                    text = f"{s.label} {label} for {n} consecutive days (days {first}-{day})."
                    observations.append({"type": "persistent_deviation", "key": key, "text": text,
                                         "days": [first, day]})
                    if n >= PATTERN_DAYS:
                        flags.append({"severity": "review", "text": f"Persistent change: {text} Requires further "
                                                                    "assessment by the medical team.", "key": key})
                    if n == PATTERN_DAYS:
                        self._remember_pattern(astronaut_id, key, text, first, day, a.is_simulated)
            tr = _trend_7d(s, day)
            if tr:
                word = "increasing" if tr["slope_per_day"] > 0 else "decreasing"
                observations.append({"type": "trend_7d", "key": key, "days": tr["days"],
                                     "text": f"{s.label} {word} over days {tr['days'][0]}-{tr['days'][1]} "
                                             f"(Theil-Sen {tr['slope_per_day']:+g} {s.unit}/day, Spearman p={tr['p']})."})

        sq = series.get("selfreport:sleep_quality")
        if sq:
            poor = [d for d in range(day - 2, day + 1) if sq.values.get(d, 99) <= 2]
            if len(poor) >= 2:
                observations.append({"type": "repeated_report", "key": sq.key, "days": poor,
                                     "text": f"Poor sleep quality self-reported on {len(poor)} of the last 3 days."})

        for x in alerts_open:
            peak = peak_as_of.get(x.id)
            if peak is not None and peak.rank >= Severity.WARNING.rank:
                flags.append({"severity": peak.value, "alert_id": x.id,
                              "text": f"{peak.value} alert open: {x.metric_code.replace('_', ' ')} "
                                      f"({x.context}), {x.direction} deviation."})
        freezes = self.s.scalars(select(BaselineAudit).where(
            BaselineAudit.astronaut_id == astronaut_id, BaselineAudit.action == "frozen")).all()
        # attribute a freeze to the day of the DATA that triggered it (audit created_at is wall-clock)
        for f in freezes:
            dt = (f.details or {}).get("data_time")
            freeze_day = (datetime.fromisoformat(dt).date() - start).days + 1 if dt else None
            if freeze_day is not None and freeze_day <= day:
                flags.append({"severity": "info", "text": f"Baseline for {f.metric_code.replace('_', ' ')} "
                                                          f"({f.context}) is frozen pending review: {f.reason}",
                              "baseline_audit_id": f.id})
                break

        reported, ongoing = self._symptoms(astronaut_id, d0, d1)
        reported_facts = [_symptom_fact(r, start) for r in reported]
        ongoing_facts = [_symptom_fact(r, start) for r in ongoing]
        refs += [{"type": "symptom_report", "id": r.id} for r in (*reported, *ongoing)]
        ratings = [RatingFact(r.id, r.kind, r.score, r.recorded_at) for r in self.s.scalars(select(SelfReport).where(
            SelfReport.astronaut_id == astronaut_id, SelfReport.kind != "note",
            SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1).order_by(SelfReport.recorded_at, SelfReport.id))]

        sections = {
            "physical": physical,
            "mental": mental,
            "tasks": task_section,
            "alerts": {"new": [{"id": x.id, "metric": x.metric_code, "context": x.context,
                                "severity": peak_as_of[x.id].value if x.id in peak_as_of else None}
                               for x in alerts_new],
                       "open_at_end_of_day": len(alerts_open),
                       "open": [{"id": x.id, "metric": x.metric_code, "context": x.context,
                                 "severity": peak_as_of[x.id].value if x.id in peak_as_of else None}
                                for x in alerts_open]},
            "notes": [r.note for r in self.s.scalars(select(SelfReport).where(
                SelfReport.astronaut_id == astronaut_id, SelfReport.kind == "note",
                SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1))],
            "symptoms": {"reported": [_symptom_record(f, r, d1) for f, r in zip(reported_facts, reported)],
                         "ongoing": [_symptom_record(f, r, d1) for f, r in zip(ongoing_facts, ongoing)]},
        }
        facts = DayFacts(
            day_start=d0, day_end=d1,
            has_records=bool(physical or mental or reported) or self._has_records(astronaut_id, d0, d1),
            symptoms_reported=reported_facts, symptoms_ongoing=ongoing_facts,
            alerts=[AlertFact(x.id, x.metric_code, x.context, x.direction, peak_as_of[x.id].value)
                    for x in alerts_open if x.id in peak_as_of],
            flags=flags, ratings=ratings)
        assessment = assess_day(facts, load_day_level_rules())

        entry = self.s.scalars(select(JournalEntry).where(JournalEntry.astronaut_id == astronaut_id,
                                                          JournalEntry.mission_day == day)).first()
        if entry is None:
            entry = JournalEntry(astronaut_id=astronaut_id, mission_day=day, day_date=d0.date())
            self.s.add(entry)
        entry.sections = sections
        entry.flags = flags
        entry.observations = observations
        entry.refs = refs
        entry.complete_day = today is not None and day < today
        entry.is_simulated = a.is_simulated
        entry.generator_version = GENERATOR_VERSION
        entry.day_level = assessment.level
        entry.day_level_reasons = assessment.reasons
        refresh_outdated(self.s, entry)
        from am.db.base import utcnow

        entry.generated_at = utcnow()
        self.s.flush()
        return entry

    def generate_range(self, astronaut_id: int, first: int = 1, last: int | None = None) -> list[JournalEntry]:
        today = current_mission_day(self.s, astronaut_id)
        if today is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        last = today if last is None else last
        if first < 1 or last < first or last > today:
            raise ValueError(f"day range must satisfy 1 <= first_day <= last_day <= {today}")
        series = build_daily_series(self.s, astronaut_id, until_day=today)  # same series as generate()
        return [self.generate(astronaut_id, d, series) for d in range(first, last + 1)]

    def _symptoms(self, astronaut_id: int, d0: datetime, d1: datetime) -> tuple[list[SymptomReport],
                                                                                  list[SymptomReport]]:
        """Reports made that day, and earlier reports not yet resolved when the day began."""
        rows = self.s.scalars(select(SymptomReport).where(
            SymptomReport.astronaut_id == astronaut_id, SymptomReport.reported_at < d1)
            .order_by(SymptomReport.reported_at, SymptomReport.id)).all()
        reported = [r for r in rows if r.reported_at >= d0]
        ongoing = [r for r in rows if r.reported_at < d0 and not _resolved_before(r, d0)]
        return reported, ongoing

    def _has_records(self, astronaut_id: int, d0: datetime, d1: datetime) -> bool:
        """Any non-rejected measurement, check-in, symptom report or chat message from the astronaut that day.

        Rejected readings are sensor or entry errors, so they do not make a day 'green'.
        """
        probes = (
            select(Measurement.id).where(Measurement.astronaut_id == astronaut_id,
                                         Measurement.quality != DataQuality.REJECTED.value,
                                         Measurement.recorded_at >= d0, Measurement.recorded_at < d1),
            select(SelfReport.id).where(SelfReport.astronaut_id == astronaut_id,
                                        SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1),
            select(SymptomReport.id).where(SymptomReport.astronaut_id == astronaut_id,
                                           SymptomReport.reported_at >= d0, SymptomReport.reported_at < d1),
            select(Message.id).join(Conversation, Message.conversation_id == Conversation.id).where(
                Conversation.astronaut_id == astronaut_id, Message.role == "user",
                Message.created_at >= d0, Message.created_at < d1),
        )
        return any(self.s.scalars(p.limit(1)).first() is not None for p in probes)

    def _remember_pattern(self, astronaut_id: int, key: str, text: str, first: int, day: int, sim: bool) -> None:
        exists = self.s.scalars(select(MemoryItem).where(
            MemoryItem.astronaut_id == astronaut_id, MemoryItem.layer == "long", MemoryItem.kind == "pattern",
            MemoryItem.content == f"Pattern: {text}")).first()
        if exists:
            return
        self.memory.add(astronaut_id, layer="long", kind="pattern", content=f"Pattern: {text}", source="journal",
                        importance=2, refs=[{"type": "daily_series", "key": key, "days": [first, day]}],
                        is_simulated=sim)
