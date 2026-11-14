"""API contracts for guidance, escalation events and the body map (feature F7).

The frontend mirrors these by hand in frontend/src/api/guidance.ts; keep both in step.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["WATCH", "WARNING", "CRITICAL"]
BodyStatus = Literal["red", "yellow", "watch", "green", "no_data"]
SubjectType = Literal["alert", "symptom_report"]


# ---------------------------------------------------------------- escalation matrix
class LevelGuidanceOut(BaseModel):
    level: Level
    procedure_id: str
    plain_meaning: str
    who_to_involve: str
    guidance: str
    raises_event: bool


class SymptomRuleOut(BaseModel):
    when: str
    level: Level


class PromptSymptomOut(BaseModel):
    code: str
    label: str


class GuidanceLevelsOut(BaseModel):
    version: str
    label: str
    levels: list[LevelGuidanceOut]
    symptom_rules: list[SymptomRuleOut]
    report_promptly_label: str
    report_promptly_symptoms: list[PromptSymptomOut]
    disclaimer: str


# ---------------------------------------------------------------- escalation events
class EscalationAlertOut(BaseModel):
    id: int
    metric_code: str
    display_name: str
    context: str
    direction: str
    severity: str
    peak_severity: str
    status: str
    is_simulated: bool


class EscalationOut(BaseModel):
    id: int
    astronaut_id: int
    alert_id: int | None
    level: Level
    procedure_id: str
    status: str  # open | acknowledged | closed
    plain_meaning: str
    who_to_involve: str
    guidance: str
    label: str
    data_time: datetime | None  # time of the reading that made the alert reach this level
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    created_at: datetime
    updated_at: datetime
    alert: EscalationAlertOut | None


class AcknowledgeIn(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------- guidance for one record
class SubjectOut(BaseModel):
    type: SubjectType
    id: int
    astronaut_id: int
    title: str
    status: str
    is_simulated: bool


class GuidanceOut(BaseModel):
    subject: SubjectOut
    level: Level
    procedure_id: str
    plain_meaning: str
    who_to_involve: str
    guidance: str
    label: str
    matrix_version: str
    applies_now: bool  # False once the alert or symptom is resolved
    reasons: list[str]
    escalations: list[EscalationOut]


class CurrentItemOut(BaseModel):
    type: SubjectType
    id: int
    title: str
    level: Level
    reason: str


class CurrentGuidanceOut(BaseModel):
    astronaut_id: int
    level: Level | None
    guidance: LevelGuidanceOut | None
    items: list[CurrentItemOut]
    open_escalations: int
    label: str
    matrix_version: str
    note: str


# ---------------------------------------------------------------- body map
class ReasonOut(BaseModel):
    text: str
    level: Level | None = None
    ref_type: str | None = None
    ref_id: int | None = None


class SystemReadingOut(BaseModel):
    source: Literal["measurement", "self_report"]
    record_id: int
    code: str
    display_name: str
    context: str
    value: float
    unit: str
    recorded_at: datetime
    severity: str | None
    assessment_status: str | None
    is_simulated: bool


class SystemSymptomOut(BaseModel):
    id: int
    symptom_code: str
    label: str
    severity: str
    report_promptly: bool
    level: Level
    reported_at: datetime
    onset_at: datetime | None
    source: str
    is_simulated: bool


class SystemAlertOut(BaseModel):
    id: int
    metric_code: str
    display_name: str
    context: str
    direction: str
    severity: str
    peak_severity: str
    status: str
    level: Level
    last_seen_at: datetime
    is_simulated: bool


class SystemDocumentOut(BaseModel):
    slug: str
    title: str
    organization: str
    url: str
    matched_topics: list[str]


class BodySystemOut(BaseModel):
    code: str
    label: str
    status: BodyStatus
    level: Level | None  # guidance level matching the status (None for green / no_data)
    coverage: str  # which records can inform this system
    reasons: list[ReasonOut]
    metrics: list[SystemReadingOut]
    active_symptoms: list[SystemSymptomOut]
    open_alerts: list[SystemAlertOut]
    documents: list[SystemDocumentOut]
    last_data_at: datetime | None


class StatusRuleOut(BaseModel):
    status: BodyStatus
    meaning: str


class BodyMapOut(BaseModel):
    astronaut_id: int
    generated_at: datetime
    simulated: bool
    label: str
    rules: list[StatusRuleOut]
    rules_note: str
    knowledge_indexed: bool
    systems: list[BodySystemOut]
