"""Daily duty roster: AM shares out the station's work by each astronaut's energy and morale
(user design, 2026-11-15).

Inputs are what the crew already shares with the medical team: check-in ratings (mood, stress,
fatigue, sleep quality, motivation), recorded sleep, active symptoms and open alerts. The private
diary is never read for this. The split is deterministic and explained per assignment; the local
model only writes a short note to each astronaut, from their point of view.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class DutyPlan(Base):
    __tablename__ = "duty_plan"
    __table_args__ = (UniqueConstraint("plan_date", name="uq_duty_plan_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(64), default="am")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # tasks nobody could take without overloading someone on a light day
    postponed: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]")


class DutyReadiness(Base):
    """energy / morale: 0-100, or None when there is nothing to go on. note: AM's words to the astronaut."""

    __tablename__ = "duty_readiness"
    __table_args__ = (UniqueConstraint("plan_id", "astronaut_id", name="uq_duty_readiness"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("duty_plan.id"), index=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    energy: Mapped[float | None] = mapped_column(Float)
    morale: Mapped[float | None] = mapped_column(Float)
    capacity: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(12))  # full | steady | light
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    note_by: Mapped[str] = mapped_column(String(16), default="template")  # llm | template


class DutyAssignment(Base):
    """status: planned | done. kind: physical | focus | social | restorative."""

    __tablename__ = "duty_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("duty_plan.id"), index=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"), index=True)
    task_code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16))
    load: Mapped[int] = mapped_column(Integer)
    minutes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="planned")
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
