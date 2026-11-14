"""ORM models for the companion: conversations, memory and self-reports (M3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from am.db.base import Base, utcnow


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    title: Mapped[str] = mapped_column(String(160), default="Conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    """One turn. role: user | assistant | tool | system_note.

    Assistant messages store the tool calls they made and the data references those
    tools returned, so every answer can be traced back to stored records.
    """

    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(64))
    generated_by: Mapped[str] = mapped_column(String(16), default="human")  # human | llm | deterministic
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # provenance for crew-network use: which signed-in device/session sent a user message
    device_name: Mapped[str | None] = mapped_column(String(80))
    device_session_id: Mapped[int | None] = mapped_column(Integer)
    client_host: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class MemoryItem(Base):
    """Layered memory.

    short   : today's conversational observations (expire after 24 h)
    long    : consolidated habits/patterns (written by the journal module, M4)
    mission : significant events across the whole mission (alerts, baseline freezes...)
    """

    __tablename__ = "memory_item"
    __table_args__ = (Index("ix_memory_lookup", "astronaut_id", "layer", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    layer: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=1)  # 1 low .. 3 high
    source: Mapped[str] = mapped_column(String(16))  # deterministic | astronaut | journal
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class SelfReport(Base):
    """Astronaut self-reported state on simple 1..5 scales.

    These are subjective check-in ratings, not validated clinical instruments.
    """

    __tablename__ = "self_report"
    __table_args__ = (Index("ix_self_report_lookup", "astronaut_id", "kind", "recorded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    checkin_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    scale_max: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    data_origin: Mapped[str] = mapped_column(String(24), default=None)  # see am.db.models_m6a.DataOrigin
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
