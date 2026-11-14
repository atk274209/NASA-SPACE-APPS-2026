"""Measurement ingestion pipeline: convert -> validate -> store -> assess -> alert -> baseline.

The stored `value`/`unit` are always canonical. When the entered unit had to be converted,
the value and unit as entered are stored too (`original_value`/`original_unit`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.alerts.service import AlertService
from am.anomaly.engine import AnomalyEngine, Assessment
from am.audit import audit
from am.baseline.service import BaselineEvent, BaselineService
from am.db.base import to_naive_utc, utcnow
from am.db.enums import AssessmentStatus, DataQuality, SourceKind
from am.db.models import Astronaut, DataSource, Measurement
from am.health.metrics import get_spec
from am.health.validation import validate_measurement
from am.policy import BaselinePolicy, ThresholdConfig


@dataclass
class MeasurementInput:
    astronaut_id: int
    metric_code: str
    value: float
    unit: str
    context: str
    recorded_at: datetime
    source_name: str
    source_kind: SourceKind
    confidence: float | None = None
    signal_quality: str | None = None


@dataclass
class IngestResult:
    measurement: Measurement
    assessment: Assessment
    alert_id: int | None
    baseline_event: BaselineEvent | None
    notes: list[str] = field(default_factory=list)


class IngestService:
    def __init__(self, session: Session, thresholds: ThresholdConfig, policy: BaselinePolicy):
        self.s = session
        self.baselines = BaselineService(session, policy, thresholds)
        self.anomaly = AnomalyEngine(session, thresholds)
        self.alerts = AlertService(session, thresholds)
        self.memory = MemoryService(session)
        self._sources: dict[str, DataSource] = {}

    def _source(self, name: str, kind: SourceKind) -> DataSource:
        key = f"{kind.value}:{name}"
        if key in self._sources:
            return self._sources[key]
        src = self.s.scalars(select(DataSource).where(DataSource.name == name)).first()
        if src is None:
            src = DataSource(name=name, kind=kind.value)
            self.s.add(src)
            self.s.flush()
        elif src.kind != kind.value:
            raise ValueError(f"data source '{name}' already registered with kind '{src.kind}'")
        self._sources[key] = src
        return src

    def ingest(self, inp: MeasurementInput, *, now: datetime | None = None) -> IngestResult:
        astronaut = self.s.get(Astronaut, inp.astronaut_id)
        if astronaut is None:
            raise LookupError("astronaut not found")
        spec = get_spec(inp.metric_code)
        if spec is None:
            raise ValueError(f"unknown metric '{inp.metric_code}'")
        if inp.value is None or not math.isfinite(inp.value):
            # Refused outright rather than stored as rejected: the value column cannot hold
            # NaN (SQLite turns it into NULL), and there is no number worth keeping.
            raise ValueError("value must be a finite number")
        if inp.confidence is not None and not math.isfinite(inp.confidence):
            raise ValueError("confidence must be a finite number")
        now = now or utcnow()
        recorded_at = to_naive_utc(inp.recorded_at)
        vr = validate_measurement(
            spec,
            value=inp.value,
            unit=inp.unit,
            context=inp.context,
            recorded_at=recorded_at,
            now=now,
            confidence=inp.confidence,
            signal_quality=inp.signal_quality,
        )
        src = self._source(inp.source_name, inp.source_kind)
        m = Measurement(
            astronaut_id=inp.astronaut_id,
            metric_code=spec.code,
            value=float(vr.value if vr.value is not None else inp.value),
            unit=vr.unit or inp.unit,
            original_value=vr.original_value,
            original_unit=vr.original_unit,
            context=inp.context,
            recorded_at=recorded_at,
            source_id=src.id,
            is_simulated=inp.source_kind == SourceKind.SIMULATED,
            quality=vr.quality.value,
            confidence=inp.confidence,
            validation_notes=vr.notes,
        )
        self.s.add(m)
        self.s.flush()

        if not vr.accepted:
            audit(self.s, actor="am-ingest", action="measurement.rejected", entity="measurement",
                  entity_id=m.id, payload={"metric": spec.code, "value": inp.value, "unit": inp.unit,
                                           "notes": vr.notes})
            m.assessment_status = AssessmentStatus.SKIPPED_REJECTED.value
            return IngestResult(m, Assessment(AssessmentStatus.SKIPPED_REJECTED, reasons=vr.notes), None, None,
                                vr.notes)

        profile = self.baselines.active_profile(m.astronaut_id, m.metric_code, m.context)
        assessment = self.anomaly.assess(m, profile)
        m.assessment_status = assessment.status.value
        m.severity = assessment.severity.value if assessment.severity else None
        m.deviation_score = assessment.z
        m.baseline_profile_id = assessment.baseline_profile_id
        self.s.flush()

        alert = self.alerts.process(m, assessment)
        if alert is not None:
            self.s.flush()

        event = None
        if spec.baseline_enabled and m.quality == DataQuality.GOOD.value:
            event = self.baselines.maybe_update(m.astronaut_id, m.metric_code, m.context)
        self._record_memory(m, event)
        return IngestResult(m, assessment, alert.id if alert else None, event, vr.notes)

    def _record_memory(self, m: Measurement, event: BaselineEvent | None) -> None:
        """Deterministic mission-memory entries for significant events (never LLM-written)."""
        for kind, alert, mid in self.alerts.events:
            self.memory.record_alert_event(alert, kind, mid)
        self.alerts.events.clear()
        if event is not None and event.action == "frozen" and event.profile_id is not None:
            from am.db.models import BaselineProfile

            prof = self.s.get(BaselineProfile, event.profile_id)
            if prof is not None:
                self.memory.record_baseline_freeze(prof, event.reason, m.is_simulated,
                                                   self.baselines.last_freeze_data_time or m.recorded_at)
