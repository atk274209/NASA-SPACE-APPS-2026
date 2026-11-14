"""Escalation guidance ('is this serious?'), escalation events, body map (owner: feature F7).

Everything served here is deterministic and DEMO-labelled: levels come from the anomaly
engine and the astronaut's own symptom ratings, and the wording comes from
config/escalation.demo.yaml. No endpoint calls the LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from am.auth.guard import doctor_only, signed_in
from am.api.deps import get_db
from am.guidance import bodymap, escalation
from am.guidance.escalation import EscalationConfigError, EscalationStateError
from am.guidance.schemas import (
    AcknowledgeIn,
    BodyMapOut,
    CurrentGuidanceOut,
    EscalationOut,
    GuidanceLevelsOut,
    GuidanceOut,
)

router = APIRouter(tags=["guidance"])
DB = Annotated[Session, Depends(get_db)]
T = TypeVar("T")


def _serve(fn: Callable[[], T]) -> T:
    """Map service errors to HTTP: unknown record -> 404, bad input -> 422, broken matrix -> 503."""
    try:
        return fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except EscalationConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/guidance/levels", response_model=GuidanceLevelsOut)
@signed_in
def guidance_levels() -> GuidanceLevelsOut:
    return _serve(lambda: escalation.levels_out(escalation.load_matrix()))


@router.get("/guidance/current", response_model=CurrentGuidanceOut)
def guidance_current(db: DB, astronaut_id: int) -> CurrentGuidanceOut:
    """The most serious open item (alert or active symptom) and the guidance for its level."""
    return _serve(lambda: escalation.current_guidance(db, astronaut_id))


@router.get("/guidance/alerts/{alert_id}", response_model=GuidanceOut)
def guidance_for_alert(alert_id: int, db: DB) -> GuidanceOut:
    return _serve(lambda: escalation.guidance_for_alert(db, alert_id))


@router.get("/guidance/symptoms/{symptom_report_id}", response_model=GuidanceOut)
def guidance_for_symptom(symptom_report_id: int, db: DB) -> GuidanceOut:
    return _serve(lambda: escalation.guidance_for_symptom(db, symptom_report_id))


@router.get("/escalations", response_model=list[EscalationOut])
def list_escalations(db: DB, astronaut_id: int, status: str | None = None) -> list[EscalationOut]:
    return _serve(lambda: escalation.events_out(db, escalation.list_events(db, astronaut_id, status=status)))


@router.post("/escalations/{event_id}/acknowledge", response_model=EscalationOut)
@doctor_only
def acknowledge_escalation(event_id: int, body: AcknowledgeIn, db: DB) -> EscalationOut:
    try:
        event = escalation.acknowledge_event(db, event_id, body.actor, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except EscalationStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return escalation.events_out(db, [event])[0]


@router.get("/bodymap", response_model=BodyMapOut)
def body_map(db: DB, astronaut_id: int) -> BodyMapOut:
    return _serve(lambda: bodymap.build_body_map(db, astronaut_id))
