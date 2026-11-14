"""ORM models (M1 scope: identity, measurements, baselines, alerts, audit).

Later milestones add tables in new modules + Alembic revisions; nothing here should
need to be rewritten to extend the system.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from am.db.base import Base, utcnow


class Astronaut(Base):
    __tablename__ = "astronaut"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Mission(Base):
    __tablename__ = "mission"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    start_date: Mapped[date] = mapped_column(Date)
    planned_days: Mapped[int | None] = mapped_column(Integer)
    # leo_station | lunar_surface | deep_space_transit | mars_surface | ground_analog | unspecified
    environment: Mapped[str] = mapped_column(String(32), default="unspecified", server_default="unspecified")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MetricDefinition(Base):
    """Registry of measurable indicators.

    plausible_min/max are ENGINEERING SANITY BOUNDS used to reject sensor/entry errors.
    They are not clinical reference ranges.
    """

    __tablename__ = "metric_definition"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32))
    allowed_contexts: Mapped[list[str]] = mapped_column(JSON)
    plausible_min: Mapped[float | None] = mapped_column(Float)
    plausible_max: Mapped[float | None] = mapped_column(Float)
    plausibility_note: Mapped[str] = mapped_column(Text, default="")
    baseline_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


class Measurement(Base):
    __tablename__ = "measurement"
    __table_args__ = (
        Index("ix_measurement_lookup", "astronaut_id", "metric_code", "context", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    metric_code: Mapped[str] = mapped_column(ForeignKey("metric_definition.code"))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    context: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    # MANUAL_TEST_ENTRY | SIMULATED_GENERATOR | REAL_DEVICE (see am.db.models_m6a.DataOrigin)
    data_origin: Mapped[str] = mapped_column(String(24), default=None)

    quality: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    # value/unit as entered before conversion to the canonical unit (None if no conversion)
    original_value: Mapped[float | None] = mapped_column(Float)
    original_unit: Mapped[str | None] = mapped_column(String(32))
    validation_notes: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Deterministic assessment result (filled by the anomaly engine)
    assessment_status: Mapped[str | None] = mapped_column(String(24))
    severity: Mapped[str | None] = mapped_column(String(16))
    deviation_score: Mapped[float | None] = mapped_column(Float)
    baseline_profile_id: Mapped[int | None] = mapped_column(ForeignKey("baseline_profile.id"))

    source: Mapped[DataSource] = relationship()


class BaselineProfile(Base):
    """Versioned personal reference profile for (astronaut, metric, context)."""

    __tablename__ = "baseline_profile"
    __table_args__ = (
        UniqueConstraint("astronaut_id", "metric_code", "context", "version", name="uq_baseline_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    metric_code: Mapped[str] = mapped_column(ForeignKey("metric_definition.code"))
    context: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    frozen_reason: Mapped[str | None] = mapped_column(Text)
    # Anchor = reference for cumulative-drift limits (initial or operator-rebuilt version).
    is_anchor: Mapped[bool] = mapped_column(Boolean, default=False)

    n: Mapped[int] = mapped_column(Integer)
    mean: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    sd: Mapped[float] = mapped_column(Float)
    mad: Mapped[float] = mapped_column(Float)
    p05: Mapped[float] = mapped_column(Float)
    p95: Mapped[float] = mapped_column(Float)
    min: Mapped[float] = mapped_column(Float)
    max: Mapped[float] = mapped_column(Float)
    trend_per_day: Mapped[float | None] = mapped_column(Float)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)

    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("baseline_profile.id"))
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BaselineAudit(Base):
    __tablename__ = "baseline_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    metric_code: Mapped[str] = mapped_column(String(64))
    context: Mapped[str] = mapped_column(String(16))
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("baseline_profile.id"))
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    metric_code: Mapped[str] = mapped_column(String(64))
    context: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    peak_severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    rule_id: Mapped[str] = mapped_column(String(64))
    threshold_version: Mapped[str] = mapped_column(String(32))
    threshold_label: Mapped[str] = mapped_column(String(64))
    baseline_profile_id: Mapped[int | None] = mapped_column(ForeignKey("baseline_profile.id"))
    last_deviation: Mapped[float] = mapped_column(Float)
    peak_deviation: Mapped[float] = mapped_column(Float)
    persistence_count: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    evidence: Mapped[list[AlertEvidence]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", order_by="AlertEvidence.id"
    )
    acks: Mapped[list[AlertAck]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", order_by="AlertAck.id"
    )


class AlertEvidence(Base):
    __tablename__ = "alert_evidence"
    __table_args__ = (UniqueConstraint("alert_id", "measurement_id", name="uq_alert_evidence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.id"))
    measurement_id: Mapped[int] = mapped_column(ForeignKey("measurement.id"))
    severity_at_time: Mapped[str] = mapped_column(String(16))
    deviation: Mapped[float] = mapped_column(Float)

    alert: Mapped[Alert] = relationship(back_populates="evidence")
    measurement: Mapped[Measurement] = relationship()


class AlertAck(Base):
    __tablename__ = "alert_ack"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.id"))
    action: Mapped[str] = mapped_column(String(16))  # acknowledge | resolve
    actor: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    alert: Mapped[Alert] = relationship(back_populates="acks")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
from am.db import models_ai  # noqa: E402,F401  (register companion tables)
from am.db import models_research  # noqa: E402,F401  (register M4 tables)
from am.db import models_m5  # noqa: E402,F401  (register M5 tables)
from am.db import models_m6  # noqa: E402,F401  (register M6 tables)
from am.db import models_m6a  # noqa: E402,F401  (register M6a tables and the data_origin default)
