"""AM Core seal: open only in an emergency (doctor, logged reason, time-limited) or after mission return."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.audit import audit
from am.db.base import utcnow
from am.db.models_amcore import AmUnseal
from am.db.models_m5 import AppSetting

EMERGENCY_WINDOW = timedelta(hours=2)
RETURN_KEY = "mission_returned_at"
MIN_REASON = 10


class SealError(Exception):
    """Refused; safe to show."""


def mission_returned_at(s: Session) -> datetime | None:
    row = s.get(AppSetting, RETURN_KEY)
    return datetime.fromisoformat(row.value) if row is not None and row.value else None


def state(s: Session) -> dict[str, Any]:
    returned = mission_returned_at(s)
    now = utcnow()
    active = s.scalars(select(AmUnseal).where(AmUnseal.kind == "emergency", AmUnseal.expires_at > now)
                       .order_by(AmUnseal.id.desc())).first()
    history = s.scalars(select(AmUnseal).order_by(AmUnseal.id.desc()).limit(20)).all()
    if returned is not None:
        open_, why, until = True, "mission_return", None
    elif active is not None:
        open_, why, until = True, "emergency", active.expires_at
    else:
        open_, why, until = False, None, None
    return {"open": open_, "open_reason": why, "open_until": until, "mission_returned_at": returned,
            "unseals": [{"id": u.id, "kind": u.kind, "actor": u.actor, "reason": u.reason, "opened_at": u.opened_at,
                         "expires_at": u.expires_at} for u in history]}


def unseal_emergency(s: Session, actor: str, reason: str) -> AmUnseal:
    reason = reason.strip()
    if len(reason) < MIN_REASON:
        raise SealError(f"breaking the seal needs a reason of at least {MIN_REASON} characters")
    u = AmUnseal(kind="emergency", actor=actor, reason=reason, opened_at=utcnow(),
                 expires_at=utcnow() + EMERGENCY_WINDOW)
    s.add(u)
    s.flush()
    audit(s, actor=actor, action="amcore.unsealed", entity="am_unseal", entity_id=u.id,
          payload={"kind": "emergency", "reason": reason[:500], "expires_at": u.expires_at.isoformat()})
    return u


def mark_mission_returned(s: Session, actor: str) -> datetime:
    now = utcnow()
    row = s.get(AppSetting, RETURN_KEY)
    if row is None:
        s.add(AppSetting(key=RETURN_KEY, value=now.isoformat(), updated_by=actor))
    else:
        row.value, row.updated_by = now.isoformat(), actor
    s.add(AmUnseal(kind="mission_return", actor=actor, reason="mission returned", opened_at=now, expires_at=None))
    s.flush()
    audit(s, actor=actor, action="amcore.mission_returned", entity="am_unseal", entity_id=None,
          payload={"at": now.isoformat()})
    return now


def crew_notices(s: Session) -> list[dict[str, Any]]:
    """What the crew is told: that the sealed log was opened, when, why and by whom (never its content)."""
    rows = s.scalars(select(AmUnseal).order_by(AmUnseal.id.desc()).limit(5)).all()
    return [{"kind": u.kind, "actor": u.actor, "reason": u.reason, "opened_at": u.opened_at} for u in rows]
