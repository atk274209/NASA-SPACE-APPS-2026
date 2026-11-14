"""Daily series built from stored records (shared by correlation, journal and missions).

Physiological streams are aggregated as the daily MEDIAN of non-rejected readings.
Daily-total metrics (context 'daily') use the median of that day's entries (normally one).
Self-reports use the daily mean score. Day 1 = mission start date (UTC).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.enums import DataQuality
from am.db.models import Measurement, MetricDefinition, Mission
from am.db.models_ai import SelfReport


@dataclass
class DailySeries:
    key: str  # "heart_rate:rest" | "selfreport:stress"
    label: str
    unit: str
    kind: str  # measurement | self_report
    aggregation: str
    values: dict[int, float] = field(default_factory=dict)  # mission_day -> value
    counts: dict[int, int] = field(default_factory=dict)

    def days(self) -> list[int]:
        return sorted(self.values)


def mission_start(session: Session, astronaut_id: int) -> date | None:
    m = session.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                        .order_by(Mission.id.desc()).limit(1)).first()
    return m.start_date if m else None


def current_mission_day(session: Session, astronaut_id: int) -> int | None:
    start = mission_start(session, astronaut_id)
    return (utcnow().date() - start).days + 1 if start else None


def day_of(start: date, ts: datetime) -> int:
    return (ts.date() - start).days + 1


def day_bounds(start: date, day: int) -> tuple[datetime, datetime]:
    s = datetime.combine(start, datetime.min.time()) + timedelta(days=day - 1)
    return s, s + timedelta(days=1)


def build_daily_series(session: Session, astronaut_id: int, until_day: int | None = None) -> dict[str, DailySeries]:
    start = mission_start(session, astronaut_id)
    if start is None:
        return {}
    metrics = {m.code: m for m in session.scalars(select(MetricDefinition))}
    buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    meta: dict[str, tuple[str, str]] = {}
    rows = session.execute(
        select(Measurement.metric_code, Measurement.context, Measurement.value, Measurement.unit,
               Measurement.recorded_at)
        .where(Measurement.astronaut_id == astronaut_id, Measurement.quality != DataQuality.REJECTED.value)
    )
    for code, ctx, value, unit, ts in rows:
        d = day_of(start, ts)
        if d < 1 or (until_day is not None and d > until_day):
            continue
        key = f"{code}:{ctx}"
        buckets[key][d].append(value)
        if key not in meta:
            name = metrics[code].display_name if code in metrics else code
            meta[key] = (f"{name} ({'daily total' if ctx == 'daily' else ctx})", unit)

    out: dict[str, DailySeries] = {}
    for key, days in buckets.items():
        label, unit = meta[key]
        s = DailySeries(key, label, unit, "measurement", "daily median")
        for d, vals in days.items():
            s.values[d] = float(median(vals))
            s.counts[d] = len(vals)
        out[key] = s

    sr: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for kind, score, ts in session.execute(
        select(SelfReport.kind, SelfReport.score, SelfReport.recorded_at)
        .where(SelfReport.astronaut_id == astronaut_id, SelfReport.kind != "note")
    ):
        d = day_of(start, ts)
        if d >= 1 and (until_day is None or d <= until_day):
            sr[kind][d].append(score)
    for kind, days in sr.items():
        s = DailySeries(f"selfreport:{kind}", f"Self-reported {kind.replace('_', ' ')} (1-5)", "score",
                        "self_report", "daily mean")
        for d, vals in days.items():
            s.values[d] = float(mean(vals))
            s.counts[d] = len(vals)
        out[s.key] = s
    return out
