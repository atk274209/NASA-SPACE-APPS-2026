"""Core routes: system, astronauts, metrics, measurements, audit."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from am.auth.guard import CurrentUser, doctor_only, signed_in
from am.api.deps import AppContext, get_ctx, get_db
from am.settings_store import effective_chat_model, effective_embed_model
from am.db.models import Astronaut, AuditLog, Measurement, MetricDefinition, Mission
from am.health.ingest import IngestResult, IngestService, MeasurementInput
from am.schemas import (
    AssessmentOut,
    AstronautCreate,
    AstronautOut,
    AuditOut,
    BaselineEventOut,
    BatchIn,
    BatchOut,
    IngestOut,
    MeasurementIn,
    MeasurementOut,
    MetricOut,
    MissionOut,
    SystemStatusOut,
)

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


@router.get("/system/status", response_model=SystemStatusOut, tags=["system"])
@signed_in
def system_status(db: DB, ctx: CTX) -> SystemStatusOut:
    db_state = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive
        db_state = f"error: {exc}"
    ollama: dict = {"url": ctx.settings.ollama_url, "reachable": False, "models": []}
    try:
        r = httpx.get(f"{ctx.settings.ollama_url}/api/tags", timeout=1.5)
        r.raise_for_status()
        ollama["reachable"] = True
        ollama["models"] = [m.get("name") for m in r.json().get("models", [])]
    except Exception as exc:
        ollama["error"] = type(exc).__name__
    ollama["configured_chat_model"] = effective_chat_model(db, ctx.settings)
    ollama["configured_embed_model"] = effective_embed_model(db, ctx.settings)
    return SystemStatusOut(
        status="ok" if db_state == "ok" else "degraded",
        database=db_state,
        data_dir=str(ctx.settings.data_dir),
        threshold_version=ctx.thresholds.version,
        threshold_label=ctx.thresholds.label,
        baseline_policy_version=ctx.policy.version,
        ollama=ollama,
    )


# ---------------------------------------------------------------- astronauts
@router.get("/astronauts", response_model=list[AstronautOut], tags=["astronauts"])
@signed_in
def list_astronauts(db: DB, principal: CurrentUser):
    stmt = select(Astronaut).order_by(Astronaut.id)
    if not principal.is_doctor:  # a crew member sees only their own record
        stmt = stmt.where(Astronaut.id == principal.astronaut_id)
    return db.scalars(stmt).all()


@router.post("/astronauts", response_model=AstronautOut, status_code=201, tags=["astronauts"])
@doctor_only
def create_astronaut(body: AstronautCreate, db: DB):
    if db.scalars(select(Astronaut).where(Astronaut.code == body.code)).first():
        raise HTTPException(409, "astronaut code already exists")
    a = Astronaut(code=body.code, display_name=body.display_name, is_simulated=False)
    db.add(a)
    db.commit()
    return a


@router.get("/astronauts/{astronaut_id}/missions", response_model=list[MissionOut], tags=["astronauts"])
def list_missions(astronaut_id: int, db: DB):
    return db.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id).order_by(Mission.id)).all()


# ---------------------------------------------------------------- metrics
@router.get("/metrics", response_model=list[MetricOut], tags=["metrics"])
@signed_in
def list_metrics(db: DB):
    return db.scalars(select(MetricDefinition).order_by(MetricDefinition.category, MetricDefinition.code)).all()


# ---------------------------------------------------------------- measurements
def _to_out(r: IngestResult) -> IngestOut:
    a = r.assessment
    return IngestOut(
        measurement=MeasurementOut.model_validate(r.measurement),
        assessment=AssessmentOut(
            status=a.status.value,
            severity=a.severity.value if a.severity else None,
            instant_severity=a.instant_severity.value if a.instant_severity else None,
            z=a.z,
            direction=a.direction,
            persistence_count=a.persistence_count,
            baseline_profile_id=a.baseline_profile_id,
            rule_id=a.rule_id,
            reasons=a.reasons,
        ),
        alert_id=r.alert_id,
        baseline_event=BaselineEventOut(**r.baseline_event.__dict__) if r.baseline_event else None,
    )


def _input(body: MeasurementIn) -> MeasurementInput:
    return MeasurementInput(**body.model_dump())


@router.post("/measurements", response_model=IngestOut, status_code=201, tags=["measurements"])
def ingest_measurement(body: MeasurementIn, db: DB, ctx: CTX):
    svc = IngestService(db, ctx.thresholds, ctx.policy)
    try:
        result = svc.ingest(_input(body))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _to_out(result)


@router.post("/measurements/batch", response_model=BatchOut, status_code=201, tags=["measurements"])
def ingest_batch(body: BatchIn, db: DB, ctx: CTX):
    svc = IngestService(db, ctx.thresholds, ctx.policy)
    results = []
    try:
        for item in sorted(body.items, key=lambda i: i.recorded_at):
            results.append(svc.ingest(_input(item)))
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    rejected = sum(1 for r in results if r.measurement.quality == "rejected")
    return BatchOut(
        accepted=len(results) - rejected,
        rejected=rejected,
        alerts_touched=sorted({r.alert_id for r in results if r.alert_id}),
        results=[_to_out(r) for r in results],
    )


@router.get("/measurements", response_model=list[MeasurementOut], tags=["measurements"])
def list_measurements(
    db: DB,
    astronaut_id: int,
    metric_code: str | None = None,
    context: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_rejected: bool = False,
    limit: Annotated[int, Query(ge=1, le=20000)] = 2000,
):
    stmt = select(Measurement).where(Measurement.astronaut_id == astronaut_id)
    if metric_code:
        stmt = stmt.where(Measurement.metric_code == metric_code)
    if context:
        stmt = stmt.where(Measurement.context == context)
    if since:
        stmt = stmt.where(Measurement.recorded_at >= since.replace(tzinfo=None))
    if until:
        stmt = stmt.where(Measurement.recorded_at <= until.replace(tzinfo=None))
    if not include_rejected:
        stmt = stmt.where(Measurement.quality != "rejected")
    rows = db.scalars(stmt.order_by(Measurement.recorded_at.desc()).limit(limit)).all()
    return list(reversed(rows))


# ---------------------------------------------------------------- audit
@router.get("/audit", response_model=list[AuditOut], tags=["system"])
@doctor_only
def list_audit(db: DB, limit: Annotated[int, Query(ge=1, le=1000)] = 200, action_prefix: str | None = None):
    stmt = select(AuditLog)
    if action_prefix:
        stmt = stmt.where(AuditLog.action.startswith(action_prefix))
    return db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit)).all()
