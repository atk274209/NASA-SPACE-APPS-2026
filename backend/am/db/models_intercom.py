"""Intercom: voice-loop style text channels between the crew, the flight surgeon and AMC (2026-11-15).

Channels:
  crew      everyone on the mission (all astronauts and the doctor); AMC posts announcements here
  ast-<id>  a private loop between one astronaut and the doctor

Intercom messages are conversation between people. They are not health records and never enter
the doctor's triage feed; the private diary is separate and never appears here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class IntercomMessage(Base):
    """sender_role: doctor | astronaut | am."""

    __tablename__ = "intercom_message"
    __table_args__ = (Index("ix_intercom_channel", "channel", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(24))
    sender_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"))
    sender_name: Mapped[str] = mapped_column(String(120))
    sender_role: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IntercomRead(Base):
    """How far each user has read each channel (for unread counts)."""

    __tablename__ = "intercom_read"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_intercom_read"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    channel: Mapped[str] = mapped_column(String(24))
    last_read_id: Mapped[int] = mapped_column(Integer, default=0)
