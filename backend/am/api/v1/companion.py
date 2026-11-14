"""Companion, memory and check-in routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.checkin import CHECKIN_ITEMS, submit_checkin, suggestions
from am.ai.companion import CompanionService
from am.ai.memory import MemoryService
from am.api.deps import AppContext, get_ctx, get_db
from am.api.v1.symptoms import ProposalOut, proposal_out
from am.db.models_ai import Conversation, Message, SelfReport
from am.settings_store import effective_chat_model, effective_embed_model

router = APIRouter(tags=["companion"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AIStatusOut(BaseModel):
    reachable: bool
    model: str
    model_installed: bool
    installed_models: list[str] = []
    reason: str | None = None


class ConversationIn(BaseModel):
    astronaut_id: int


class ConversationOut(ORM):
    id: int
    astronaut_id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(ORM):
    id: int
    conversation_id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    refs: list[dict[str, Any]]
    model: str | None
    generated_by: str
    latency_ms: int | None
    created_at: datetime


class SendIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class TraceOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool
    ref_count: int


class TurnOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
    trace: list[TraceOut]
    ai_available: bool
    fallback_reason: str | None
    proposals: list[ProposalOut] = []  # symptoms AM noticed in this user message, pending confirmation


class SuggestionOut(BaseModel):
    text: str
    reason: str


class MemoryOut(ORM):
    id: int
    layer: str
    kind: str
    content: str
    importance: int
    source: str
    source_refs: list[dict[str, Any]]
    is_simulated: bool
    occurred_at: datetime
    expires_at: datetime | None


class CheckinItemOut(BaseModel):
    key: str
    label: str
    low_anchor: str
    high_anchor: str


class CheckinIn(BaseModel):
    astronaut_id: int
    ratings: dict[str, int] = {}
    note: str = Field(default="", max_length=1000)


class SelfReportOut(ORM):
    id: int
    checkin_id: str
    kind: str
    score: float
    note: str
    is_simulated: bool
    recorded_at: datetime


def _companion(db: Session, ctx: AppContext) -> CompanionService:
    return CompanionService(db, ctx.llm, effective_chat_model(db, ctx.settings), ctx.thresholds, ctx.policy,
                            embed_model=effective_embed_model(db, ctx.settings))


@router.get("/ai/status", response_model=AIStatusOut)
def ai_status(db: DB, ctx: CTX):
    return AIStatusOut(**ctx.llm.status(effective_chat_model(db, ctx.settings)))


@router.get("/companion/conversations", response_model=list[ConversationOut])
def list_conversations(db: DB, astronaut_id: int):
    return db.scalars(select(Conversation).where(Conversation.astronaut_id == astronaut_id)
                      .order_by(Conversation.updated_at.desc()).limit(50)).all()


@router.post("/companion/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationIn, db: DB, ctx: CTX):
    try:
        c = _companion(db, ctx).create_conversation(body.astronaut_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return c


@router.get("/companion/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: int, db: DB):
    if db.get(Conversation, conversation_id) is None:
        raise HTTPException(404, "conversation not found")
    return db.scalars(select(Message).where(Message.conversation_id == conversation_id,
                                            Message.role.in_(("user", "assistant")))
                      .order_by(Message.id)).all()


@router.post("/companion/conversations/{conversation_id}/messages", response_model=TurnOut, status_code=201)
def send_message(conversation_id: int, body: SendIn, db: DB, ctx: CTX):
    try:
        r = _companion(db, ctx).send(conversation_id, body.content)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return TurnOut(
        user_message=MessageOut.model_validate(r.user_message),
        assistant_message=MessageOut.model_validate(r.assistant_message),
        trace=[TraceOut(**t.__dict__) for t in r.trace],
        ai_available=r.ai_available,
        fallback_reason=r.fallback_reason,
        proposals=[proposal_out(p, conversation_id) for p in r.proposals],
    )


@router.get("/companion/suggestions", response_model=list[SuggestionOut])
def get_suggestions(db: DB, astronaut_id: int):
    return [SuggestionOut(**s.__dict__) for s in suggestions(db, astronaut_id)]


@router.get("/memory", response_model=list[MemoryOut])
def list_memory(db: DB, astronaut_id: int, layer: str | None = None):
    return MemoryService(db).list(astronaut_id, layer, limit=200)


@router.get("/checkins/items", response_model=list[CheckinItemOut])
def checkin_items():
    return [CheckinItemOut(key=k, label=v[0], low_anchor=v[1], high_anchor=v[2]) for k, v in CHECKIN_ITEMS.items()]


@router.post("/checkins", response_model=list[SelfReportOut], status_code=201)
def create_checkin(body: CheckinIn, db: DB):
    try:
        rows = submit_checkin(db, body.astronaut_id, body.ratings, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return rows


@router.get("/checkins", response_model=list[SelfReportOut])
def list_checkins(db: DB, astronaut_id: int, limit: int = 100):
    return db.scalars(select(SelfReport).where(SelfReport.astronaut_id == astronaut_id)
                      .order_by(SelfReport.recorded_at.desc()).limit(min(limit, 500))).all()
