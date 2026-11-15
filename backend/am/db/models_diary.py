"""Private diary (user design, 2026-11-15).

Each astronaut keeps a daily diary by talking with AM: AM asks about the day and how they feel,
listens, and supports them. The diary belongs to the astronaut alone:

  * Only its author reads it during the mission. Nobody else can, the doctor included, until the
    mission has returned to Earth (the same mission-return mark that opens AM Core). After that,
    a doctor may read it, and every read is audited.
  * Diary messages never enter the doctor's live feed. Only a medical matter leaves the diary:
      - a symptom on the DEMO report-promptly list (chest pain, self-harm thoughts, ...) is sent at
        once as a single sentence and waits for the doctor (rule-level red), like in the companion;
      - any other symptom is offered to the astronaut, who decides whether to send it.
  * Each day gets a colour: green (a normal or good day), yellow (a difficult day), red (serious
    distress). Deterministic rules set the floor; the local model may only raise it, and only with
    a quote from the astronaut's own words.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class DiaryDay(Base):
    """One diary page: one astronaut, one mission day.

    rule_level / ai_level / level: none | green | yellow | red (level = the higher of rule and AI).
    ai_status: none | done | unavailable | rejected
    """

    __tablename__ = "diary_day"
    __table_args__ = (UniqueConstraint("astronaut_id", "day_date", name="uq_diary_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"), index=True)
    day_date: Mapped[date] = mapped_column(Date)
    mission_day: Mapped[int | None] = mapped_column(Integer)
    rule_level: Mapped[str] = mapped_column(String(8), default="green")
    rule_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    ai_status: Mapped[str] = mapped_column(String(16), default="none")
    ai_level: Mapped[str | None] = mapped_column(String(8))
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    ai_quote: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    analysed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DiaryMessage(Base):
    """role: user | assistant. generated_by: human | llm | deterministic."""

    __tablename__ = "diary_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("diary_day.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(16), default="human")
    model: Mapped[str | None] = mapped_column(String(64))
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiaryForward(Base):
    """A medical matter found in the diary.

    status: sent_urgent (report-promptly: sent at once) | offered (waiting for the astronaut) |
            sent (the astronaut sent it) | kept_private (the astronaut chose not to send it)
    report_flag_id / symptom_report_id: what the doctor received, if anything.
    """

    __tablename__ = "diary_forward"
    __table_args__ = (UniqueConstraint("message_id", "symptom_code", name="uq_diary_forward"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("diary_message.id"))
    symptom_code: Mapped[str] = mapped_column(String(48))
    quote: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    symptom_report_id: Mapped[int | None] = mapped_column(Integer)
    report_flag_id: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
