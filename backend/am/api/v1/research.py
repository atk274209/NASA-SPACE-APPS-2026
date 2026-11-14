"""Research intelligence, journal, daily missions and knowledge-base routes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.config import REPO_ROOT
from am.db.models_research import (
    AnalysisRun,
    DailyTask,
    JournalEntry,
    KnowledgeDocument,
    ResearchFinding,
    ResearchObservation,
)
from am.journal.service import JournalService
from am.missions.service import PROTOCOL_LABEL, MissionService
from am.rag.knowledge import ingest_directory, search
from am.research.ari import build_observation
from am.research.reference import reference_for
from am.research.series import build_daily_series, current_mission_day
from am.research.service import run_correlation

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge" / "sources"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AstronautIn(BaseModel):
    astronaut_id: int


# ------------------------------------------------------------------ schemas
class FindingOut(ORM):
    id: int
    run_id: int
    finding_type: str
    x_key: str | None
    y_key: str
    x_label: str | None
    y_label: str
    lag_days: int
    n_days: int
    rho: float
    p_value: float
    q_value: float
    rho_detrended: float | None
    p_detrended: float | None
    detrended_supported: bool | None
    slope_per_day: float | None
    unit: str
    days: list[int]
    summary: str
    caveat: str


class RunOut(ORM):
    id: int
    astronaut_id: int
    kind: str
    params: dict[str, Any]
    n_tests: int
    n_series: int
    through_day: int | None
    is_simulated: bool
    created_at: datetime
    findings: list[FindingOut]


class SeriesOut(BaseModel):
    key: str
    label: str
    unit: str
    kind: str
    aggregation: str
    points: list[dict[str, float]]
    reference: dict[str, Any] | None


class ObservationOut(ORM):
    id: int
    astronaut_id: int
    finding_ids: list[int]
    title: str
    observation: str
    scientific_context: str
    hypothesis: str
    evidence_level: str
    confidence_note: str
    doc_refs: list[dict[str, Any]]
    generated_by: str
    model: str | None
    grounding: dict[str, Any]
    status: str
    is_simulated: bool
    created_at: datetime


class ObservationIn(BaseModel):
    finding_id: int
    use_llm: bool = True


class ReviewIn(BaseModel):
    status: str = Field(pattern="^(reviewed|rejected|draft)$")
    actor: str = Field(min_length=1, max_length=64)


class JournalOut(ORM):
    id: int
    astronaut_id: int
    mission_day: int
    day_date: date
    sections: dict[str, Any]
    flags: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    refs: list[dict[str, Any]]
    complete_day: bool
    is_simulated: bool
    generator_version: str
    generated_at: datetime


class JournalGenerateIn(BaseModel):
    astronaut_id: int
    first_day: int | None = None
    last_day: int | None = None


class TaskOut(ORM):
    id: int
    mission_day: int
    template_code: str
    category: str
    title: str
    detail: str
    priority: int
    reason: str
    reason_refs: list[dict[str, Any]]
    protocol_ref: str
    completion_rule: str
    status: str
    completed_by: str | None
    completed_at: datetime | None


class TodayOut(BaseModel):
    mission_day: int
    protocol_note: str
    tasks: list[TaskOut]
    done: int
    total: int


class TaskStatusIn(BaseModel):
    status: str


class DocumentOut(ORM):
    id: int
    slug: str
    title: str
    authors: list[str]
    organization: str
    published_date: str | None
    url: str
    doi: str | None
    source_type: str
    topics: list[str]
    license_basis: str
    key_findings: list[str]
    limitations: list[str]
    retrieved_at: str | None
    verification_notes: str


class KnowledgeIngestOut(BaseModel):
    loaded: int
    updated: int
    unchanged: int
    skipped: list[str]
    embedded: int
    embed_model: str | None
    embed_error: str | None
    documents_total: int


# ------------------------------------------------------------------ correlation
@router.post("/research/correlation/run", response_model=RunOut, status_code=201, tags=["research"])
def correlation_run(body: AstronautIn, db: DB):
    try:
        run = run_correlation(db, body.astronaut_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return run


@router.get("/research/correlation/latest", response_model=RunOut | None, tags=["research"])
def correlation_latest(db: DB, astronaut_id: int):
    return db.scalars(select(AnalysisRun).where(AnalysisRun.astronaut_id == astronaut_id)
                      .order_by(AnalysisRun.id.desc()).limit(1)).first()


@router.get("/research/series", response_model=list[SeriesOut], tags=["research"])
def daily_series(db: DB, astronaut_id: int):
    out = []
    for s in build_daily_series(db, astronaut_id).values():
        ref = reference_for(s)
        out.append(SeriesOut(key=s.key, label=s.label, unit=s.unit, kind=s.kind, aggregation=s.aggregation,
                             points=[{"day": d, "value": s.values[d]} for d in s.days()],
                             reference=None if ref is None else {"median": ref.median, "scale": ref.scale,
                                                                 "days": list(ref.days)}))
    return sorted(out, key=lambda x: x.key)


# ------------------------------------------------------------------ observations
@router.post("/research/observations", response_model=ObservationOut, status_code=201, tags=["research"])
def create_observation(body: ObservationIn, db: DB, ctx: CTX):
    try:
        obs = build_observation(db, body.finding_id, llm=ctx.llm if body.use_llm else None,
                                chat_model=ctx.settings.chat_model, embed_model=ctx.settings.embed_model)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return obs


@router.get("/research/observations", response_model=list[ObservationOut], tags=["research"])
def list_observations(db: DB, astronaut_id: int):
    return db.scalars(select(ResearchObservation).where(ResearchObservation.astronaut_id == astronaut_id)
                      .order_by(ResearchObservation.id.desc())).all()


@router.post("/research/observations/{obs_id}/review", response_model=ObservationOut, tags=["research"])
def review_observation(obs_id: int, body: ReviewIn, db: DB):
    from am.audit import audit

    obs = db.get(ResearchObservation, obs_id)
    if obs is None:
        raise HTTPException(404, "observation not found")
    obs.status = body.status
    audit(db, actor=body.actor, action=f"observation.{body.status}", entity="research_observation",
          entity_id=obs.id)
    db.commit()
    return obs


@router.get("/research/findings/{finding_id}", response_model=FindingOut, tags=["research"])
def get_finding(finding_id: int, db: DB):
    f = db.get(ResearchFinding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    return f


# ------------------------------------------------------------------ journal
@router.post("/journal/generate", response_model=list[JournalOut], status_code=201, tags=["journal"])
def journal_generate(body: JournalGenerateIn, db: DB):
    try:
        first = 1 if body.first_day is None else body.first_day
        entries = JournalService(db).generate_range(body.astronaut_id, first, body.last_day)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return entries


@router.get("/journal", response_model=list[JournalOut], tags=["journal"])
def journal_list(db: DB, astronaut_id: int):
    return db.scalars(select(JournalEntry).where(JournalEntry.astronaut_id == astronaut_id)
                      .order_by(JournalEntry.mission_day.desc())).all()


# ------------------------------------------------------------------ missions
@router.get("/missions/today", response_model=TodayOut, tags=["missions"])
def missions_today(db: DB, astronaut_id: int):
    svc = MissionService(db)
    try:
        tasks = svc.ensure_today(astronaut_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    day = current_mission_day(db, astronaut_id) or 0
    return TodayOut(mission_day=day, protocol_note=PROTOCOL_LABEL, tasks=[TaskOut.model_validate(t) for t in tasks],
                    done=sum(t.status == "done" for t in tasks), total=len(tasks))


@router.post("/missions/tasks/{task_id}", response_model=TaskOut, tags=["missions"])
def set_task_status(task_id: int, body: TaskStatusIn, db: DB):
    try:
        t = MissionService(db).set_status(task_id, body.status)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return t


@router.get("/missions/history", response_model=list[dict[str, Any]], tags=["missions"])
def missions_history(db: DB, astronaut_id: int):
    rows = db.scalars(select(DailyTask).where(DailyTask.astronaut_id == astronaut_id)).all()
    by_day: dict[int, list[DailyTask]] = {}
    for t in rows:
        by_day.setdefault(t.mission_day, []).append(t)
    return [{"mission_day": d, "done": sum(t.status == "done" for t in ts), "total": len(ts)}
            for d, ts in sorted(by_day.items())]


# ------------------------------------------------------------------ knowledge
@router.post("/knowledge/ingest", response_model=KnowledgeIngestOut, tags=["knowledge"])
def knowledge_ingest(db: DB, ctx: CTX):
    rep = ingest_directory(db, KNOWLEDGE_DIR, ctx.llm, ctx.settings.embed_model)
    db.commit()
    total = len(db.scalars(select(KnowledgeDocument.id)).all())
    return KnowledgeIngestOut(**rep.__dict__, documents_total=total)


@router.get("/knowledge/documents", response_model=list[DocumentOut], tags=["knowledge"])
def knowledge_documents(db: DB):
    return db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.title)).all()


@router.get("/knowledge/search", response_model=list[dict[str, Any]], tags=["knowledge"])
def knowledge_search(db: DB, ctx: CTX, q: str, k: int = 5):
    return [h.as_dict() for h in search(db, q, llm=ctx.llm, embed_model=ctx.settings.embed_model, k=min(k, 20))]
