"""Cabin comfort control (Module P): astronaut comfort strip, doctor Environment screen, manual override.

Authorization: a control or action is owned by the astronaut whose quarters it belongs to; cabin-wide
controls (pressure) have no astronaut owner, so only the doctor can reach them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.api.deps import get_db
from am.auth import guard
from am.auth.guard import CurrentUser, doctor_only, register_owner
from am.cabin import controller as cab
from am.cabin.envelope import load_envelope
from am.db.base import utcnow
from am.db.models_cabin import CabinZone, ComfortSignal, EnvAction, EnvAlert, EnvControl

router = APIRouter(tags=["cabin"])
DB = Annotated[Session, Depends(get_db)]


def _control_owner(session: Session, control_id: int) -> int | None:
    c = session.get(EnvControl, control_id)
    if c is None:
        return None
    z = session.get(CabinZone, c.zone_id)
    return z.astronaut_id if z else None


guard.OWNER_PARAMS["control_id"] = _control_owner
register_owner("action_id", EnvAction)
register_owner("env_alert_id", EnvAlert)


class ControlOut(BaseModel):
    id: int
    zone_id: int
    zone_code: str
    zone_name: str
    astronaut_id: int | None
    variable: str
    unit: str
    mode: str
    mode_reason: str
    setpoint: float
    reading: float | None
    read_at: datetime | None
    fault: str
    drift: float
    min: float
    max: float
    auto_min: float
    auto_max: float
    step: float


class ActionOut(BaseModel):
    id: int
    zone_id: int
    zone_code: str
    astronaut_id: int | None
    variable: str
    kind: str
    before: float | None
    after: float | None
    rule_id: str
    actor: str
    reason: str
    verified: bool | None
    feedback: str | None
    signal_quote: str | None
    reverted: bool
    created_at: datetime


class AlertOut(BaseModel):
    id: int
    zone_code: str
    astronaut_id: int | None
    variable: str
    severity: str
    rule_id: str
    text: str
    status: str
    acknowledged_by: str | None
    created_at: datetime


class CabinStateOut(BaseModel):
    label: str
    controls: list[ControlOut]
    actions: list[ActionOut]
    alerts: list[AlertOut]


def _controls(db: Session, rows: list[EnvControl]) -> list[ControlOut]:
    env = load_envelope()
    zones = {z.id: z for z in db.scalars(select(CabinZone))}
    out = []
    for c in rows:
        lim, z = env.variables[c.variable], zones[c.zone_id]
        out.append(ControlOut(id=c.id, zone_id=z.id, zone_code=z.code, zone_name=z.name, astronaut_id=z.astronaut_id,
                              variable=c.variable, unit=lim.unit, mode=c.mode, mode_reason=c.mode_reason or "",
                              setpoint=c.setpoint, reading=c.last_reading, read_at=c.last_read_at, fault=c.fault,
                              drift=c.drift or 0.0, min=lim.min, max=lim.max, auto_min=lim.auto_min,
                              auto_max=lim.auto_max, step=lim.step))
    return out


def _actions(db: Session, rows: list[EnvAction]) -> list[ActionOut]:
    zones = {z.id: z for z in db.scalars(select(CabinZone))}
    reverted = set(db.scalars(select(EnvAction.reverts_id).where(EnvAction.reverts_id.is_not(None))))
    sig = {s.id: s.quote for s in db.scalars(select(ComfortSignal).where(
        ComfortSignal.id.in_([r.signal_id for r in rows if r.signal_id])))}
    return [ActionOut(id=a.id, zone_id=a.zone_id, zone_code=zones[a.zone_id].code, astronaut_id=a.astronaut_id,
                      variable=a.variable, kind=a.kind, before=a.before, after=a.after, rule_id=a.rule_id, actor=a.actor,
                      reason=a.reason, verified=a.verified, feedback=a.feedback,
                      signal_quote=sig.get(a.signal_id) if a.signal_id else None, reverted=a.id in reverted,
                      created_at=a.created_at) for a in rows]


def _alerts(db: Session, rows: list[EnvAlert]) -> list[AlertOut]:
    zones = {z.id: z for z in db.scalars(select(CabinZone))}
    return [AlertOut(id=a.id, zone_code=zones[a.zone_id].code, astronaut_id=a.astronaut_id, variable=a.variable,
                     severity=a.severity, rule_id=a.rule_id, text=a.text, status=a.status,
                     acknowledged_by=a.acknowledged_by, created_at=a.created_at) for a in rows]


@router.get("/cabin/state", response_model=CabinStateOut)
def astronaut_state(db: DB, astronaut_id: int):
    """The astronaut's quarters (temperature, lighting) and the shared cabin pressure (read-only for them)."""
    cab.ensure_zones(db)
    db.commit()
    zone_ids = [z.id for z in db.scalars(select(CabinZone).where(
        (CabinZone.astronaut_id == astronaut_id) | (CabinZone.code == cab.CABIN_CODE)))]
    controls = db.scalars(select(EnvControl).where(EnvControl.zone_id.in_(zone_ids)).order_by(EnvControl.id)).all()
    own = [z for z in zone_ids]
    actions = db.scalars(select(EnvAction).where(EnvAction.astronaut_id == astronaut_id)
                         .order_by(EnvAction.id.desc()).limit(10)).all()
    alerts = db.scalars(select(EnvAlert).where(EnvAlert.zone_id.in_(own), EnvAlert.status == "open")).all()
    return CabinStateOut(label=load_envelope().label, controls=_controls(db, list(controls)),
                         actions=_actions(db, list(actions)), alerts=_alerts(db, list(alerts)))


