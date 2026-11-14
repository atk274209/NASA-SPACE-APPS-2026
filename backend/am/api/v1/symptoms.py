"""Symptom catalog, reports and chat-detected proposals (owner: feature F2).

Every endpoint takes the astronaut explicitly (directly or through the record) so the crew
network can later add an authorization check per astronaut.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.auth.guard import signed_in
from am.api.deps import get_db
from am.db.models_ai import Message
from am.db.models_m5 import SymptomProposal, SymptomReport
from am.symptoms import catalog
from am.symptoms.service import DecisionConflict, SymptomService

router = APIRouter(tags=["symptoms"])
DB = Annotated[Session, Depends(get_db)]
SeverityIn = Literal["mild", "moderate", "severe"]


# ------------------------------------------------------------------ schemas
class CatalogItemOut(BaseModel):
    code: str
    label: str
    category: str
    body_system: str
    body_system_label: str
    report_promptly: bool


class CatalogOut(BaseModel):
    report_promptly_label: str
    severities: list[str]
    symptoms: list[CatalogItemOut]


class SymptomReportIn(BaseModel):
    astronaut_id: int
    symptom_code: str = Field(min_length=1, max_length=48)
    severity: SeverityIn
    note: str = Field(default="", max_length=1000)
    onset_at: datetime | None = None


class ResolveIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class SymptomReportOut(BaseModel):
    id: int
    astronaut_id: int
    symptom_code: str
    label: str
    category: str
    body_system: str
    report_promptly: bool
    severity: str
    note: str
    onset_at: datetime | None
    reported_at: datetime
    source: str
    proposal_id: int | None
    status: str
    resolved_at: datetime | None
    is_simulated: bool


class ProposalOut(BaseModel):
    id: int
    astronaut_id: int
    message_id: int
    conversation_id: int | None
    symptom_code: str
    label: str
    report_promptly: bool
    quote: str
    detector: str
    status: str
    decided_at: datetime | None
    created_at: datetime


class ConfirmIn(BaseModel):
    severity: SeverityIn
    note: str = Field(default="", max_length=1000)


class ConfirmOut(BaseModel):
    proposal: ProposalOut
    report: SymptomReportOut


def report_out(r: SymptomReport) -> SymptomReportOut:
    sym = catalog.get(r.symptom_code)
    return SymptomReportOut(
        id=r.id, astronaut_id=r.astronaut_id, symptom_code=r.symptom_code,
        label=sym.label if sym else r.symptom_code, category=sym.category if sym else "unknown",
        body_system=sym.body_system if sym else "unknown", report_promptly=bool(sym and sym.report_promptly),
        severity=r.severity, note=r.note, onset_at=r.onset_at, reported_at=r.reported_at, source=r.source,
        proposal_id=r.proposal_id, status=r.status, resolved_at=r.resolved_at, is_simulated=r.is_simulated)


def proposal_out(p: SymptomProposal, conversation_id: int | None) -> ProposalOut:
    sym = catalog.get(p.symptom_code)
    return ProposalOut(
        id=p.id, astronaut_id=p.astronaut_id, message_id=p.message_id, conversation_id=conversation_id,
        symptom_code=p.symptom_code, label=sym.label if sym else p.symptom_code,
        report_promptly=bool(sym and sym.report_promptly), quote=p.quote, detector=p.detector, status=p.status,
        decided_at=p.decided_at, created_at=p.created_at)


def _conversation_ids(db: Session, proposals: list[SymptomProposal]) -> dict[int, int]:
    ids = {p.message_id for p in proposals}
    if not ids:
        return {}
    rows = db.execute(select(Message.id, Message.conversation_id).where(Message.id.in_(ids)))
    return {message_id: conversation_id for message_id, conversation_id in rows}


# ------------------------------------------------------------------ catalog
@router.get("/symptoms/catalog", response_model=CatalogOut)
@signed_in
def symptom_catalog():
    return CatalogOut(
        report_promptly_label=catalog.REPORT_PROMPTLY_LABEL,
        severities=list(catalog.SEVERITIES),
        symptoms=[CatalogItemOut(code=s.code, label=s.label, category=s.category, body_system=s.body_system,
                                 body_system_label=catalog.BODY_SYSTEMS[s.body_system],
                                 report_promptly=s.report_promptly) for s in catalog.SYMPTOMS])


# ------------------------------------------------------------------ reports
@router.get("/symptoms", response_model=list[SymptomReportOut])
def list_reports(db: DB, astronaut_id: int, status: str | None = None):
    try:
        rows = SymptomService(db).reports(astronaut_id, status or None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [report_out(r) for r in rows]


@router.post("/symptoms", response_model=SymptomReportOut, status_code=201)
def create_report(body: SymptomReportIn, db: DB):
    try:
        r = SymptomService(db).report(body.astronaut_id, body.symptom_code, body.severity, note=body.note,
                                      onset_at=body.onset_at, source="manual")
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return report_out(r)


@router.post("/symptoms/{report_id}/resolve", response_model=SymptomReportOut)
def resolve_report(report_id: int, body: ResolveIn, db: DB):
    try:
        r = SymptomService(db).resolve(report_id, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return report_out(r)


# ------------------------------------------------------------------ proposals
@router.get("/symptoms/proposals", response_model=list[ProposalOut])
def list_proposals(db: DB, astronaut_id: int, status: str = "pending"):
    try:
        rows = SymptomService(db).proposals(astronaut_id, status)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    conv = _conversation_ids(db, rows)
    return [proposal_out(p, conv.get(p.message_id)) for p in rows]


@router.post("/symptoms/proposals/{proposal_id}/confirm", response_model=ConfirmOut, status_code=201)
def confirm_proposal(proposal_id: int, body: ConfirmIn, db: DB):
    try:
        p, r = SymptomService(db).confirm(proposal_id, body.severity, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return ConfirmOut(proposal=proposal_out(p, _conversation_ids(db, [p]).get(p.message_id)), report=report_out(r))


@router.post("/symptoms/proposals/{proposal_id}/dismiss", response_model=ProposalOut)
def dismiss_proposal(proposal_id: int, db: DB):
    try:
        p = SymptomService(db).dismiss(proposal_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return proposal_out(p, _conversation_ids(db, [p]).get(p.message_id))
