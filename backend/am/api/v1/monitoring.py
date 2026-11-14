"""Baseline, alert and simulation routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from am.alerts.service import AlertService
from am.auth.guard import doctor_only, signed_in
from am.api.deps import AppContext, get_ctx, get_db
from am.baseline.service import BaselineService
from am.db.base import utcnow
from am.db.enums import AlertStatus, BaselineStatus, DataQuality, Severity
from am.db.models import (
    Alert,
    AlertEvidence,
    Astronaut,
    BaselineAudit,
    BaselineProfile,
    Measurement,
    MetricDefinition,
    Mission,
)
from am.health.ingest import IngestService
from am.schemas import (
    AckIn,
    AlertDetailOut,
    AlertOut,
    AstronautOut,
    BaselineAuditOut,
    BaselineOut,
    LatestReadingOut,
    MeasurementOut,
    MissionOut,
    OverviewOut,
    OperatorAction,
    RebuildIn,
    ResolveIn,
    ScenarioOut,
    SimulationRunIn,
    SimulationRunOut,
    ThresholdRuleOut,
    ThresholdsOut,
)
from am.simulation.generator import SCENARIOS, generate

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


# ---------------------------------------------------------------- baselines
@router.get("/baselines", response_model=list[BaselineOut], tags=["baselines"])
def list_baselines(db: DB, ctx: CTX, astronaut_id: int, include_history: bool = False, metric_code: str | None = None):
    stmt = select(BaselineProfile).where(BaselineProfile.astronaut_id == astronaut_id)
    if not include_history:
        stmt = stmt.where(BaselineProfile.status != BaselineStatus.SUPERSEDED.value)
    if metric_code:
        stmt = stmt.where(BaselineProfile.metric_code == metric_code)
    rows = db.scalars(stmt.order_by(BaselineProfile.metric_code, BaselineProfile.context,
                                    BaselineProfile.version)).all()
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    return [baseline_out(svc, p) for p in rows]


def baseline_out(svc: BaselineService, p: BaselineProfile) -> BaselineOut:
    out = BaselineOut.model_validate(p)
    out.deviation_scale = svc.scale_of(p)
    return out


@router.get("/baselines/audit", response_model=list[BaselineAuditOut], tags=["baselines"])
def baseline_audit(db: DB, astronaut_id: int, metric_code: str | None = None, context: str | None = None):
    stmt = select(BaselineAudit).where(BaselineAudit.astronaut_id == astronaut_id)
    if metric_code:
        stmt = stmt.where(BaselineAudit.metric_code == metric_code)
    if context:
        stmt = stmt.where(BaselineAudit.context == context)
    return db.scalars(stmt.order_by(BaselineAudit.id.desc()).limit(500)).all()


@router.post("/baselines/{profile_id}/unfreeze", response_model=BaselineOut, tags=["baselines"])
@doctor_only
def unfreeze_baseline(profile_id: int, body: OperatorAction, db: DB, ctx: CTX):
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    try:
        p = svc.unfreeze(profile_id, body.actor, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return baseline_out(svc, p)


@router.post("/baselines/rebuild", response_model=BaselineOut, status_code=201, tags=["baselines"])
@doctor_only
def rebuild_baseline(body: RebuildIn, db: DB, ctx: CTX):
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    try:
        p = svc.rebuild(body.astronaut_id, body.metric_code, body.context,
                        start=body.window_start.replace(tzinfo=None), end=body.window_end.replace(tzinfo=None),
                        actor=body.actor, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return baseline_out(svc, p)


@router.get("/thresholds", response_model=ThresholdsOut, tags=["baselines"])
@signed_in
def get_thresholds(ctx: CTX):
    t = ctx.thresholds
    return ThresholdsOut(
        version=t.version,
        label=t.label,
        rules=[
            ThresholdRuleOut(
                metric_code=code,
                rule_id=r.rule_id,
                direction=r.direction,
                bands={k.value: v for k, v in r.bands.items()},
                persistence_required={k.value: v for k, v in r.persistence.required.items()},
                persistence_lookback=r.persistence.lookback,
            )
            for code, r in t.rules.items()
        ],
    )


@router.get("/astronauts/{astronaut_id}/overview", response_model=OverviewOut, tags=["astronauts"])
def astronaut_overview(astronaut_id: int, db: DB, ctx: CTX):
    astronaut = db.get(Astronaut, astronaut_id)
    if astronaut is None:
        raise HTTPException(404, "astronaut not found")
    mission = db.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                         .order_by(Mission.id.desc()).limit(1)).first()
    mission_day = (utcnow().date() - mission.start_date).days + 1 if mission else None
    open_alerts = db.scalars(select(Alert).where(Alert.astronaut_id == astronaut_id,
                                                 Alert.status != AlertStatus.RESOLVED.value)
                             .order_by(Alert.last_seen_at.desc())).all()

    # latest non-rejected reading per (metric, context)
    latest_ids = select(func.max(Measurement.id)).where(
        Measurement.astronaut_id == astronaut_id, Measurement.quality != DataQuality.REJECTED.value
    ).group_by(Measurement.metric_code, Measurement.context)
    latest_rows = db.scalars(select(Measurement).where(Measurement.id.in_(latest_ids))).all()
    metrics = {md.code: md for md in db.scalars(select(MetricDefinition)).all()}
    svc = BaselineService(db, ctx.policy, ctx.thresholds)
    latest = []
    for row in sorted(latest_rows, key=lambda r: (metrics[r.metric_code].category, r.metric_code, r.context)):
        prof = svc.active_profile(astronaut_id, row.metric_code, row.context)
        md = metrics[row.metric_code]
        latest.append(LatestReadingOut(
            metric_code=row.metric_code, context=row.context, display_name=md.display_name, category=md.category,
            measurement=MeasurementOut.model_validate(row),
            baseline=baseline_out(svc, prof) if prof else None,
            baseline_enabled=md.baseline_enabled,
        ))

    if open_alerts:
        # Unacknowledged alerts report their PEAK severity: a critical episode stays visible
        # until a person has reviewed it, even if the latest reading is less deviant.
        status = max(
            (Severity(a.peak_severity if a.status == AlertStatus.ACTIVE.value else a.severity) for a in open_alerts),
            key=lambda s: s.rank,
        ).value
    elif any(r.baseline for r in latest):
        status = Severity.NORMAL.value
    else:
        status = "UNASSESSED"
    count = db.scalar(select(func.count()).select_from(Measurement)
                      .where(Measurement.astronaut_id == astronaut_id)) or 0
    return OverviewOut(
        astronaut=AstronautOut.model_validate(astronaut),
        mission=MissionOut.model_validate(mission) if mission else None,
        mission_day=mission_day,
        status=status,
        open_alerts=[AlertOut.model_validate(a) for a in open_alerts],
        latest=latest,
        measurement_count=count,
        simulated=astronaut.is_simulated,
    )


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
@doctor_only
def resolve_alert(alert_id: int, body: ResolveIn, db: DB, ctx: CTX):
    svc = AlertService(db, ctx.thresholds)
    return _alert_action(lambda: svc.resolve(alert_id, body.actor, body.note), alert_id, db)


# ---------------------------------------------------------------- simulation
@router.get("/simulation/scenarios", response_model=list[ScenarioOut], tags=["simulation"])
@doctor_only
def list_scenarios():
    return [ScenarioOut(name=s.name, description=s.description, days=s.days) for s in SCENARIOS.values()]


@router.post("/simulation/run", response_model=SimulationRunOut, status_code=201, tags=["simulation"])
@doctor_only
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
