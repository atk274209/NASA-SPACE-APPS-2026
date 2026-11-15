"""ORM models for M6a: fictional astronaut profiles, End Day runs, and the data-origin label.

See docs/M6_SPEC.md.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, utcnow


class DataOrigin(StrEnum):
    """Where a health record came from. Every record in this prototype is test data."""

    MANUAL_TEST_ENTRY = "MANUAL_TEST_ENTRY"      # typed by a tester
    SIMULATED_GENERATOR = "SIMULATED_GENERATOR"  # written by the simulator / a scenario fixture
    REAL_DEVICE = "REAL_DEVICE"                  # reserved: nothing in the prototype assigns it


DATA_ORIGINS = tuple(o.value for o in DataOrigin)


def default_origin(is_simulated: bool | None) -> str:
    return (DataOrigin.SIMULATED_GENERATOR if is_simulated else DataOrigin.MANUAL_TEST_ENTRY).value


def _fill_origin(_mapper, _conn, target) -> None:
    if getattr(target, "data_origin", None) is None:
        target.data_origin = default_origin(getattr(target, "is_simulated", False))


def _register_origin_default() -> None:
    from am.db.models import Measurement
    from am.db.models_ai import SelfReport
    from am.db.models_m5 import SymptomReport

    for model in (Measurement, SymptomReport, SelfReport):
        if not event.contains(model, "before_insert", _fill_origin):
            event.listen(model, "before_insert", _fill_origin)


class AstronautProfile(Base):
    """FICTIONAL profile of a test astronaut, used to exercise person-specific analysis.

    medical_history: list of {"condition", "status", "since", "note"}; status is
    active | resolved | monitoring. Every value is invented for testing; none describes a real
    person. Conditions are free text: AM does not derive any threshold from them.
    """

    __tablename__ = "astronaut_profile"

    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"), primary_key=True)
    callsign: Mapped[str] = mapped_column(String(32), default="")
    crew_role: Mapped[str] = mapped_column(String(64), default="")
    age_years: Mapped[int | None] = mapped_column(Integer)
    bio: Mapped[str] = mapped_column(Text, default="")
    medical_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    chronic_conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    allergies: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_fictional: Mapped[bool] = mapped_column(Boolean, default=True)
    # Crew hydration leaderboard (adherence only, never amounts). The astronaut can leave it any time.
    leaderboard_opt_in: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DayCloseRun(Base):
    """One End Day run for the whole crew, keyed by the mission date it closed.

    status: running | completed | failed
    steps:  {step_name: {"status": "done" | "failed", "at": iso, "detail": {...}}}
    results: {astronaut_id (str): {"mission_day": int, "warnings": [...], ...}}
    """

    __tablename__ = "day_close_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    close_date: Mapped[date] = mapped_column(Date, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    steps: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


_register_origin_default()