@router.get("/cabin/overview", response_model=CabinStateOut)
@doctor_only
def overview(db: DB, limit: int = 100):
    cab.ensure_zones(db)
    db.commit()
    controls = db.scalars(select(EnvControl).order_by(EnvControl.zone_id, EnvControl.variable)).all()
    actions = db.scalars(select(EnvAction).order_by(EnvAction.id.desc()).limit(max(1, min(limit, 500)))).all()
    alerts = db.scalars(select(EnvAlert).order_by(EnvAlert.status.desc(), EnvAlert.id.desc()).limit(100)).all()
    return CabinStateOut(label=load_envelope().label, controls=_controls(db, list(controls)),
                         actions=_actions(db, list(actions)), alerts=_alerts(db, list(alerts)))


class SetIn(BaseModel):
    value: float
    reason: str = Field(default="", max_length=500)


def _serve(db: Session, fn):
    try:
        out = fn()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except cab.CabinError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return out


@router.post("/cabin/controls/{control_id}/set", response_model=ActionOut)
def set_control(control_id: int, body: SetIn, db: DB, principal: CurrentUser):
    c = db.get(EnvControl, control_id)
    if c is None:
        raise HTTPException(404, "Control not found.")
    a = _serve(db, lambda: cab.set_value(db, c, body.value, actor=principal.username, is_doctor=principal.is_doctor,
                                         reason=body.reason))
    return _actions(db, [a])[0]


class ModeIn(BaseModel):
    mode: str
    reason: str = Field(default="", max_length=500)


@router.post("/cabin/controls/{control_id}/mode", response_model=ActionOut)
def set_mode(control_id: int, body: ModeIn, db: DB, principal: CurrentUser):
    c = db.get(EnvControl, control_id)
    if c is None:
        raise HTTPException(404, "Control not found.")
    a = _serve(db, lambda: cab.set_mode(db, c, body.mode, actor=principal.username, reason=body.reason))
    return _actions(db, [a])[0]


@router.post("/cabin/pause-all", response_model=list[ActionOut])
@doctor_only
def pause_all(body: ModeIn, db: DB, principal: CurrentUser):
    """Doctor: pause (or resume) autonomy everywhere at once."""
    acts = _serve(db, lambda: [cab.set_mode(db, c, body.mode, actor=principal.username, reason=body.reason)
                               for c in db.scalars(select(EnvControl)) if c.fault == "none" or body.mode != "AUTONOMOUS"])
    return _actions(db, acts)


@router.post("/cabin/actions/{action_id}/revert", response_model=ActionOut)
def revert_action(action_id: int, db: DB, principal: CurrentUser):
    a = _serve(db, lambda: cab.revert(db, action_id, actor=principal.username))
    return _actions(db, [a])[0]


class FeedbackIn(BaseModel):
    helped: bool


@router.post("/cabin/actions/{action_id}/feedback", response_model=ActionOut)
def action_feedback(action_id: int, body: FeedbackIn, db: DB, principal: CurrentUser):
    a = _serve(db, lambda: cab.feedback(db, action_id, body.helped, actor=principal.username))
    return _actions(db, [a])[0]


@router.post("/cabin/alerts/{env_alert_id}/acknowledge", response_model=AlertOut)
@doctor_only
def acknowledge(env_alert_id: int, db: DB, principal: CurrentUser):
    al = db.get(EnvAlert, env_alert_id)
    if al is None:
        raise HTTPException(404, "Alert not found.")
    if al.status == "open":
        al.status, al.acknowledged_by, al.acknowledged_at = "acknowledged", principal.username, utcnow()
        db.commit()
    return _alerts(db, [al])[0]
