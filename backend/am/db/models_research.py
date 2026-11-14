"""ORM models for M4: analysis, research observations, journal, daily missions, knowledge base."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, \
    UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from am.db.base import Base, utcnow


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    kind: Mapped[str] = mapped_column(String(32))  # correlation
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    n_tests: Mapped[int] = mapped_column(Integer, default=0)
    n_series: Mapped[int] = mapped_column(Integer, default=0)
    through_day: Mapped[int | None] = mapped_column(Integer)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    findings: Mapped[list[ResearchFinding]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                           order_by="ResearchFinding.id")


class ResearchFinding(Base):
    """A statistically screened pattern. Never a causal claim."""

    __tablename__ = "research_finding"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"))
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    finding_type: Mapped[str] = mapped_column(String(32))  # trend | correlation | temporal_association
    x_key: Mapped[str | None] = mapped_column(String(64))
    y_key: Mapped[str] = mapped_column(String(64))
    x_label: Mapped[str | None] = mapped_column(String(160))
    y_label: Mapped[str] = mapped_column(String(160))
    lag_days: Mapped[int] = mapped_column(Integer, default=0)
    n_days: Mapped[int] = mapped_column(Integer)
    rho: Mapped[float] = mapped_column(Float)
    p_value: Mapped[float] = mapped_column(Float)
    q_value: Mapped[float] = mapped_column(Float)
    rho_detrended: Mapped[float | None] = mapped_column(Float)
    p_detrended: Mapped[float | None] = mapped_column(Float)
    detrended_supported: Mapped[bool | None] = mapped_column(Boolean)
    slope_per_day: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    days: Mapped[list[int]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text)
    caveat: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[AnalysisRun] = relationship(back_populates="findings")


class ResearchObservation(Base):
    """ARI observation: finding(s) + stored literature + (optionally) LLM-drafted hypothesis."""

    __tablename__ = "research_observation"

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    finding_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(200))
    observation: Mapped[str] = mapped_column(Text)           # deterministic, from findings
    scientific_context: Mapped[str] = mapped_column(Text, default="")  # retrieved literature passages
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    evidence_level: Mapped[str] = mapped_column(String(32))  # correlation | temporal_association | trend | hypothesis
    confidence_note: Mapped[str] = mapped_column(Text)
    doc_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(16))  # deterministic | llm
    model: Mapped[str | None] = mapped_column(String(64))
    grounding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | reviewed | rejected
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class JournalEntry(Base):
    __tablename__ = "journal_entry"
    __table_args__ = (UniqueConstraint("astronaut_id", "mission_day", name="uq_journal_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    mission_day: Mapped[int] = mapped_column(Integer)
    day_date: Mapped[date] = mapped_column(Date)
    sections: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    observations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    complete_day: Mapped[bool] = mapped_column(Boolean, default=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    generator_version: Mapped[str] = mapped_column(String(16))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Day marking (deterministic, DEMO rules): none | green | yellow | red
    day_level: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    day_level_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]")
    # Narrative: written by the local LLM from this entry's facts (or a deterministic template)
    narrative: Mapped[str] = mapped_column(Text, default="", server_default="")
    narrative_generated_by: Mapped[str | None] = mapped_column(String(16))  # llm | deterministic
    narrative_model: Mapped[str | None] = mapped_column(String(64))
    narrative_grounding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    narrative_generated_at: Mapped[datetime | None] = mapped_column(DateTime)


class DailyTask(Base):
    __tablename__ = "daily_task"
    __table_args__ = (UniqueConstraint("astronaut_id", "mission_day", "template_code", name="uq_daily_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    astronaut_id: Mapped[int] = mapped_column(ForeignKey("astronaut.id"))
    mission_day: Mapped[int] = mapped_column(Integer)
    template_code: Mapped[str] = mapped_column(String(48))
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high .. 3 low
    reason: Mapped[str] = mapped_column(Text)
    reason_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    protocol_ref: Mapped[str] = mapped_column(String(64))
    completion_rule: Mapped[str] = mapped_column(String(48), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done | skipped
    completed_by: Mapped[str | None] = mapped_column(String(64))  # astronaut | data
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(400))
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    organization: Mapped[str] = mapped_column(String(200), default="")
    published_date: Mapped[str | None] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(600))
    doi: Mapped[str | None] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(48))
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    license_basis: Mapped[str] = mapped_column(Text, default="")
    key_findings: Mapped[list[str]] = mapped_column(JSON, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    retrieved_at: Mapped[str | None] = mapped_column(String(16))
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                       order_by="DocumentChunk.ord")


class DocumentChunk(Base):
    __tablename__ = "document_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_document.id"))
    ord: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # passage | finding | limitation
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(600))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embed_model: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")
