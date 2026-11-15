"""Daily duty roster routes. A crew member sees their own duties; the doctor sees the whole roster."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.auth.guard import CurrentUser, doctor_only, signed_in
from am.db.base import utcnow
from am.db.models import Astronaut
from am.db.models_duty import DutyAssignment, DutyPlan, DutyReadiness
from am.duties.service import LABEL, plan_today, write_note
from am.settings_store import effective_chat_model

router = APIRouter(tags=["duties"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_code: str
    title: str
    detail: str
    kind: str
    load: int
    minutes: int
    reason: str
    status: str


class MemberOut(BaseModel):
    astronaut_id: int
    code: str
    name: str
    energy: float | None
    morale: float | None
    band: str
    capacity: int
    reasons: list[str]
    inputs: dict[str, Any]
    note: str
    note_by: str
    assignments: list[AssignmentOut]


class RosterOut(BaseModel):
    plan_date: date
    created_at: datetime
    created_by: str
    label: str
    postponed: list[dict[str, Any]] = []
    crew: list[MemberOut]


def _roster(db: Session, plan: DutyPlan, only: int | None = None) -> RosterOut:
    crew = []
    stmt = select(DutyReadiness).where(DutyReadiness.plan_id == plan.id).order_by(DutyReadiness.astronaut_id)
    if only is not None:
        stmt = stmt.where(DutyReadiness.astronaut_id == only)
    for r in db.scalars(stmt):
        a = db.get(Astronaut, r.astronaut_id)
        tasks = db.scalars(select(DutyAssignment).where(DutyAssignment.plan_id == plan.id,
                                                        DutyAssignment.astronaut_id == r.astronaut_id)
                           .order_by(DutyAssignment.load.desc(), DutyAssignment.id)).all()
        crew.append(MemberOut(astronaut_id=a.id, code=a.code, name=a.display_name, energy=r.energy, morale=r.morale,
                              band=r.band, capacity=r.capacity, reasons=r.reasons, inputs=r.inputs, note=r.note,
                              note_by=r.note_by, assignments=[AssignmentOut.model_validate(t) for t in tasks]))
    return RosterOut(plan_date=plan.plan_date, created_at=plan.created_at, created_by=plan.created_by, label=LABEL,
                     postponed=(plan.postponed or []) if only is None else [], crew=crew)


@router.get("/duties/today", response_model=RosterOut)
@signed_in
def duties_today(db: DB, ctx: CTX, principal: CurrentUser) -> RosterOut:
    plan = plan_today(db)
    db.commit()
    if principal.is_doctor:
        return _roster(db, plan)
    if principal.astronaut_id is None:
        raise HTTPException(403, "No crew record for this account.")
    r = db.scalar(select(DutyReadiness).where(DutyReadiness.plan_id == plan.id,
                                              DutyReadiness.astronaut_id == principal.astronaut_id))
    if r is not None and r.note_by == "template":  # AM writes the note in their shoes, once, when they look
        write_note(db, r, ctx.llm, effective_chat_model(db, ctx.settings))
        db.commit()
    return _roster(db, plan, only=principal.astronaut_id)


@router.post("/duties/today/replan", response_model=RosterOut)
@doctor_only
def replan(db: DB, principal: CurrentUser) -> RosterOut:
    plan = plan_today(db, actor=f"doctor:{principal.username}", replace=True)
    db.commit()
    return _roster(db, plan)


@router.post("/duties/assignments/{assignment_id}/done", response_model=AssignmentOut)
@signed_in
def mark_done(assignment_id: int, db: DB, principal: CurrentUser) -> AssignmentOut:
    t = db.get(DutyAssignment, assignment_id)
    if t is None or (not principal.is_doctor and t.astronaut_id != principal.astronaut_id):
        raise HTTPException(404, "Duty not found.")
    t.status = "planned" if t.status == "done" else "done"
    t.done_at = utcnow() if t.status == "done" else None
    db.commit()
    return AssignmentOut.model_validate(t)
