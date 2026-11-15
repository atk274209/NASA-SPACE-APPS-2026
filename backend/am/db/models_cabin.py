"""ORM models for Module P: comfort-aware cabin control against a SIMULATED cabin.

Zones: one per crew quarters (temperature, lighting) and one cabin-wide zone (pressure).
Every signal, action, undo, override and refusal is a row here (who, what, when, why).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow

MODES = ("AUTONOMOUS", "PAUSED", "MANUAL")


class CabinZone(Base):
    __tablename__ = "cabin_zone"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))  # None: cabin-wide


class EnvControl(Base):
    """One controlled variable in one zone. setpoint is what the (simulated) actuator holds.

    fault: none | sensor | actuator (Test Console fault injection). drift: added to the simulated reading.
    """

    __tablename__ = "env_control"
    __table_args__ = (UniqueConstraint("zone_id", "variable", name="uq_env_control_zone_variable"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("cabin_zone.id"))
    variable: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16), default="AUTONOMOUS")
    setpoint: Mapped[float] = mapped_column(Float)
    last_reading: Mapped[float | None] = mapped_column(Float)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime)
    fault: Mapped[str] = mapped_column(String(16), default="none")
    drift: Mapped[float] = mapped_column(Float, default=0.0)
    mode_reason: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ComfortSignal(Base):
    """A comfort wish or complaint AMC heard in conversation, with the exact quote.

    direction: up | down | report (pressure: never adjusted from a complaint, reviewed by a person)
    status: acted | refused | review
    """

    __tablename__ = "comfort_signal"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    variable: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    context: Mapped[str] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="review")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ComfortPreference(Base):
    """Per astronaut, variable and context: the value that helped (built only from feedback)."""

    __tablename__ = "comfort_preference"
    __table_args__ = (UniqueConstraint("astronaut_id", "variable", "context", name="uq_comfort_preference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    variable: Mapped[str] = mapped_column(String(16))
    context: Mapped[str] = mapped_column(String(16))
    preferred: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EnvAction(Base):
    """kind: AUTO | MANUAL | REVERT | REFUSED | EXCEPTION | MODE | FAULT"""

    __tablename__ = "env_action"
    __table_args__ = (Index("ix_env_action_timeline", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("cabin_zone.id"))
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))  # zone owner (None: cabin-wide)
    variable: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    before: Mapped[float | None] = mapped_column(Float)
    after: Mapped[float | None] = mapped_column(Float)
    rule_id: Mapped[str] = mapped_column(String(32))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("comfort_signal.id"))
    reverts_id: Mapped[int | None] = mapped_column(ForeignKey("env_action.id"))
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool | None] = mapped_column(Boolean)
    feedback: Mapped[str | None] = mapped_column(String(16))  # helped | not_helped
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EnvAlert(Base):
    """Environment alert: same NORMAL/WATCH/WARNING/CRITICAL scale as health alerts; never from the LLM."""

    __tablename__ = "env_alert"

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("cabin_zone.id"))
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))
    variable: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
