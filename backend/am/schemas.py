"""Pydantic API schemas (request/response contracts)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from am.db.enums import SourceKind


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- astronauts
class AstronautCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=128)


class AstronautOut(ORM):
    id: int
    code: str
    display_name: str
    is_simulated: bool
    created_at: datetime


class MissionOut(ORM):
    id: int
    name: str
    astronaut_id: int
    start_date: date
    planned_days: int | None
    environment: str = "unspecified"
    is_simulated: bool


# ---------------------------------------------------------------- metrics
class MetricOut(ORM):
    code: str
    category: str
    display_name: str
    unit: str
    allowed_contexts: list[str]
    plausible_min: float | None
    plausible_max: float | None
    plausibility_note: str
    baseline_enabled: bool


# ---------------------------------------------------------------- measurements
class MeasurementIn(BaseModel):
    astronaut_id: int
    metric_code: str
    value: float
    unit: str
    context: str
    recorded_at: datetime
    source_name: str = Field(min_length=1, max_length=128)
    source_kind: SourceKind = SourceKind.MANUAL
    confidence: float | None = None
    signal_quality: str | None = None


class MeasurementOut(ORM):
    id: int
    astronaut_id: int
    metric_code: str
    value: float
    unit: str
    context: str
    recorded_at: datetime
    ingested_at: datetime
    source_id: int
    is_simulated: bool
    quality: str
    confidence: float | None
    validation_notes: list[str]
    assessment_status: str | None
    severity: str | None
    deviation_score: float | None
    baseline_profile_id: int | None
    original_value: float | None = None  # as entered, before unit conversion (None if not converted)
    original_unit: str | None = None


class AssessmentOut(BaseModel):
    status: str
    severity: str | None
    instant_severity: str | None
    z: float | None
    direction: str | None
    persistence_count: int
    baseline_profile_id: int | None
    rule_id: str | None
    reasons: list[str]


class BaselineEventOut(BaseModel):
    action: str
    profile_id: int | None
    reason: str


class IngestOut(BaseModel):
    measurement: MeasurementOut
    assessment: AssessmentOut
    alert_id: int | None
    baseline_event: BaselineEventOut | None


class BatchIn(BaseModel):
    items: list[MeasurementIn] = Field(min_length=1, max_length=5000)


class BatchOut(BaseModel):
    accepted: int
    rejected: int
    alerts_touched: list[int]
    results: list[IngestOut]


# ---------------------------------------------------------------- baselines
class BaselineOut(ORM):
    id: int
    astronaut_id: int
    metric_code: str
    context: str
    version: int
    status: str
    is_frozen: bool
    frozen_reason: str | None
    is_anchor: bool
    n: int
    mean: float
    median: float
    sd: float
    mad: float
    p05: float
    p95: float
    min: float
    max: float
    trend_per_day: float | None
    window_start: datetime
    window_end: datetime
    supersedes_id: int | None
    policy_version: str
    created_at: datetime
    deviation_scale: float | None = None  # scale used by the anomaly engine for this profile


class BaselineAuditOut(ORM):
    id: int
    astronaut_id: int
    metric_code: str
    context: str
    profile_id: int | None
    action: str
    reason: str
    details: dict[str, Any]
    actor: str
    created_at: datetime


class OperatorAction(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3)


class RebuildIn(OperatorAction):
    astronaut_id: int
    metric_code: str
    context: str
    window_start: datetime
    window_end: datetime


# ---------------------------------------------------------------- alerts
class AlertEvidenceOut(ORM):
    measurement_id: int
    severity_at_time: str
    deviation: float
    measurement: MeasurementOut


class AlertAckOut(ORM):
    action: str
    actor: str
    note: str
    created_at: datetime


class AlertOut(ORM):
    id: int
    astronaut_id: int
    metric_code: str
    context: str
    severity: str
    peak_severity: str
    status: str
    direction: str
    rule_id: str
    threshold_version: str
    threshold_label: str
    baseline_profile_id: int | None
    last_deviation: float
    peak_deviation: float
    persistence_count: int
    explanation: str
    is_simulated: bool
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class AlertDetailOut(AlertOut):
    evidence: list[AlertEvidenceOut]
    acks: list[AlertAckOut]


class AckIn(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    note: str = ""


class ResolveIn(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=3)


# ---------------------------------------------------------------- simulation / system
class SimulationRunIn(BaseModel):
    scenario: str
    seed: int = 42
    astronaut_code: str = "SIM-001"
    display_name: str = "Simulated Astronaut 001"


class SimulationRunOut(BaseModel):
    scenario: str
    description: str
    astronaut: AstronautOut
    mission: MissionOut
    measurements_ingested: int
    rejected: int
    alerts_open: int
    baseline_profiles: int
    label: str = "SIMULATED DATA"


class ScenarioOut(BaseModel):
    name: str
    description: str
    days: int


class AuditOut(ORM):
    id: int
    actor: str
    action: str
    entity: str
    entity_id: str | None
    payload: dict[str, Any]
    created_at: datetime


class ThresholdRuleOut(BaseModel):
    metric_code: str
    rule_id: str
    direction: str
    bands: dict[str, float]
    persistence_required: dict[str, int]
    persistence_lookback: int


class ThresholdsOut(BaseModel):
    version: str
    label: str
    rules: list[ThresholdRuleOut]


class LatestReadingOut(BaseModel):
    metric_code: str
    context: str
    display_name: str
    category: str
    measurement: MeasurementOut
    baseline: BaselineOut | None
    baseline_enabled: bool = True  # False: logged only (e.g. radiation dose), never baselined


class OverviewOut(BaseModel):
    astronaut: AstronautOut
    mission: MissionOut | None
    mission_day: int | None
    status: str  # worst open-alert severity, or NORMAL / UNASSESSED
    open_alerts: list[AlertOut]
    latest: list[LatestReadingOut]
    measurement_count: int
    simulated: bool


class SystemStatusOut(BaseModel):
    status: str
    database: str
    data_dir: str
    threshold_version: str
    threshold_label: str
    baseline_policy_version: str
    ollama: dict[str, Any]
