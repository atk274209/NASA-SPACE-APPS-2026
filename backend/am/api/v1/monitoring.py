"""Baseline, alert and simulation routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from am.alerts.service import AlertService
from am.api.deps import AppContext, get_ctx, get_db
from am.baseline.service import BaselineService
from am.db.base import utcnow
from am.db.enums import AlertStatus, BaselineStatus
from am.db.models import Alert, AlertEvidence, Astronaut, BaselineAudit, BaselineProfile, Mission
from am.health.ingest import IngestService
from am.schemas import (
    AckIn,
    AlertDetailOut,
    AlertOut,
    AstronautOut,
    BaselineAuditOut,
    BaselineOut,
    MissionOut,
    OperatorAction,
    RebuildIn,
    ResolveIn,
    ScenarioOut,
    SimulationRunIn,
    SimulationRunOut,
)
from am.simulation.generator import SCENARIOS, generate

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


# ---------------------------------------------------------------- baselines
@router.get("/baselines", response_model=list[BaselineOut], tags=["baselines"])
def list_baselines(db: DB, astronaut_id: int, include_history: bool = False, metric_code: str | None = None):
    stmt = select(BaselineProfile).where(BaselineProfile.astronaut_id == astronaut_id)
    if not include_history:
        stmt = stmt.where(BaselineProfile.status != BaselineStatus.SUPERSEDED.value)
    if metric_code:
        stmt = stmt.where(BaselineProfile.metric_code == metric_code)
    return db.scalars(stmt.order_by(BaselineProfile.metric_code, BaselineProfile.context,
                                    BaselineProfile.version)).all()


@router.get("/baselines/audit", response_model=list[BaselineAuditOut], tags=["baselines"])
def baseline_audit(db: DB, astronaut_id: int, metric_code: str | None = None, context: str | None = None):
    stmt = select(BaselineAudit).where(BaselineAudit.astronaut_id == astronaut_id)
    if metric_code:
        stmt = stmt.where(BaselineAudit.metric_code == metric_code)
    if context:
        stmt = stmt.where(BaselineAudit.context == context)
    return db.scalars(stmt.order_by(BaselineAudit.id.desc()).limit(500)).all()


@router.post("/baselines/{profile_id}/unfreeze", response_model=BaselineOut, tags=["baselines"])
def unfreeze_baseline(profile_id: int, body: OperatorAction, db: DB, ctx: CTX):
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    try:
        p = svc.unfreeze(profile_id, body.actor, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return p


@router.post("/baselines/rebuild", response_model=BaselineOut, status_code=201, tags=["baselines"])
def rebuild_baseline(body: RebuildIn, db: DB, ctx: CTX):
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    try:
        p = svc.rebuild(body.astronaut_id, body.metric_code, body.context,
                        start=body.window_start.replace(tzinfo=None), end=body.window_end.replace(tzinfo=None),
                        actor=body.actor, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return p


# ---------------------------------------------------------------- alerts
@router.get("/alerts", response_model=list[AlertOut], tags=["alerts"])
def list_alerts(db: DB, astronaut_id: int | None = None, status: str | None = None, open_only: bool = False):
    stmt = select(Alert)
    if astronaut_id is not None:
        stmt = stmt.where(Alert.astronaut_id == astronaut_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    if open_only:
        stmt = stmt.where(Alert.status != AlertStatus.RESOLVED.value)
    return db.scalars(stmt.order_by(Alert.last_seen_at.desc())).all()


@router.get("/alerts/{alert_id}", response_model=AlertDetailOut, tags=["alerts"])
def get_alert(alert_id: int, db: DB):
    a = db.scalars(
        select(Alert)
        .where(Alert.id == alert_id)
        .options(selectinload(Alert.evidence).selectinload(AlertEvidence.measurement), selectinload(Alert.acks))
    ).first()
    if a is None:
        raise HTTPException(404, "alert not found")
    return a


def _alert_action(fn, alert_id: int, db: Session):
    try:
        fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return get_alert(alert_id, db)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertDetailOut, tags=["alerts"])
def acknowledge_alert(alert_id: int, body: AckIn, db: DB, ctx: CTX):
    svc = AlertService(db, ctx.thresholds)
    return _alert_action(lambda: svc.acknowledge(alert_id, body.actor, body.note), alert_id, db)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertDetailOut, tags=["alerts"])
def resolve_alert(alert_id: int, body: ResolveIn, db: DB, ctx: CTX):
    svc = AlertService(db, ctx.thresholds)
    return _alert_action(lambda: svc.resolve(alert_id, body.actor, body.note), alert_id, db)


# ---------------------------------------------------------------- simulation
@router.get("/simulation/scenarios", response_model=list[ScenarioOut], tags=["simulation"])
def list_scenarios():
    return [ScenarioOut(name=s.name, description=s.description, days=s.days) for s in SCENARIOS.values()]


@router.post("/simulation/run", response_model=SimulationRunOut, status_code=201, tags=["simulation"])
def run_simulation(body: SimulationRunIn, db: DB, ctx: CTX):
    scenario = SCENARIOS.get(body.scenario)
    if scenario is None:
        raise HTTPException(404, f"unknown scenario '{body.scenario}'")
    if db.scalars(select(Astronaut).where(Astronaut.code == body.astronaut_code)).first():
        raise HTTPException(409, "astronaut code already exists; choose another code for a new simulation run")

    astronaut = Astronaut(code=body.astronaut_code, display_name=body.display_name, is_simulated=True)
    db.add(astronaut)
    db.flush()
    end = utcnow()
    mission = Mission(
        name=f"SIMULATED MISSION ({scenario.name})",
        astronaut_id=astronaut.id,
        start_date=(end - timedelta(days=scenario.days)).date(),
        planned_days=scenario.days,
        is_simulated=True,
    )
    db.add(mission)
    db.flush()

    svc = IngestService(db, ctx.thresholds, ctx.policy)
    rejected = 0
    items = generate(scenario, astronaut_id=astronaut.id, end=end, seed=body.seed)
    for item in items:
        r = svc.ingest(item, now=end)
        rejected += r.measurement.quality == "rejected"
    db.commit()

    alerts_open = db.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.astronaut_id == astronaut.id, Alert.status != AlertStatus.RESOLVED.value)
    ) or 0
    return SimulationRunOut(
        scenario=scenario.name,
        description=scenario.description,
        astronaut=AstronautOut.model_validate(astronaut),
        mission=MissionOut.model_validate(mission),
        measurements_ingested=len(items),
        rejected=rejected,
        alerts_open=alerts_open,
        baseline_profiles=svc.baselines.count_profiles(astronaut.id),
    )
