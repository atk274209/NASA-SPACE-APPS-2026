"""ORM models for M5: symptoms, settings, outbox, escalation.

Sign-in accounts and sessions live in am.db.models_m6.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class SymptomReport(Base):
    """A symptom or complaint in the astronaut's own words or rating.

    source:   manual | checkin | chat_confirmed | chat_auto | simulated
    severity: mild | moderate | severe (self-rated) | unrated (noted from chat, not rated yet)
    status:   active | resolved | retracted (noted in error; kept for the audit trail)
    A chat_auto report is written by AMC from an exact quote of the astronaut's message
    (see SymptomProposal); the astronaut can rate it or retract it. The language model never
    sets severity and never writes a report without a verified quote.
    """

    __tablename__ = "symptom_report"
    __table_args__ = (Index("ix_symptom_lookup", "astronaut_id", "reported_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    symptom_code: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))  # mild | moderate | severe | unrated
    note: Mapped[str] = mapped_column(Text, default="")
    onset_at: Mapped[datetime | None] = mapped_column(DateTime)
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("symptom_proposal.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | resolved | retracted
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    data_origin: Mapped[str] = mapped_column(String(24), default=None)  # see am.db.models_m6a.DataOrigin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SymptomProposal(Base):
    """A symptom AMC noticed in a chat message: the detection record (message, exact quote, detector).

    `quote` must be an exact substring of the source message (verified deterministically).
    detector: keyword | llm
    status:   pending (waiting for the astronaut) | confirmed | dismissed | auto_noted (AMC recorded
              a chat_auto report from it) | retracted (the astronaut said the note was wrong)
    """

    __tablename__ = "symptom_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    symptom_code: Mapped[str] = mapped_column(String(48))
    quote: Mapped[str] = mapped_column(Text)
    detector: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # see class docstring
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SyncPackage(Base):
    """Outbox entry: an explicitly authorized export prepared for transmission to ground.

    AMC never transmits by itself; it prepares a file and records who authorized what.
    status: prepared | exported | cancelled (ground reports); stored | pruned (backups)
    """

    __tablename__ = "sync_package"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))
    kind: Mapped[str] = mapped_column(String(32))  # ground_report | backup
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    authorized_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EscalationEvent(Base):
    """Local escalation record raised deterministically from an alert (DEMO procedure)."""

    __tablename__ = "escalation_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alert.id"))
    level: Mapped[str] = mapped_column(String(16))  # WATCH | WARNING | CRITICAL
    procedure_id: Mapped[str] = mapped_column(String(48))
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | closed
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
