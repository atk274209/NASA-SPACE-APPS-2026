"""Three-layer memory.

* short   : what the astronaut said today (self-reported observations, check-ins);
            expires after SHORT_TTL. Source = astronaut.
* long    : consolidated habits/patterns. Written by the journal module (M4); M3 only
            reads it.
* mission : significant events for the whole mission. Written deterministically from
            alert and baseline events; never from LLM output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.enums import Severity
from am.db.models import Alert, BaselineProfile
from am.db.models_ai import MemoryItem

SHORT_TTL = timedelta(hours=24)
LAYERS = ("short", "long", "mission")


@dataclass
class MemoryContext:
    short: list[MemoryItem]
    long: list[MemoryItem]
    mission: list[MemoryItem]

    def all_items(self) -> list[MemoryItem]:
        return [*self.mission, *self.long, *self.short]


class MemoryService:
    def __init__(self, session: Session):
        self.s = session

    def add(
        self,
        astronaut_id: int,
        *,
        layer: str,
        kind: str,
        content: str,
        source: str,
        importance: int = 1,
        refs: list[dict[str, Any]] | None = None,
        occurred_at: datetime | None = None,
        is_simulated: bool = False,
    ) -> MemoryItem:
        if layer not in LAYERS:
            raise ValueError(f"unknown memory layer '{layer}'")
        now = utcnow()
        item = MemoryItem(
            astronaut_id=astronaut_id,
            layer=layer,
            kind=kind,
            content=content.strip()[:2000],
            importance=max(1, min(3, importance)),
            source=source,
            source_refs=refs or [],
            occurred_at=occurred_at or now,
            is_simulated=is_simulated,
            expires_at=now + SHORT_TTL if layer == "short" else None,
        )
        self.s.add(item)
        self.s.flush()
        return item

    def list(self, astronaut_id: int, layer: str | None = None, limit: int = 100,
             include_expired: bool = False) -> list[MemoryItem]:
        stmt = select(MemoryItem).where(MemoryItem.astronaut_id == astronaut_id)
        if layer:
            stmt = stmt.where(MemoryItem.layer == layer)
        if not include_expired:
            stmt = stmt.where(or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utcnow()))
        return list(self.s.scalars(stmt.order_by(MemoryItem.occurred_at.desc()).limit(limit)))

    def context(self, astronaut_id: int) -> MemoryContext:
        mission = self.list(astronaut_id, "mission", limit=40)
        # keep the most important recent mission events
        mission = sorted(mission, key=lambda m: (m.importance, m.occurred_at), reverse=True)[:12]
        mission.sort(key=lambda m: m.occurred_at)
        return MemoryContext(
            short=list(reversed(self.list(astronaut_id, "short", limit=20))),
            long=list(reversed(self.list(astronaut_id, "long", limit=10))),
            mission=mission,
        )

    # ---------------------------------------------------------- deterministic recorders
    def record_alert_event(self, alert: Alert, event: str, measurement_id: int | None) -> MemoryItem | None:
        """Mission memory for alerts reaching WARNING or above (opened or escalated)."""
        sev = Severity(alert.peak_severity)
        if sev.rank < Severity.WARNING.rank:
            return None
        verb = "opened" if event == "opened" else "escalated to"
        content = (
            f"Alert {verb} {sev.value}: {alert.metric_code.replace('_', ' ')} ({alert.context}), "
            f"{alert.direction} deviation {alert.last_deviation:+.1f} robust z from personal baseline "
            f"(thresholds {alert.threshold_version}, {alert.threshold_label})."
        )
        refs = [{"type": "alert", "id": alert.id}]
        if measurement_id:
            refs.append({"type": "measurement", "id": measurement_id})
        return self.add(alert.astronaut_id, layer="mission", kind="alert", content=content,
                        source="deterministic", importance=3 if sev == Severity.CRITICAL else 2,
                        refs=refs, occurred_at=alert.last_seen_at, is_simulated=alert.is_simulated)

    def record_baseline_freeze(self, profile: BaselineProfile, reason: str, is_simulated: bool,
                               occurred_at: datetime) -> MemoryItem:
        content = (
            f"Baseline for {profile.metric_code.replace('_', ' ')} ({profile.context}) v{profile.version} "
            f"frozen: {reason}."
        )
        return self.add(profile.astronaut_id, layer="mission", kind="baseline", content=content,
                        source="deterministic", importance=2,
                        refs=[{"type": "baseline", "id": profile.id}],
                        occurred_at=occurred_at, is_simulated=is_simulated)
