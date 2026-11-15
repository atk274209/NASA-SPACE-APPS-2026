"""Private diary routes.

Every /diary route serves the signed-in astronaut's own diary only; a doctor gets 403 there.
The doctor's one route (/doctor/diary/{astronaut_id}) answers 423 until the mission has returned.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from am.ai.safety_text import label as tr_label
from am.api.deps import AppContext, get_ctx, get_db
from am.auth.guard import CurrentUser, Principal, doctor_only, signed_in
from am.db.models_diary import DiaryDay, DiaryForward
from am.diary.service import DiarySealed, DiaryService, day_dict
from am.settings_store import effective_chat_model
from am.symptoms import catalog

router = APIRouter(tags=["diary"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class DayOut(BaseModel):
    id: int
    day_date: date
    mission_day: int | None
    level: str
    rule_level: str
    rule_reasons: list[str]
    ai_status: str
    ai_level: str | None
    ai_reason: str
    ai_quote: str
    summary: str
    analysed_at: datetime | None
    is_today: bool


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    generated_by: str
    created_at: datetime


class ForwardOut(BaseModel):
    id: int
    message_id: int
    symptom_code: str
    label: str
    quote: str
    status: str


class PageOut(BaseModel):
    day: DayOut
    messages: list[MessageOut]
    forwards: list[ForwardOut]
    ai_available: bool = True


class WriteIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class DecideIn(BaseModel):
    send: bool
    severity: Literal["mild", "moderate", "severe"] | None = None


def _own(principal: Principal) -> int:
    if principal.is_doctor or principal.astronaut_id is None:
        raise HTTPException(403, "The diary is private to its author.")
    return principal.astronaut_id


def _service(db: Session, ctx: AppContext) -> DiaryService:
    return DiaryService(db, ctx.llm, effective_chat_model(db, ctx.settings))


def _day(db: Session, principal: Principal, day_id: int) -> DiaryDay:
    d = db.get(DiaryDay, day_id)
    if d is None or d.astronaut_id != _own(principal):
        raise HTTPException(404, "Diary page not found.")
    return d


def _forward_out(f: DiaryForward, lang: str = "en") -> ForwardOut:
    sym = catalog.get(f.symptom_code)
    return ForwardOut(id=f.id, message_id=f.message_id, symptom_code=f.symptom_code,
                      label=tr_label(f.symptom_code, sym.label if sym else f.symptom_code, lang),
                      quote=f.quote, status=f.status)


def _page(svc: DiaryService, d: DiaryDay, ai_available: bool = True) -> PageOut:
    return PageOut(day=DayOut(**day_dict(d)),
                   messages=[MessageOut.model_validate(m) for m in svc.messages(d.id)],
                   forwards=[_forward_out(f) for f in svc.forwards(d.id)], ai_available=ai_available)


@router.get("/diary/today", response_model=PageOut)
@signed_in
def diary_today(db: DB, ctx: CTX, principal: CurrentUser) -> PageOut:
    svc = _service(db, ctx)
    d = svc.today(_own(principal))
    db.commit()
    return _page(svc, d)


@router.get("/diary/days", response_model=list[DayOut])
@signed_in
def diary_days(db: DB, ctx: CTX, principal: CurrentUser) -> list[DayOut]:
    return [DayOut(**day_dict(d)) for d in _service(db, ctx).days(_own(principal))]


@router.get("/diary/days/{day_id}", response_model=PageOut)
@signed_in
def diary_page(day_id: int, db: DB, ctx: CTX, principal: CurrentUser) -> PageOut:
    return _page(_service(db, ctx), _day(db, principal, day_id))


@router.post("/diary/days/{day_id}/messages", response_model=PageOut)
@signed_in
def diary_write(day_id: int, body: WriteIn, db: DB, ctx: CTX, principal: CurrentUser) -> PageOut:
    d = _day(db, principal, day_id)
    svc = _service(db, ctx)
    try:
        turn = svc.send(d, body.content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _page(svc, d, turn.ai_available)


@router.post("/diary/days/{day_id}/analyse", response_model=DayOut)
@signed_in
def diary_analyse(day_id: int, db: DB, ctx: CTX, principal: CurrentUser) -> DayOut:
    d = _service(db, ctx).analyse(_day(db, principal, day_id))
    db.commit()
    return DayOut(**day_dict(d))


@router.post("/diary/forwards/{forward_id}", response_model=ForwardOut)
@signed_in
def diary_decide(forward_id: int, body: DecideIn, db: DB, ctx: CTX, principal: CurrentUser) -> ForwardOut:
    f = db.get(DiaryForward, forward_id)
    if f is None or f.astronaut_id != _own(principal):
        raise HTTPException(404, "Not found.")
    try:
        _service(db, ctx).decide(f, body.send, body.severity)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _forward_out(f)


class DoctorDayOut(BaseModel):
    day: DayOut
    messages: list[MessageOut]


@router.get("/doctor/diary/{astronaut_id}", response_model=list[DoctorDayOut])
@doctor_only
def doctor_diary(astronaut_id: int, db: DB, principal: CurrentUser) -> list[DoctorDayOut]:
    try:
        rows = DiaryService(db).doctor_view(astronaut_id, actor=f"doctor:{principal.username}")
    except DiarySealed as exc:
        raise HTTPException(423, str(exc)) from exc
    db.commit()
    return [DoctorDayOut(day=DayOut(**day_dict(d)), messages=[MessageOut.model_validate(m) for m in msgs])
            for d, msgs in rows]



