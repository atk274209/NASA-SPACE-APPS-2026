"""Append-only audit trail helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from am.db.models import AuditLog


def audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: Any = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        payload=payload or {},
    )
    session.add(row)
    return row
