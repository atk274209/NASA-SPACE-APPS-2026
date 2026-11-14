"""Adherence streaks (owner: feature F3).

GET /streaks?astronaut_id= returns one row per routine: the current and longest run of
consecutive mission days, the last 30 mission days as a calendar and the adherence
milestones reached. Streaks count whether something was recorded, never its value.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from am.api.deps import get_db
from am.missions.streaks import streak_report, sync_today_tasks

router = APIRouter(tags=["streaks"])
DB = Annotated[Session, Depends(get_db)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StreakDayOut(ORM):
    mission_day: int
    day_date: date
    # done | missed (other records exist that day) | no_data (no records at all) | pending (today, still open)
    state: Literal["done", "missed", "no_data", "pending"]
    simulated: bool


class MilestoneOut(ORM):
    days: int
    label: str
    reached: bool
    reached_day: int | None


class StreakOut(ORM):
    code: str
    title: str
    rule: str
    current: int
    at_risk: bool
    today_done: bool
    longest: int
    longest_days: list[int]
    total_done: int
    next_milestone: int | None
    milestones: list[MilestoneOut]
    calendar: list[StreakDayOut]


class StreaksOut(ORM):
    astronaut_id: int
    mission_id: int
    mission_start: date
    mission_day: int
    today: date
    simulated: bool
    window_days: int
    rules: str
    milestone_note: str
    streaks: list[StreakOut]


@router.get("/streaks", response_model=StreaksOut)
def get_streaks(db: DB, astronaut_id: int):
    # Same data-verified task completion the Missions page applies, so both pages agree.
    sync_today_tasks(db, astronaut_id)
    db.commit()
    try:
        report = streak_report(db, astronaut_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return StreaksOut.model_validate(report)
