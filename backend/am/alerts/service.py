"""Alert lifecycle.

Alerts are opened/escalated only from deterministic assessments. They are never
auto-resolved: a human must acknowledge and resolve them (audited). If severity rises
above the level at which an alert was acknowledged, it returns to ACTIVE.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.anomaly.engine import Assessment
from am.audit import audit
from am.db.base import utcnow
from am.db.enums import AlertStatus, AssessmentStatus, Severity
from am.db.models import Alert, AlertAck, AlertEvidence, Measurement
from am.policy import ThresholdConfig

SYSTEM_ACTOR = "am-system"


class AlertService:
    def __init__(self, session: Session, thresholds: ThresholdConfig):
        self.s = session
        self.thresholds = thresholds
        # (event, alert, measurement_id) for 'opened' / 'escalated'; consumed by the memory recorder
        self.events: list[tuple[str, Alert, int | None]] = []

    def open_alert(self, astronaut_id: int, metric: str, context: str, direction: str) -> Alert | None:
        return self.s.scalars(
            select(Alert)
            .where(
                Alert.astronaut_id == astronaut_id,
                Alert.metric_code == metric,
                Alert.context == context,
                Alert.direction == direction,
                Alert.status != AlertStatus.RESOLVED.value,
            )
            .order_by(Alert.id.desc())
            .limit(1)
        ).first()

    def process(self, m: Measurement, a: Assessment) -> Alert | None:
        if a.status != AssessmentStatus.ASSESSED or a.severity is None or a.severity == Severity.NORMAL:
            return None
        assert a.z is not None and a.direction is not None
        explanation = "; ".join(a.reasons)
        alert = self.open_alert(m.astronaut_id, m.metric_code, m.context, a.direction)
        if alert is None:
            alert = Alert(
                astronaut_id=m.astronaut_id,
                metric_code=m.metric_code,
                context=m.context,
                severity=a.severity.value,
                peak_severity=a.severity.value,
                status=AlertStatus.ACTIVE.value,
                direction=a.direction,
                rule_id=a.rule_id or "unknown",
                threshold_version=self.thresholds.version,
                threshold_label=self.thresholds.label,
                baseline_profile_id=a.baseline_profile_id,
                last_deviation=a.z,
                peak_deviation=a.z,
                persistence_count=a.persistence_count,
                explanation=explanation,
                is_simulated=m.is_simulated,
                first_seen_at=m.recorded_at,
                last_seen_at=m.recorded_at,
            )
            self.s.add(alert)
            self.s.flush()
            self.events.append(("opened", alert, m.id))
            audit(self.s, actor=SYSTEM_ACTOR, action="alert.opened", entity="alert", entity_id=alert.id,
                  payload={"severity": alert.severity, "measurement_id": m.id, "z": a.z})
        else:
            prev_peak = Severity(alert.peak_severity)
            alert.severity = a.severity.value
            alert.last_deviation = a.z
            alert.persistence_count = a.persistence_count
            alert.last_seen_at = max(alert.last_seen_at, m.recorded_at)
            alert.explanation = explanation
            if abs(a.z) > abs(alert.peak_deviation):
                alert.peak_deviation = a.z
            if a.severity.rank > prev_peak.rank:
                alert.peak_severity = a.severity.value
                if alert.status == AlertStatus.ACKNOWLEDGED.value:
                    alert.status = AlertStatus.ACTIVE.value
                self.events.append(("escalated", alert, m.id))
                audit(self.s, actor=SYSTEM_ACTOR, action="alert.escalated", entity="alert", entity_id=alert.id,
                      payload={"from": prev_peak.value, "to": a.severity.value, "measurement_id": m.id})
        self.s.add(
            AlertEvidence(alert_id=alert.id, measurement_id=m.id, severity_at_time=a.severity.value, deviation=a.z)
        )
        return alert

    def acknowledge(self, alert_id: int, actor: str, note: str = "") -> Alert:
        alert = self._get(alert_id)
        if alert.status == AlertStatus.RESOLVED.value:
            raise ValueError("alert already resolved")
        alert.status = AlertStatus.ACKNOWLEDGED.value
        self.s.add(AlertAck(alert_id=alert.id, action="acknowledge", actor=actor, note=note))
        audit(self.s, actor=actor, action="alert.acknowledged", entity="alert", entity_id=alert.id,
              payload={"note": note})
        return alert

    def resolve(self, alert_id: int, actor: str, note: str) -> Alert:
        alert = self._get(alert_id)
        if alert.status == AlertStatus.RESOLVED.value:
            raise ValueError("alert already resolved")
        if not note.strip():
            raise ValueError("resolution note is required")
        alert.status = AlertStatus.RESOLVED.value
        alert.resolved_at = utcnow()
        self.s.add(AlertAck(alert_id=alert.id, action="resolve", actor=actor, note=note))
        audit(self.s, actor=actor, action="alert.resolved", entity="alert", entity_id=alert.id,
              payload={"note": note})
        return alert

    def _get(self, alert_id: int) -> Alert:
        alert = self.s.get(Alert, alert_id)
        if alert is None:
            raise LookupError("alert not found")
        return alert
