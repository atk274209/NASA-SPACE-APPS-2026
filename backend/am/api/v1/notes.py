"""AM's notes about what crew members said, and values they stated in conversation (Module L).

Readable by the astronaut the notes are about and by the doctor (the authorization guard scopes
every route to the astronaut record it names).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.auth.guard import CurrentUser, register_owner
from am.db.models_m6 import AiNote
from am.db.models_m6b import MeasurementProposal
from am.extract import service as extract

router = APIRouter(tags=["notes"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]
register_owner("mproposal_id", MeasurementProposal)


class NoteOut(BaseModel):
    id: int
    astronaut_id: int
    message_id: int | None
    conversation_id: int | None
    category: str
    text: str
    quote: str
    detector: str
    status: str
    is_simulated: bool
    noted_at: datetime


@router.get("/notes", response_model=list[NoteOut])
def list_notes(db: DB, astronaut_id: int, limit: int = 200):
    rows = db.scalars(select(AiNote).where(AiNote.astronaut_id == astronaut_id, AiNote.status == "active")
                      .order_by(AiNote.id.desc()).limit(max(1, min(limit, 500))))
    return [NoteOut.model_validate(r, from_attributes=True) for r in rows]


class ValueProposalOut(BaseModel):
    id: int
    astronaut_id: int
    message_id: int
    metric_code: str
    value: float
    unit: str
    context: str | None
    allowed_contexts: list[str]
    quote: str
    status: str
    measurement_id: int | None
    created_at: datetime


def _out(p: MeasurementProposal) -> ValueProposalOut:
    from am.health.metrics import get_spec

    spec = get_spec(p.metric_code)
    return ValueProposalOut(id=p.id, astronaut_id=p.astronaut_id, message_id=p.message_id, metric_code=p.metric_code,
                            value=p.value, unit=p.unit, context=p.context,
                            allowed_contexts=list(spec.allowed_contexts) if spec else [], quote=p.quote,
                            status=p.status, measurement_id=p.measurement_id, created_at=p.created_at)


@router.get("/measurement-proposals", response_model=list[ValueProposalOut])
def list_value_proposals(db: DB, astronaut_id: int):
    return [_out(p) for p in extract.pending(db, astronaut_id)]


class ConfirmValueIn(BaseModel):
    context: str | None = None


@router.post("/measurement-proposals/{mproposal_id}/confirm", response_model=ValueProposalOut)
def confirm_value(mproposal_id: int, db: DB, ctx: CTX, principal: CurrentUser, body: ConfirmValueIn | None = None):
    try:
        p, _ = extract.confirm(db, ctx, mproposal_id, context=body.context if body else None, actor=principal.username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except extract.ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _out(p)


@router.post("/measurement-proposals/{mproposal_id}/dismiss", response_model=ValueProposalOut)
def dismiss_value(mproposal_id: int, db: DB, principal: CurrentUser):
    try:
        p = extract.dismiss(db, mproposal_id, actor=principal.username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except extract.ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _out(p)
