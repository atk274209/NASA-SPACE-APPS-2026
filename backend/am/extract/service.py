"""Conversation capture (Module L): AM keeps the notes and proposes the values the astronaut said.

  * Values: each number the astronaut states becomes a pending MeasurementProposal. One tap confirms
    it into a Measurement through the normal ingest pipeline (validation, baseline, anomaly engine).
    Nothing is recorded without confirmation; a missing activity context is asked for, never guessed.
  * Notes: AM writes an AiNote for each symptom word and each stated value, always with the exact
    quote and a link to the message. Notes never contain a diagnosis. The astronaut and the doctor
    can read them; nobody else.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.audit import audit
from am.db.base import utcnow
from am.db.enums import SourceKind
from am.db.models_ai import Message
from am.db.models_m6 import AiNote
from am.db.models_m6a import DataOrigin
from am.db.models_m6b import MeasurementProposal
from am.extract.values import extract_values
from am.health.ingest import IngestService, MeasurementInput
from am.health.metrics import get_spec
from am.symptoms import catalog
from am.symptoms.detect import detect_keywords

SOURCE_NAME = "AM conversation (CONVERSATION_EXTRACTED)"


class ProposalError(Exception):
    """Refused; the message is safe to show."""


def capture(session: Session, astronaut_id: int, message: Message) -> list[MeasurementProposal]:
    """Value proposals and notes for one user message. Flushes; the caller commits."""
    out: list[MeasurementProposal] = []
    for hit in extract_values(message.content):
        spec = get_spec(hit.metric_code)
        if spec is None or hit.quote not in message.content:
            continue
        p = MeasurementProposal(astronaut_id=astronaut_id, message_id=message.id, metric_code=hit.metric_code,
                                value=hit.value, unit=hit.unit, context=hit.context, quote=hit.quote,
                                detector="pattern", status="pending")
        session.add(p)
        out.append(p)
        session.add(AiNote(astronaut_id=astronaut_id, message_id=message.id, conversation_id=message.conversation_id,
                           category=_category(hit.metric_code), detector="keyword", quote=hit.quote,
                           text=f"Said {spec.display_name.lower()} {hit.value:g} {hit.unit}"
                                + (f" ({hit.context})" if hit.context else "") + "; waiting for confirmation."))
    for d in detect_keywords(message.content):
        sym = catalog.get(d.code)
        if sym is None:
            continue
        session.add(AiNote(astronaut_id=astronaut_id, message_id=message.id, conversation_id=message.conversation_id,
                           category="symptom", detector="keyword", quote=d.quote,
                           text=f"Mentioned {sym.label.lower()}. Not a diagnosis."))
    session.flush()
    return out


def _category(metric: str) -> str:
    return {"sleep_duration": "sleep", "water_intake": "nutrition"}.get(metric, "other")


def _pending(session: Session, proposal_id: int) -> MeasurementProposal:
    p = session.get(MeasurementProposal, proposal_id)
    if p is None:
        raise LookupError("proposal not found")
    if p.status != "pending":
        raise ProposalError(f"this proposal was already {p.status}")
    return p


def confirm(session: Session, ctx, proposal_id: int, *, context: str | None, actor: str):
    """Record the stated value as a Measurement (normal ingest pipeline). Returns (proposal, IngestResult)."""
    p = _pending(session, proposal_id)
    ctx_value = context or p.context
    if not ctx_value:
        raise ProposalError("choose the activity context (for example rest or exercise) before confirming")
    if p.metric_code == "water_intake":  # water goes to the hydration log; End Day stores the daily total once
        from am.nutrition.service import log_water

        log_water(session, p.astronaut_id, p.value, source="conversation", actor=actor)
        p.status, p.context, p.decided_at = "confirmed", "daily", utcnow()
        audit(session, actor=actor, action="measurement_proposal.confirmed", entity="measurement_proposal",
              entity_id=p.id, payload={"hydration_log": True, "message_id": p.message_id})
        session.flush()
        return p, None
    msg = session.get(Message, p.message_id)
    svc = IngestService(session, ctx.thresholds, ctx.policy)
    result = svc.ingest(MeasurementInput(astronaut_id=p.astronaut_id, metric_code=p.metric_code, value=p.value,
                                         unit=p.unit, context=ctx_value, recorded_at=msg.created_at if msg else utcnow(),
                                         source_name=SOURCE_NAME, source_kind=SourceKind.MANUAL))
    result.measurement.data_origin = DataOrigin.MANUAL_TEST_ENTRY.value  # all prototype data is test data
    p.status, p.context, p.measurement_id, p.decided_at = "confirmed", ctx_value, result.measurement.id, utcnow()
    audit(session, actor=actor, action="measurement_proposal.confirmed", entity="measurement_proposal",
          entity_id=p.id, payload={"measurement_id": result.measurement.id, "message_id": p.message_id})
    session.flush()
    return p, result


def dismiss(session: Session, proposal_id: int, *, actor: str) -> MeasurementProposal:
    p = _pending(session, proposal_id)
    p.status, p.decided_at = "dismissed", utcnow()
    audit(session, actor=actor, action="measurement_proposal.dismissed", entity="measurement_proposal",
          entity_id=p.id, payload={"message_id": p.message_id})
    session.flush()
    return p


def pending(session: Session, astronaut_id: int) -> list[MeasurementProposal]:
    return list(session.scalars(select(MeasurementProposal).where(
        MeasurementProposal.astronaut_id == astronaut_id, MeasurementProposal.status == "pending")
        .order_by(MeasurementProposal.id.desc())))
