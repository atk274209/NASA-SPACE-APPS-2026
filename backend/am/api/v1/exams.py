"""Routine examinations and adherence points (Module M). Scoped to the astronaut record named."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from am.api.deps import get_db
from am.auth.guard import CurrentUser, register_owner
from am.db.models_m6b import RoutineExam
from am.exams import service as exams

router = APIRouter(tags=["exams"])
DB = Annotated[Session, Depends(get_db)]
register_owner("exam_id", RoutineExam)


class ExamOut(BaseModel):
    id: int
    astronaut_id: int
    exam_code: str
    title: str
    detail: str
    procedure_ref: str
    due_day: int
    state: str  # upcoming | due | overdue | done
    done_day: int | None
    completed_at: datetime | None
    result_text: str
    entered_by: str | None


class ExamsOut(BaseModel):
    mission_day: int | None
    label: str
    exams: list[ExamOut]


@router.get("/exams", response_model=ExamsOut)
def list_exams(db: DB, astronaut_id: int):
    today, views = exams.exams_for(db, astronaut_id)
    db.commit()  # newly scheduled slots
    return ExamsOut(mission_day=today, label=exams.load_config()["label"], exams=[ExamOut(
        id=v.exam.id, astronaut_id=v.exam.astronaut_id, exam_code=v.exam.exam_code, title=v.title, detail=v.detail,
        procedure_ref=v.procedure_ref, due_day=v.exam.due_day, state=v.state, done_day=v.exam.done_day,
        completed_at=v.exam.completed_at, result_text=v.exam.result_text, entered_by=v.exam.entered_by)
        for v in views])


class ResultIn(BaseModel):
    result_text: str = Field(min_length=1, max_length=4000, description="the result as recorded by the procedure")


@router.post("/exams/{exam_id}/result", response_model=ExamsOut)
def enter_result(exam_id: int, body: ResultIn, db: DB, principal: CurrentUser):
    try:
        x = exams.record_result(db, exam_id, body.result_text, actor=principal.username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except exams.ExamError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return list_exams(db, x.astronaut_id)


@router.get("/points", response_model=dict[str, Any])
def points(db: DB, astronaut_id: int):
    return exams.points_summary(db, astronaut_id)
