"""AMC Core: the station intelligence's own reports and decisions (user design, 2026-11-14).

  * The crew has no access. The doctor always sees the decisions marked visible_to_doctor.
  * The full core (daily astronaut and crew reports, behavioural evaluation, crew attitude, the
    mission log) opens only in an emergency (a doctor breaks the seal with a logged reason, for a
    limited time, and the crew is told) or after mission return.
  * Safety information is never sealed: alerts, red flags and approvals keep flowing to the doctor
    through the live feed. AMC Core never decides medical or emergency status (Engineering Rule 8).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class AmReport(Base):
    """kind: astronaut_day | crew_day. facts/assessment are deterministic; narrative is written by the
    local model from those facts only (or by a template when the model is offline)."""

    __tablename__ = "am_report"
    __table_args__ = (UniqueConstraint("kind", "astronaut_id", "close_date", name="uq_am_report_day"),
                      Index("ix_am_report_narrative", "narrative_status"))

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))
    close_date: Mapped[date] = mapped_column(Date)
    mission_day: Mapped[int | None] = mapped_column(Integer)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assessment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    narrative: Mapped[str] = mapped_column(Text, default="")
    narrative_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|llm|template
    narrative_model: Mapped[str | None] = mapped_column(String(64))
    narrative_attempts: Mapped[int] = mapped_column(Integer, default=0)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # grounding + wording checks
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AmDecision(Base):
    """A decision AMC took inside its own remit (never medical). category: attention | checkin |
    mission_focus | doctor_notice | habitat | crew. executed: AMC applied it itself (False: a plan or a
    recommendation for a person)."""

    __tablename__ = "am_decision"
    __table_args__ = (Index("ix_am_decision_day", "close_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    close_date: Mapped[date] = mapped_column(Date)
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))
    category: Mapped[str] = mapped_column(String(24))
    level: Mapped[str] = mapped_column(String(16), default="routine")  # routine | watch | priority
    decision: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String(32))
    visible_to_doctor: Mapped[bool] = mapped_column(Boolean, default=False)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AmUnseal(Base):
    """kind: emergency (time-limited, reason required, crew told) | mission_return (stays open)."""

    __tablename__ = "am_unseal"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
