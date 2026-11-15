"""ORM models for M6b: the doctor's live feed of what astronauts write, with triage flags.

See am.triage. Every message an astronaut writes to AM, every symptom report and every check-in
note gets one ReportFlag row as soon as it is stored. The deterministic rule level is set at once;
the local model's suggestion arrives later and can only raise the level shown, never lower it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow

LEVELS = ("none", "yellow", "red")


def level_rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


class ReportFlag(Base):
    """One thing an astronaut wrote, as the doctor sees it in the live feed.

    source_type: message | symptom_report | checkin_note
    rule_level:  none | yellow | red      deterministic (am.triage.rules); never from the model
    ai_status:   pending | done | unavailable | rejected | skipped
    ai_level:    none | yellow | red      the local model's suggestion, only when ai_status == done
    ai_quote:    exact text from `text` that the model cites; a suggestion without a valid quote is rejected
    """

    __tablename__ = "report_flag"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_report_flag_source"),
        Index("ix_report_flag_feed", "created_at"),
        Index("ix_report_flag_ai", "ai_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    source_type: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    rule_level: Mapped[str] = mapped_column(String(8), default="none")
    rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    rule_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)

    ai_status: Mapped[str] = mapped_column(String(16), default="pending")
    ai_level: Mapped[str | None] = mapped_column(String(8))
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    ai_quote: Mapped[str] = mapped_column(Text, default="")
    ai_model: Mapped[str | None] = mapped_column(String(64))
    ai_attempts: Mapped[int] = mapped_column(Integer, default=0)
    ai_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ai_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Doctor approval (master prompt Module D): a RULE-level red entry waits for the doctor.
    # approval_status: None (not needed) | awaiting | approved. Never set by the model.
    approval_status: Mapped[str | None] = mapped_column(String(16))
    approval_by: Mapped[str | None] = mapped_column(String(64))
    approval_at: Mapped[datetime | None] = mapped_column(DateTime)
    approval_note: Mapped[str] = mapped_column(Text, default="", server_default="")

    seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    seen_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def level(self) -> str:
        """Level shown to the doctor: the higher of the rule level and a validated AI suggestion."""
        ai = self.ai_level if self.ai_status == "done" else None
        return max(self.rule_level, ai or "none", key=level_rank)


class MeasurementProposal(Base):
    """A value the astronaut stated in conversation (Module L), waiting for one-tap confirmation.

    status: pending | confirmed | dismissed. On confirmation it becomes a Measurement from the source
    "AM conversation (CONVERSATION_EXTRACTED)", linked back here (measurement_id) and to the message.
    context is None when the astronaut did not say it (they pick it when confirming).
    """

    __tablename__ = "measurement_proposal"
    __table_args__ = (Index("ix_measurement_proposal_lookup", "astronaut_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    metric_code: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    context: Mapped[str | None] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(Text)
    detector: Mapped[str] = mapped_column(String(16), default="pattern")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    measurement_id: Mapped[int | None] = mapped_column(ForeignKey("measurement.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoutineExam(Base):
    """One scheduled routine examination (Module M). Results are what the astronaut ENTERED.

    status: scheduled | done. "overdue" is derived (scheduled and due_day < today's mission day).
    """

    __tablename__ = "routine_exam"
    __table_args__ = (UniqueConstraint("astronaut_id", "exam_code", "due_day", name="uq_routine_exam_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    exam_code: Mapped[str] = mapped_column(String(32))
    due_day: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    done_day: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    entered_by: Mapped[str | None] = mapped_column(String(64))
    data_origin: Mapped[str] = mapped_column(String(24), default="MANUAL_TEST_ENTRY", server_default="MANUAL_TEST_ENTRY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PointsEntry(Base):
    """Gamification points for adherence only (never for a health value). One row per earning event."""

    __tablename__ = "points_entry"
    __table_args__ = (UniqueConstraint("astronaut_id", "kind", "ref", name="uq_points_entry_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    kind: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(64))
    points: Mapped[int] = mapped_column(Integer)
    mission_day: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HydrationLog(Base):
    """One drink the astronaut logged. End Day writes the day's total as a water_intake measurement."""

    __tablename__ = "hydration_log"
    __table_args__ = (Index("ix_hydration_log_day", "astronaut_id", "mission_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    mission_day: Mapped[int] = mapped_column(Integer)
    amount_ml: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), default="quick_add")  # quick_add | conversation | test
    data_origin: Mapped[str] = mapped_column(String(24), default="MANUAL_TEST_ENTRY")
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SupplementLog(Base):
    """A supplement the astronaut says they took (per their mission protocol). No dose, no advice."""

    __tablename__ = "supplement_log"
    __table_args__ = (UniqueConstraint("astronaut_id", "mission_day", "supplement_code", name="uq_supplement_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    mission_day: Mapped[int] = mapped_column(Integer)
    supplement_code: Mapped[str] = mapped_column(String(32))
    data_origin: Mapped[str] = mapped_column(String(24), default="MANUAL_TEST_ENTRY")
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------------ automatic feed entries
APPROVAL_WINDOW = timedelta(days=1)


def _needs_approval(rule_level: str, is_simulated: bool, created_at: datetime | None) -> bool:
    """Rule-level red waits for the doctor. Back-filled SIMULATED history older than a day does not."""
    if rule_level != "red":
        return False
    return not is_simulated or created_at is None or created_at >= utcnow() - APPROVAL_WINDOW


def _insert_flag(connection, *, astronaut_id: int, source_type: str, source_id: int, text: str,
                 is_simulated: bool, created_at: datetime | None, rules) -> None:
    connection.execute(ReportFlag.__table__.insert().values(
        astronaut_id=astronaut_id, source_type=source_type, source_id=source_id, text=text[:2000],
        is_simulated=bool(is_simulated), rule_level=rules.level, rule_ids=rules.rule_ids,
        rule_reasons=rules.reasons, ai_status="pending", ai_reason="", ai_quote="", ai_attempts=0, ai_detail={},
        approval_status="awaiting" if _needs_approval(rules.level, is_simulated, created_at) else None,
        approval_note="",
        created_at=created_at or utcnow()))


def _on_message(_mapper, connection, target) -> None:
    if target.role != "user" or not (target.content or "").strip():
        return
    from sqlalchemy import select

    from am.db.models_ai import Conversation
    from am.triage.rules import text_rules

    astronaut_id = connection.execute(select(Conversation.astronaut_id)
                                      .where(Conversation.id == target.conversation_id)).scalar()
    if astronaut_id is None:
        return
    _insert_flag(connection, astronaut_id=astronaut_id, source_type="message", source_id=target.id,
                 text=target.content, is_simulated=False, created_at=getattr(target, "created_at", None),
                 rules=text_rules(target.content))


def _on_symptom(_mapper, connection, target) -> None:
    from am.symptoms import catalog
    from am.triage.rules import symptom_rules, text_rules

    sym = catalog.get(target.symptom_code)
    label = sym.label if sym else target.symptom_code
    text = f"Symptom report: {label}, rated {target.severity}." + (f' Note: "{target.note}"' if target.note else "")
    rules = symptom_rules(target.symptom_code, target.severity)
    if target.note:  # the free-text note can say more than the rating (e.g. self-harm)
        rules.merge(text_rules(target.note))
    _insert_flag(connection, astronaut_id=target.astronaut_id, source_type="symptom_report", source_id=target.id,
                 text=text, is_simulated=target.is_simulated, created_at=target.reported_at, rules=rules)


def _on_self_report(_mapper, connection, target) -> None:
    if target.kind != "note" or not (target.note or "").strip():
        return
    from am.triage.rules import text_rules

    _insert_flag(connection, astronaut_id=target.astronaut_id, source_type="checkin_note", source_id=target.id,
                 text=target.note, is_simulated=target.is_simulated, created_at=target.recorded_at,
                 rules=text_rules(target.note))


def _register_feed_events() -> None:
    from sqlalchemy import event

    from am.db.models_ai import Message, SelfReport
    from am.db.models_m5 import SymptomReport

    for model, fn in ((Message, _on_message), (SymptomReport, _on_symptom), (SelfReport, _on_self_report)):
        if not event.contains(model, "after_insert", fn):
            event.listen(model, "after_insert", fn)


_register_feed_events()
