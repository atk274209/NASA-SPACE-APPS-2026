"""ORM models for M5: symptoms, crew network/auth, settings, outbox, escalation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class SymptomReport(Base):
    """A symptom or complaint the astronaut reported (self-rated severity).

    source: checkin | manual | chat_confirmed | simulated
    Never created by the LLM on its own: chat-detected symptoms become a SymptomProposal
    that the astronaut must confirm.
    """

    __tablename__ = "symptom_report"
    __table_args__ = (Index("ix_symptom_lookup", "astronaut_id", "reported_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    symptom_code: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))  # mild | moderate | severe (self-rated)
    note: Mapped[str] = mapped_column(Text, default="")
    onset_at: Mapped[datetime | None] = mapped_column(DateTime)
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("symptom_proposal.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | resolved
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SymptomProposal(Base):
    """A symptom AM noticed in a chat message. Pending until the astronaut confirms or dismisses.

    `quote` must be an exact substring of the source message (verified deterministically).
    detector: keyword | llm
    """

    __tablename__ = "symptom_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    symptom_code: Mapped[str] = mapped_column(String(48))
    quote: Mapped[str] = mapped_column(Text)
    detector: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | dismissed
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AccessCode(Base):
    """Personal access code for crew-network sign-in. Only a salted scrypt hash is stored.

    role: crew (own records only) | medical (all crew) | operator (all crew + settings)
    """

    __tablename__ = "access_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))  # None for operator/medical
    label: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))
    code_hash: Mapped[str] = mapped_column(String(256))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)


class DeviceSession(Base):
    """Signed-in device. The bearer token is random; only its SHA-256 is stored."""

    __tablename__ = "device_session"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_device_session_token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    access_code_id: Mapped[int] = mapped_column(ForeignKey("access_code.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(80))
    client_host: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncPackage(Base):
    """Outbox entry: an explicitly authorized export prepared for transmission to ground.

    AM never transmits by itself; it prepares a file and records who authorized what.
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
