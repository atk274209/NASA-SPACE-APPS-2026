"""Mission journal: deterministic structured day summaries from stored data.

Every line refers to stored records (daily series keys + days, alerts, tasks, self-reports).
Patterns that persist (>= PATTERN_DAYS consecutive days outside the mission-start
reference) are written to LONG-TERM memory, deterministically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from scipy.stats import spearmanr, theilslopes
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.db.enums import Severity
from am.db.models import Alert, Astronaut, BaselineAudit
from am.db.models_ai import MemoryItem, SelfReport
from am.db.models_research import DailyTask, JournalEntry
from am.research.reference import REFERENCE_DAYS, reference_for
from am.research.series import DailySeries, build_daily_series, current_mission_day, day_bounds, mission_start

GENERATOR_VERSION = "journal-1"
PATTERN_DAYS = 5
FLAG_RUN_DAYS = 3

PHYSICAL = ["exercise_duration:daily", "step_count:daily", "sleep_duration:daily", "heart_rate:rest",
            "heart_rate:sleep", "hrv_rmssd:rest", "spo2:rest", "body_temperature:rest", "water_intake:daily",
            "energy_intake:daily"]
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


class JournalService:
    def __init__(self, session: Session):
        self.s = session
        self.memory = MemoryService(session)

    def generate(self, astronaut_id: int, day: int, series: dict[str, DailySeries] | None = None) -> JournalEntry:
        a = self.s.get(Astronaut, astronaut_id)
        if a is None:
            raise LookupError("astronaut not found")
        start = mission_start(self.s, astronaut_id)
        if start is None:
            raise ValueError("astronaut has no mission; mission days are undefined")
        today = current_mission_day(self.s, astronaut_id)
        if day < 1 or (today is not None and day > today):
            raise ValueError(f"mission day must be between 1 and {today}")
        series = series if series is not None else build_daily_series(self.s, astronaut_id, until_day=day)
        d0, d1 = day_bounds(start, day)
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
            # peak AS OF the end of this day, from the evidence recorded up to then (no hindsight)
            levels = [Severity(e.severity_at_time) for e in x.evidence if e.measurement.recorded_at < d1]
            if not levels:
                continue
            peak = max(levels, key=lambda s: s.rank)
            if peak.rank >= Severity.WARNING.rank:
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

        sections = {
            "physical": physical,
            "mental": mental,
            "tasks": task_section,
            "alerts": {"new": [{"id": x.id, "metric": x.metric_code, "context": x.context,
                                "severity": x.peak_severity} for x in alerts_new],
                       "open_at_end_of_day": len(alerts_open)},
            "notes": [r.note for r in self.s.scalars(select(SelfReport).where(
                SelfReport.astronaut_id == astronaut_id, SelfReport.kind == "note",
                SelfReport.recorded_at >= d0, SelfReport.recorded_at < d1))],
        }
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
        series = build_daily_series(self.s, astronaut_id, until_day=last)
        return [self.generate(astronaut_id, d, series) for d in range(first, last + 1)]

    def _remember_pattern(self, astronaut_id: int, key: str, text: str, first: int, day: int, sim: bool) -> None:
        exists = self.s.scalars(select(MemoryItem).where(
            MemoryItem.astronaut_id == astronaut_id, MemoryItem.layer == "long", MemoryItem.kind == "pattern",
            MemoryItem.content == f"Pattern: {text}")).first()
        if exists:
            return
        self.memory.add(astronaut_id, layer="long", kind="pattern", content=f"Pattern: {text}", source="journal",
                        importance=2, refs=[{"type": "daily_series", "key": key, "days": [first, day]}],
                        is_simulated=sim)
