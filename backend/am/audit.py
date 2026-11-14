"""Append-only audit trail helper.

`actor` is the name a caller gives for the action (a role, a service, or a name typed into a
form). When the action happens inside a signed-in request, the authenticated username is
added to the payload as `auth_user`, so the trail never depends on a self-declared name.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from am.db.models import AuditLog

_audit_user: ContextVar[str | None] = ContextVar("am_audit_user", default=None)


def set_audit_user(username: str | None) -> None:
    """Bind the authenticated username to the current request context (see am.auth.guard)."""
    _audit_user.set(username)


def audit_user() -> str | None:
    return _audit_user.get()


def audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: Any = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    data = dict(payload or {})
    user = _audit_user.get()
    if user is not None:
        data.setdefault("auth_user", user)
    row = AuditLog(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        payload=data,
    )
    session.add(row)
    return row
