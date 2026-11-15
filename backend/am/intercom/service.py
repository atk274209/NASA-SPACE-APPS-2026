"""Intercom channels: who may use which, posting, reading, unread counts, and AMC's announcements."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.db.models import Astronaut
from am.db.models_intercom import IntercomMessage, IntercomRead

CREW = "crew"
MAX_BODY = 2000


class ChannelDenied(Exception):
    """This user may not use this channel."""


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    kind: str  # crew | private
    astronaut_id: int | None


def private_id(astronaut_id: int) -> str:
    return f"ast-{astronaut_id}"


def channels_for(s: Session, *, is_doctor: bool, astronaut_id: int | None) -> list[Channel]:
    out = [Channel(CREW, "Crew loop", "crew", None)]
    if is_doctor:
        for a in s.scalars(select(Astronaut).order_by(Astronaut.id)):
            out.append(Channel(private_id(a.id), f"{a.display_name} ({a.code})", "private", a.id))
    elif astronaut_id is not None:
        out.append(Channel(private_id(astronaut_id), "Private loop with the flight surgeon", "private", astronaut_id))
    return out


def check(s: Session, channel: str, *, is_doctor: bool, astronaut_id: int | None) -> Channel:
    for c in channels_for(s, is_doctor=is_doctor, astronaut_id=astronaut_id):
        if c.id == channel:
            return c
    raise ChannelDenied("This channel is not yours.")


def post(s: Session, channel: str, body: str, *, sender_user_id: int | None, sender_name: str, sender_role: str,
         urgent: bool = False) -> IntercomMessage:
    body = body.strip()
    if not body:
        raise ValueError("message is empty")
    m = IntercomMessage(channel=channel, sender_user_id=sender_user_id, sender_name=sender_name[:120],
                        sender_role=sender_role, body=body[:MAX_BODY], urgent=urgent)
    s.add(m)
    s.flush()
    if sender_user_id is not None:
        mark_read(s, sender_user_id, channel, m.id)
    return m


def messages(s: Session, channel: str, after_id: int = 0, limit: int = 200) -> list[IntercomMessage]:
    rows = s.scalars(select(IntercomMessage).where(IntercomMessage.channel == channel, IntercomMessage.id > after_id)
                     .order_by(IntercomMessage.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def mark_read(s: Session, user_id: int, channel: str, last_id: int) -> None:
    r = s.scalar(select(IntercomRead).where(IntercomRead.user_id == user_id, IntercomRead.channel == channel))
    if r is None:
        s.add(IntercomRead(user_id=user_id, channel=channel, last_read_id=last_id))
    elif last_id > r.last_read_id:
        r.last_read_id = last_id
    s.flush()


def unread(s: Session, user_id: int, channel: str) -> int:
    last = s.scalar(select(IntercomRead.last_read_id).where(IntercomRead.user_id == user_id,
                                                            IntercomRead.channel == channel)) or 0
    return s.scalar(select(func.count()).select_from(IntercomMessage).where(
        IntercomMessage.channel == channel, IntercomMessage.id > last,
        (IntercomMessage.sender_user_id.is_(None)) | (IntercomMessage.sender_user_id != user_id))) or 0


def last_message(s: Session, channel: str) -> IntercomMessage | None:
    return s.scalars(select(IntercomMessage).where(IntercomMessage.channel == channel)
                     .order_by(IntercomMessage.id.desc()).limit(1)).first()


def announce(s: Session, body: str, *, urgent: bool = False) -> IntercomMessage:
    """AMC speaks on the crew loop (for example when a simulated space event starts)."""
    return post(s, CREW, body, sender_user_id=None, sender_name="AMC", sender_role="am", urgent=urgent)
