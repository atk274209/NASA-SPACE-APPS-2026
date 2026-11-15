"""SIMULATED space events for demonstrations (jury presentation, 2026-11-15).

While AMC runs in test mode, a demo director starts a random event shortly after start-up and
then every few minutes: a solar particle event, a cabin fault, a crew member's readings rising,
a symptom report. Each event acts on AMC's real subsystems (sensors, wearable feed, symptom
reports), so what the audience sees is AMC's actual, unscripted reaction. Every event and every
record it creates is marked SIMULATED.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class SpaceEvent(Base):
    """status: active | resolved. reactions: what AMC did, in plain words, as it happened."""

    __tablename__ = "space_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info | watch | warning | critical
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    reactions: Mapped[list[str]] = mapped_column(JSON, default=list)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    triggered_by: Mapped[str] = mapped_column(String(64), default="am-demo")
