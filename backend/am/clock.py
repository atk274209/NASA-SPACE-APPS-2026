"""Mission clock persistence and controlled advancement.

Mission time = real UTC time + an offset. The offset is zero unless the test bench moved
mission time forward (see am.simlive). It is stored in app_setting so a restart continues
on the same mission timeline, and every deliberate jump is written to the audit log.
Security timers (sign-in sessions, rate limits) never use mission time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from am.audit import audit
from am.db.base import clock_offset, real_utcnow, set_clock_offset, utcnow
from am.db.models_m5 import AppSetting

CLOCK_KEY = "mission_clock_offset_seconds"


def load_clock(session: Session) -> timedelta:
    """Apply the persisted offset to this process (called once at start-up)."""
    row = session.get(AppSetting, CLOCK_KEY)
    offset = timedelta(seconds=float(row.value)) if row is not None else timedelta(0)
    set_clock_offset(max(offset, timedelta(0)), allow_backward=True)
    return clock_offset()


def persist_clock(session: Session, actor: str = "am-system") -> None:
    """Store the current offset (flush only; the caller commits)."""
    seconds = clock_offset().total_seconds()
    row = session.get(AppSetting, CLOCK_KEY)
    if row is None:
        session.add(AppSetting(key=CLOCK_KEY, value=seconds, updated_by=actor))
    else:
        row.value = seconds
        row.updated_by = actor
    session.flush()


def advance_clock(session: Session, *, by: timedelta | None = None, to: datetime | None = None,
                  actor: str, reason: str, record_audit: bool = True) -> datetime:
    """Move mission time forward by a duration, or to a mission-time instant. Returns mission now.

    Exactly one of `by` / `to` must be given. Moving backward raises ValueError.
    """
    if (by is None) == (to is None):
        raise ValueError("give exactly one of 'by' or 'to'")
    before = utcnow()
    delta = by if by is not None else (to - before)
    if delta < timedelta(0):
        raise ValueError("the mission clock cannot move backward")
    set_clock_offset(clock_offset() + delta)
    persist_clock(session, actor)
    if record_audit:
        audit(session, actor=actor, action="clock.advanced", entity="mission_clock", entity_id=None,
              payload={"from": before.isoformat(), "to": utcnow().isoformat(), "seconds": delta.total_seconds(),
                       "reason": reason})
    return utcnow()


def clock_state() -> dict[str, Any]:
    offset = clock_offset()
    return {
        "mission_now": utcnow(),
        "real_now": real_utcnow(),
        "offset_seconds": offset.total_seconds(),
        "test_clock": offset > timedelta(seconds=1),
    }
