"""Daily self-report check-ins and deterministic, context-aware prompts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.db.base import utcnow
from am.db.enums import AlertStatus, BaselineStatus
from am.db.models import Alert, Astronaut, BaselineProfile
from am.db.models_ai import SelfReport

# Anchors shown in the UI. Simple 1..5 self-ratings; NOT validated clinical instruments.
CHECKIN_ITEMS: dict[str, tuple[str, str, str]] = {
    "mood": ("Mood", "very low", "very good"),
    "stress": ("Stress", "none", "very high"),
    "fatigue": ("Fatigue", "none", "severe"),
    "sleep_quality": ("Sleep quality", "very poor", "very good"),
    "motivation": ("Motivation", "very low", "very high"),
}


def submit_checkin(session: Session, astronaut_id: int, ratings: dict[str, int], note: str = "") -> list[SelfReport]:
    a = session.get(Astronaut, astronaut_id)
    if a is None:
        raise LookupError("astronaut not found")
    unknown = set(ratings) - set(CHECKIN_ITEMS)
    if unknown:
        raise ValueError(f"unknown check-in items: {sorted(unknown)}")
    if not ratings and not note.strip():
        raise ValueError("check-in is empty")
    for k, v in ratings.items():
        if not isinstance(v, int) or not 1 <= v <= 5:
            raise ValueError(f"{k} must be an integer from 1 to 5")
    cid = str(uuid.uuid4())
    now = utcnow()
    rows = [SelfReport(astronaut_id=astronaut_id, checkin_id=cid, kind=k, score=float(v), note="",
                       is_simulated=False, recorded_at=now) for k, v in ratings.items()]
    if note.strip():
        rows.append(SelfReport(astronaut_id=astronaut_id, checkin_id=cid, kind="note", score=0.0,
                               note=note.strip()[:1000], recorded_at=now))
    session.add_all(rows)
    session.flush()
    parts = [f"{CHECKIN_ITEMS[k][0].lower()} {v}/5" for k, v in ratings.items()]
    text = "Check-in: " + ", ".join(parts) if parts else "Check-in note"
    if note.strip():
        text += f'. Note: "{note.strip()[:300]}"'
    MemoryService(session).add(astronaut_id, layer="short", kind="checkin", content=text, source="astronaut",
                               refs=[{"type": "self_report", "id": r.id} for r in rows])
    return rows


@dataclass
class Suggestion:
    text: str
    reason: str


def suggestions(session: Session, astronaut_id: int) -> list[Suggestion]:
    """Questions AMC proposes, derived from stored state (no LLM involved)."""
    out: list[Suggestion] = []
    alerts = session.scalars(select(Alert).where(Alert.astronaut_id == astronaut_id,
                                                 Alert.status != AlertStatus.RESOLVED.value)
                             .order_by(Alert.last_seen_at.desc())).all()
    for a in alerts[:2]:
        metric = a.metric_code.replace("_", " ")
        out.append(Suggestion(f"What is behind the open {metric} alert ({a.context})?",
                              f"Alert {a.id} is {a.status} with peak severity {a.peak_severity}."))
    frozen = session.scalars(select(BaselineProfile).where(
        BaselineProfile.astronaut_id == astronaut_id, BaselineProfile.is_frozen.is_(True),
        BaselineProfile.status != BaselineStatus.SUPERSEDED.value)).first()
    if frozen:
        out.append(Suggestion(f"Why is my {frozen.metric_code.replace('_', ' ')} baseline ({frozen.context}) frozen?",
                              "A baseline stopped adapting and is waiting for review."))
    recent = session.scalars(select(SelfReport).where(
        SelfReport.astronaut_id == astronaut_id, SelfReport.recorded_at >= utcnow() - timedelta(hours=24))).first()
    if recent is None:
        out.append(Suggestion("How did my sleep and recovery look over the last few days?",
                              "No check-in in the last 24 hours."))
    out.append(Suggestion("Summarize my health status today.", "Daily overview."))
    return out[:4]
