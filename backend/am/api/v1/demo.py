"""SIMULATED demo space events: list (crew and doctor), trigger and pause (doctor, test mode only)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.auth.guard import CurrentUser, doctor_only, signed_in
from am.db.models import Astronaut
from am.db.models_demo import SpaceEvent
from am.demo.events import KINDS, resolve_due, trigger

router = APIRouter(tags=["demo"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    title: str
    summary: str
    severity: str
    astronaut_id: int | None
    astronaut_name: str | None = None
    status: str
    reactions: list[str]
    refs: list[dict[str, Any]]
    started_at: datetime
    ends_at: datetime | None
    resolved_at: datetime | None
    triggered_by: str


class EventsOut(BaseModel):
    available: bool
    auto: bool
    interval_s: float
    events: list[EventOut]


class TriggerIn(BaseModel):
    kind: str | None = None


class AutoIn(BaseModel):
    enabled: bool


def _out(db: Session, ev: SpaceEvent) -> EventOut:
    o = EventOut.model_validate(ev)
    if ev.astronaut_id:
        a = db.get(Astronaut, ev.astronaut_id)
        o.astronaut_name = a.display_name if a else None
    return o


def _require_test_mode(ctx: AppContext) -> None:
    if not ctx.settings.test_mode:
        raise HTTPException(404, "Demo events run only in test mode.")


@router.get("/demo/events", response_model=EventsOut)
@signed_in
def list_events(request: Request, db: DB, ctx: CTX, principal: CurrentUser, limit: int = 20) -> EventsOut:
    stmt = select(SpaceEvent).order_by(SpaceEvent.id.desc()).limit(min(max(limit, 1), 100))
    if not principal.is_doctor:  # a crew member sees crew-wide events and their own
        stmt = stmt.where(or_(SpaceEvent.astronaut_id.is_(None), SpaceEvent.astronaut_id == principal.astronaut_id))
    director = getattr(request.app.state, "demo", None)
    return EventsOut(available=ctx.settings.test_mode, auto=bool(director and director.enabled),
                     interval_s=ctx.settings.demo_event_interval_s,
                     events=[_out(db, e) for e in db.scalars(stmt)])


@router.get("/demo/kinds", response_model=list[dict[str, str]])
@doctor_only
def list_kinds() -> list[dict[str, str]]:
    return [{"code": k.code, "title": k.title} for k in KINDS]


@router.post("/demo/events", response_model=EventOut, status_code=201)
@doctor_only
def trigger_event(body: TriggerIn, db: DB, ctx: CTX, principal: CurrentUser) -> EventOut:
    _require_test_mode(ctx)
    resolve_due(db)
    try:
        ev = trigger(db, ctx, body.kind, actor=f"doctor:{principal.username}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _out(db, ev)


@router.post("/demo/auto", response_model=dict)
@doctor_only
def set_auto(body: AutoIn, request: Request, ctx: CTX) -> dict:
    _require_test_mode(ctx)
    director = getattr(request.app.state, "demo", None)
    if director is None:
        raise HTTPException(409, "Automatic demo events are switched off in the settings (demo_events).")
    director.enabled = body.enabled
    return {"auto": director.enabled}
