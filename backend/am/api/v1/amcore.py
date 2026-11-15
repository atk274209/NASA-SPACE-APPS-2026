"""AM Core panel. Crew: no access. Doctor: decisions marked visible always; the sealed reports and the
mission log only while the core is open (emergency unseal or mission return)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.amcore import access
from am.api.deps import get_db
from am.auth.guard import CurrentUser, doctor_only
from am.db.models import Astronaut
from am.db.models_amcore import AmDecision, AmReport
from am.db.models_m6b import ReportFlag

router = APIRouter(tags=["am-core"])
DB = Annotated[Session, Depends(get_db)]


def _require_open(db: Session) -> dict[str, Any]:
    st = access.state(db)
    if not st["open"]:
        raise HTTPException(423, "AM Core is sealed. It opens in an emergency (break the seal with a reason) or "
                                 "after mission return.")
    return st


@router.get("/am/core/status", response_model=dict[str, Any])
@doctor_only
def status(db: DB):
    st = access.state(db)
    st["reports"] = db.scalar(select(func.count()).select_from(AmReport)) or 0
    st["last_close_date"] = db.scalar(select(func.max(AmReport.close_date)))
    return st


class UnsealIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/am/core/unseal", response_model=dict[str, Any])
@doctor_only
def unseal(body: UnsealIn, db: DB, principal: CurrentUser):
    try:
        access.unseal_emergency(db, principal.username, body.reason)
    except access.SealError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return access.state(db)


class DecisionOut(BaseModel):
    id: int
    close_date: date
    astronaut_id: int | None
    astronaut_code: str | None
    category: str
    level: str
    decision: str
    rationale: str
    rule_id: str
    visible_to_doctor: bool
    executed: bool
    created_at: datetime


@router.get("/am/core/decisions", response_model=list[DecisionOut])
@doctor_only
def decisions(db: DB, close_date: date | None = None, limit: int = 200):
    """Visible decisions always; every decision while the core is open."""
    opened = access.state(db)["open"]
    stmt = select(AmDecision).order_by(AmDecision.close_date.desc(), AmDecision.id)
    if not opened:
        stmt = stmt.where(AmDecision.visible_to_doctor.is_(True))
    if close_date is not None:
        stmt = stmt.where(AmDecision.close_date == close_date)
    codes = {a.id: a.code for a in db.scalars(select(Astronaut))}
    return [DecisionOut(**{k: getattr(d, k) for k in DecisionOut.model_fields if k != "astronaut_code"},
                        astronaut_code=codes.get(d.astronaut_id)) for d in db.scalars(stmt.limit(max(1, min(limit, 1000))))]


class ReportOut(BaseModel):
    id: int
    kind: str
    astronaut_id: int | None
    astronaut_code: str | None
    close_date: date
    mission_day: int | None
    facts: dict[str, Any]
    assessment: dict[str, Any]
    narrative: str
    narrative_status: str
    narrative_model: str | None
    checks: dict[str, Any]
    is_simulated: bool


def _reports(db: Session, rows) -> list[ReportOut]:
    codes = {a.id: a.code for a in db.scalars(select(Astronaut))}
    return [ReportOut(id=r.id, kind=r.kind, astronaut_id=r.astronaut_id, astronaut_code=codes.get(r.astronaut_id),
                      close_date=r.close_date, mission_day=r.mission_day, facts=r.facts, assessment=r.assessment,
                      narrative=r.narrative, narrative_status=r.narrative_status, narrative_model=r.narrative_model,
                      checks=r.checks or {}, is_simulated=r.is_simulated) for r in rows]


@router.get("/am/core/days", response_model=list[date])
@doctor_only
def days(db: DB):
    _require_open(db)
    return list(db.scalars(select(AmReport.close_date).distinct().order_by(AmReport.close_date.desc())))


@router.get("/am/core/reports", response_model=list[ReportOut])
@doctor_only
def reports(db: DB, close_date: date | None = None, astronaut_id: int | None = None, kind: str | None = None):
    _require_open(db)
    stmt = select(AmReport).order_by(AmReport.close_date.desc(), AmReport.kind, AmReport.astronaut_id)
    if close_date is not None:
        stmt = stmt.where(AmReport.close_date == close_date)
    if astronaut_id is not None:
        stmt = stmt.where(AmReport.astronaut_id == astronaut_id)
    if kind is not None:
        stmt = stmt.where(AmReport.kind == kind)
    return _reports(db, db.scalars(stmt.limit(500)))


@router.get("/am/core/mission-log", response_model=list[ReportOut])
@doctor_only
def mission_log(db: DB):
    """The whole mission as AM told it: one crew chapter per closed day, oldest first."""
    _require_open(db)
    return _reports(db, db.scalars(select(AmReport).where(AmReport.kind == "crew_day").order_by(AmReport.close_date)))


@router.get("/am/core/summary", response_model=dict[str, Any])
@doctor_only
def summary(db: DB):
    """Deterministic mission summary across all sealed reports (for mission return)."""
    _require_open(db)
    rows = db.scalars(select(AmReport).where(AmReport.kind == "astronaut_day")).all()
    per: dict[str, dict[str, Any]] = {}
    codes = {a.id: a.code for a in db.scalars(select(Astronaut))}
    for r in rows:
        code = codes.get(r.astronaut_id, "?")
        p = per.setdefault(code, {"days": 0, "attention": Counter(), "water_target_met": 0, "checked_in": 0,
                                  "declining_days": Counter()})
        p["days"] += 1
        p["attention"][r.assessment.get("attention", "routine")] += 1
        p["water_target_met"] += bool(r.facts.get("health", {}).get("hydration", {}).get("met"))
        p["checked_in"] += bool(r.facts.get("behaviour", {}).get("checked_in"))
        for k in r.assessment.get("declining", []):
            p["declining_days"][k] += 1
    flags = Counter(db.scalars(select(ReportFlag.rule_level)))
    return {"days_logged": len({r.close_date for r in rows}), "crew": per,
            "flags_by_rule_level": dict(flags),
            "decisions": db.scalar(select(func.count()).select_from(AmDecision)) or 0,
            "label": "Deterministic summary of AM Core's sealed daily reports. Not a clinical assessment."}
