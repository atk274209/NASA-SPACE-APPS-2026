"""Intercom routes. Every user sees only their own channels: the crew loop and their private loop
(the doctor: every astronaut's private loop)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from am.api.deps import get_db
from am.auth.guard import CurrentUser, Principal, signed_in
from am.intercom import service as ic

router = APIRouter(tags=["intercom"])
DB = Annotated[Session, Depends(get_db)]


class ChannelOut(BaseModel):
    id: str
    title: str
    kind: str
    astronaut_id: int | None
    unread: int
    last_body: str | None
    last_sender: str | None
    last_at: datetime | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    channel: str
    sender_user_id: int | None
    sender_name: str
    sender_role: str
    body: str
    urgent: bool
    created_at: datetime


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=ic.MAX_BODY)
    urgent: bool = False


def _who(p: Principal) -> dict:
    return {"is_doctor": p.is_doctor, "astronaut_id": p.astronaut_id}


def _channel(db: Session, p: Principal, channel: str) -> ic.Channel:
    try:
        return ic.check(db, channel, **_who(p))
    except ic.ChannelDenied as exc:
        raise HTTPException(404, "Channel not found.") from exc


@router.get("/intercom/channels", response_model=list[ChannelOut])
@signed_in
def list_channels(db: DB, principal: CurrentUser) -> list[ChannelOut]:
    out = []
    for c in ic.channels_for(db, **_who(principal)):
        last = ic.last_message(db, c.id)
        out.append(ChannelOut(id=c.id, title=c.title, kind=c.kind, astronaut_id=c.astronaut_id,
                              unread=ic.unread(db, principal.user_id, c.id),
                              last_body=last.body[:120] if last else None, last_sender=last.sender_name if last else None,
                              last_at=last.created_at if last else None))
    return out


@router.get("/intercom/channels/{channel}/messages", response_model=list[MessageOut])
@signed_in
def read_channel(channel: str, db: DB, principal: CurrentUser, after_id: int = 0) -> list[MessageOut]:
    _channel(db, principal, channel)
    rows = ic.messages(db, channel, after_id)
    if rows:
        ic.mark_read(db, principal.user_id, channel, rows[-1].id)
        db.commit()
    return [MessageOut.model_validate(m) for m in rows]


@router.post("/intercom/channels/{channel}/messages", response_model=MessageOut, status_code=201)
@signed_in
def send(channel: str, body: PostIn, db: DB, principal: CurrentUser) -> MessageOut:
    _channel(db, principal, channel)
    try:
        m = ic.post(db, channel, body.body, sender_user_id=principal.user_id, sender_name=principal.display_name,
                    sender_role="doctor" if principal.is_doctor else "astronaut", urgent=body.urgent)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return MessageOut.model_validate(m)
