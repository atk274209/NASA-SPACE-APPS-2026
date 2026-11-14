"""Deterministic anomaly classification against the personal baseline.

Pipeline per measurement (no LLM involved):
  1. skip rejected data
  2. select the baseline for the SAME context (rest vs exercise vs sleep...). If there is
     none, the measurement is NOT compared to another context -> NO_BASELINE.
  3. robust z = (x - median) / scale
  4. instant level from configured bands (respecting rule direction)
  5. persistence: same-direction deviations among recent assessed readings; a level
     needing persistence is capped one step lower until persistence is met (floor:
     a single reading >= WARNING band is still surfaced as WATCH)
  6. degraded-quality data is capped (default WATCH)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.baseline.stats import profile_scale, robust_z
from am.db.enums import AssessmentStatus, DataQuality, Severity
from am.db.models import BaselineProfile, Measurement
from am.policy import MetricRule, ThresholdConfig


@dataclass
class Assessment:
    status: AssessmentStatus
    severity: Severity | None = None
    z: float | None = None
    direction: str | None = None  # high | low
    baseline_profile_id: int | None = None
    persistence_count: int = 0
    instant_severity: Severity | None = None
    rule_id: str | None = None
    reasons: list[str] = field(default_factory=list)


def _instant_level(z: float, rule: MetricRule) -> Severity:
    if rule.direction == "high" and z < 0:
        return Severity.NORMAL
    if rule.direction == "low" and z > 0:
        return Severity.NORMAL
    mag = abs(z)
    for sev in (Severity.CRITICAL, Severity.WARNING, Severity.WATCH):
        if mag >= rule.bands[sev]:
            return sev
    return Severity.NORMAL


class AnomalyEngine:
    def __init__(self, session: Session, thresholds: ThresholdConfig):
        self.s = session
        self.thresholds = thresholds

    def assess(self, m: Measurement, profile: BaselineProfile | None) -> Assessment:
        if m.quality == DataQuality.REJECTED.value:
            return Assessment(AssessmentStatus.SKIPPED_REJECTED, reasons=["measurement rejected by validation"])
        rule = self.thresholds.rule_for(m.metric_code)
        if rule is None:
            return Assessment(AssessmentStatus.NO_BASELINE, reasons=["no anomaly rule configured for metric"])
        if profile is None:
            return Assessment(
                AssessmentStatus.NO_BASELINE,
                rule_id=rule.rule_id,
                reasons=[f"no personal baseline yet for context '{m.context}'; not compared to other contexts"],
            )

        provisional = profile.status == "provisional"
        scale = profile_scale(profile.median, profile.mad, profile.sd, rule.min_scale_fraction, provisional,
                              profile.p05, profile.p95, profile.n)
        z = robust_z(m.value, profile.median, scale)
        direction = "high" if z >= 0 else "low"
        instant = _instant_level(z, rule)
        reasons = [
            f"value {m.value:g} {m.unit} vs personal median {profile.median:.1f} "
            f"(baseline v{profile.version}, {profile.status}, context '{profile.context}'); robust z={z:+.2f}"
        ]

        persistence = 1 if instant.rank >= Severity.WATCH.rank else 0
        if persistence:
            persistence += self._recent_same_direction(m, rule, direction)

        # Persistence gating. Floor: an instant >= WARNING is never reported below WATCH.
        floor = Severity.WATCH if instant.rank >= Severity.WARNING.rank else Severity.NORMAL
        level = instant
        while level.rank > floor.rank and persistence < rule.persistence.required.get(level, 1):
            reasons.append(
                f"{level.value} requires {rule.persistence.required[level]} persistent deviations "
                f"(have {persistence}); capped"
            )
            level = Severity.from_rank(level.rank - 1)

        if m.quality == DataQuality.DEGRADED.value and level.rank > rule.degraded_quality_cap.rank:
            reasons.append(f"degraded data quality; capped at {rule.degraded_quality_cap.value}")
            level = rule.degraded_quality_cap

        if provisional:
            reasons.append("baseline is PROVISIONAL: small-sample predictive scale used; interpret with caution")

        return Assessment(
            AssessmentStatus.ASSESSED,
            severity=level,
            z=z,
            direction=direction,
            baseline_profile_id=profile.id,
            persistence_count=persistence,
            instant_severity=instant,
            rule_id=rule.rule_id,
            reasons=reasons,
        )

    def _recent_same_direction(self, m: Measurement, rule: MetricRule, direction: str) -> int:
        p = rule.persistence
        since = m.recorded_at - timedelta(hours=p.max_age_hours)
        stmt = (
            select(Measurement.deviation_score)
            .where(
                Measurement.astronaut_id == m.astronaut_id,
                Measurement.metric_code == m.metric_code,
                Measurement.context == m.context,
                Measurement.assessment_status == AssessmentStatus.ASSESSED.value,
                Measurement.recorded_at >= since,
                Measurement.recorded_at <= m.recorded_at,
            )
            .order_by(Measurement.recorded_at.desc())
            .limit(max(p.lookback - 1, 0))
        )
        if m.id is not None:
            stmt = stmt.where(Measurement.id != m.id)
        watch = rule.bands[Severity.WATCH]
        count = 0
        for (dev,) in self.s.execute(stmt):
            if dev is None:
                continue
            if direction == "high" and dev >= watch:
                count += 1
            elif direction == "low" and dev <= -watch:
                count += 1
        return count
