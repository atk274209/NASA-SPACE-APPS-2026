"""SIMULATED wearable readings (smart shirt) for a recent time window.

Values are arbitrary demonstration draws, not physiological norms. Used by the Test Console
(/api/test/wearable) and by the demo space events (am.demo.events).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.enums import SourceKind
from am.endday.pipeline import day_lock_through
from am.health.ingest import IngestService, MeasurementInput

WEARABLE_SOURCE = "simulated wearable (smart shirt)"
WEARABLE_PRESETS = ("nominal", "elevated_heart_rate", "fever")


class NoWindow(Exception):
    """The mission day has just started; there is no time window to fill yet."""


@dataclass
class WearableResult:
    readings: int = 0
    alert_ids: set[int] = field(default_factory=set)
    start: datetime | None = None
    end: datetime | None = None


def simulate_wearable(db: Session, thresholds, policy, astronaut_id: int, preset: str, hours: float) -> WearableResult:
    import numpy as np

    if preset not in WEARABLE_PRESETS:
        raise ValueError(f"preset must be one of {list(WEARABLE_PRESETS)}")
    end = utcnow()
    start = end - timedelta(hours=hours)
    lock = day_lock_through(db)
    if lock is not None and start < lock:
        start = lock
    if start >= end:
        raise NoWindow("The mission day has just started; there is no time window to fill yet.")
    rng = np.random.default_rng(int(end.timestamp()) % 100000 + astronaut_id)
    hr_shift = 25.0 if preset == "elevated_heart_rate" else 12.0 if preset == "fever" else 0.0
    temp_shift = 1.2 if preset == "fever" else 0.0
    svc = IngestService(db, thresholds, policy)
    out = WearableResult(start=start, end=end)
    t = start + timedelta(minutes=10)
    while t <= end:
        for metric, unit, mean, sd, dec in (("heart_rate", "bpm", 68 + hr_shift, 2.5, 0),
                                            ("spo2", "%", 97.5, 0.5, 1),
                                            ("body_temperature", "degC", 36.7 + temp_shift, 0.1, 2)):
            if metric != "heart_rate" and t.minute >= 20:  # SpO2 and temperature a few times per hour
                continue
            value = round(float(rng.normal(mean, sd)), dec) if dec else float(round(rng.normal(mean, sd)))
            res = svc.ingest(MeasurementInput(astronaut_id=astronaut_id, metric_code=metric, value=value,
                                              unit=unit, context="rest", recorded_at=t, source_name=WEARABLE_SOURCE,
                                              source_kind=SourceKind.SIMULATED, confidence=0.9))
            out.readings += 1
            if res.alert_id:
                out.alert_ids.add(res.alert_id)
        t += timedelta(minutes=10)
    return out
