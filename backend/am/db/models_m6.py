"""ORM models for M6: sign-in accounts and sessions, test-bench profiles, AI notes, crew log."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from am.db.base import Base, real_utcnow, utcnow


class UserAccount(Base):
    """Sign-in account.

    role: astronaut (sees and writes only the linked astronaut's records)
          doctor    (crew medical officer / flight surgeon: sees every crew member's records)
    Only a salted scrypt hash of the password is stored (see am.auth.passwords).
    """

    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    astronaut_id: Mapped[int | None] = mapped_column(ForeignKey("astronaut.id"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_test_account: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=real_utcnow)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    """A signed-in browser/device. The cookie holds a random token; only its SHA-256 is stored.

    All timestamps here are REAL time: a session must not expire because the test bench moved
    mission time forward.
    """

    __tablename__ = "auth_session"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_auth_session_token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(80), default="")
    client_host: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=real_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=real_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class SimProfile(Base):
    """Test-bench persona for one astronaut: how SIMULATED telemetry is generated for them.

    Everything produced from a profile is stored with source kind 'simulated'.
    effects: list of {"id", "preset", "label", "start_at", "end_at"} (mission-time ISO strings).
    """

    __tablename__ = "sim_profile"

    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, default=1)
    auto_telemetry: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_checkin: Mapped[bool] = mapped_column(Boolean, default=True)
    base: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    effects: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    generated_until: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AiNote(Base):
    """A note AMC keeps about what a crew member said or did, always tied to its source.

    category: symptom | mood | sleep | nutrition | exercise | interpersonal | workload | checkin | other
    detector: keyword (deterministic) | llm (model-proposed, quote verified) | system (from stored records)
    `quote` is the exact text the note is based on (a substring of the source message), or empty
    for system notes built from stored records. Notes never contain a diagnosis.
    """

    __tablename__ = "ai_note"
    __table_args__ = (Index("ix_ai_note_lookup", "astronaut_id", "noted_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversation.id"))
    symptom_report_id: Mapped[int | None] = mapped_column(ForeignKey("symptom_report.id"))
    category: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text, default="")
    detector: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | retracted
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    noted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CrewLogEntry(Base):
    """One day of the crew log, kept by AMC for the medical officer.

    `facts` is the deterministic per-astronaut digest the summary was written from; `summary`
    is drafted by the local model from those facts (or a template when no model is available).
    """

    __tablename__ = "crew_log_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    log_date: Mapped[date] = mapped_column(Date, unique=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    facts_hash: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    generated_by: Mapped[str] = mapped_column(String(16), default="deterministic")  # llm | deterministic
    model: Mapped[str | None] = mapped_column(String(64))
    grounding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
