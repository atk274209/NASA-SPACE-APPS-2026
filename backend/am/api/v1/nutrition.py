"""Hydration and supplements (astronaut's own records) and the crew hydration leaderboard (opt-in)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from am.api.deps import get_db
from am.auth.guard import CurrentUser, register_owner, signed_in
from am.db.models_m6a import AstronautProfile
from am.db.models_m6b import HydrationLog
from am.nutrition import service as nutri

router = APIRouter(tags=["nutrition"])
DB = Annotated[Session, Depends(get_db)]
register_owner("water_id", HydrationLog)


def _serve(db: Session, fn):
    try:
        out = fn()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except nutri.NutritionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return out


@router.get("/nutrition/today", response_model=dict[str, Any])
def today(db: DB, astronaut_id: int):
    return _serve(db, lambda: nutri.today_status(db, astronaut_id))


class WaterIn(BaseModel):
    astronaut_id: int
    amount_ml: float = Field(gt=0)


@router.post("/nutrition/water", response_model=dict[str, Any], status_code=201)
def add_water(body: WaterIn, db: DB, principal: CurrentUser):
    _serve(db, lambda: nutri.log_water(db, body.astronaut_id, body.amount_ml, source="quick_add",
                                       actor=principal.username))
    return nutri.today_status(db, body.astronaut_id)


@router.post("/nutrition/water/{water_id}/undo", response_model=dict[str, Any])
def undo_water(water_id: int, db: DB, principal: CurrentUser):
    row = db.get(HydrationLog, water_id)
    if row is None:
        raise HTTPException(404, "Entry not found.")
    aid = row.astronaut_id
    _serve(db, lambda: nutri.undo_water(db, water_id, actor=principal.username))
    return nutri.today_status(db, aid)


class SupplementIn(BaseModel):
    astronaut_id: int
    supplement_code: str
    taken: bool = True


@router.post("/nutrition/supplements", response_model=dict[str, Any])
def supplement(body: SupplementIn, db: DB, principal: CurrentUser):
    _serve(db, lambda: nutri.log_supplement(db, body.astronaut_id, body.supplement_code, body.taken,
                                            actor=principal.username))
    return nutri.today_status(db, body.astronaut_id)


@router.get("/nutrition/leaderboard", response_model=dict[str, Any])
@signed_in
def leaderboard(db: DB):
    """Crew hydration adherence (opted-in astronauts only; no amounts)."""
    return nutri.leaderboard(db)


class OptInIn(BaseModel):
    astronaut_id: int
    opt_in: bool


@router.post("/profile/leaderboard", response_model=dict[str, Any])
def set_opt_in(body: OptInIn, db: DB, principal: CurrentUser):
    value = _serve(db, lambda: nutri.set_opt_in(db, body.astronaut_id, body.opt_in, actor=principal.username))
    return {"astronaut_id": body.astronaut_id, "opt_in": value}


@router.get("/profile/leaderboard", response_model=dict[str, Any])
def get_opt_in(db: DB, astronaut_id: int):
    prof = db.get(AstronautProfile, astronaut_id)
    return {"astronaut_id": astronaut_id, "opt_in": True if prof is None else prof.leaderboard_opt_in}
