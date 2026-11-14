"""Symptom reports and chat-detected symptom proposals.

Safety properties:
  * AM never records a symptom from chat by itself. Detection (keyword or LLM) only creates a
    pending SymptomProposal; a SymptomReport exists only after the astronaut confirms the
    proposal or reports the symptom manually.
  * Severity is the astronaut's own rating (mild | moderate | severe). Nothing here
    classifies, diagnoses or suggests treatment.
  * Every report, resolution and proposal decision is written to the audit log. Confirmed and
    manual reports also become a short-term memory note, written by this deterministic code
    and never by the language model.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.ai.ollama import LLMUnavailable, OllamaClient
from am.audit import audit
from am.db.base import to_naive_utc, utcnow
from am.db.models import Astronaut
from am.db.models_ai import Message
from am.db.models_m5 import SymptomProposal, SymptomReport
from am.symptoms import catalog
from am.symptoms.detect import Detection, detect_keywords, detect_with_llm

log = logging.getLogger(__name__)

# Engineering tolerance for clocks that differ between devices; not a clinical value.
ONSET_CLOCK_SKEW = timedelta(minutes=5)
MAX_NOTE_CHARS = 1000
MEMORY_NOTE_CHARS = 200  # memory notes stay short; the full note lives on the report
REPORT_STATUSES = ("active", "resolved")
PROPOSAL_STATUSES = ("pending", "confirmed", "dismissed")
SOURCES = ("manual", "chat_confirmed", "checkin", "simulated")


class DecisionConflict(Exception):
    """The record's state no longer allows this action (already resolved, confirmed or dismissed)."""


def symptom_label(code: str) -> str:
    sym = catalog.get(code)
    return sym.label if sym else code


class SymptomService:
    def __init__(self, session: Session):
        self.s = session
        self.memory = MemoryService(session)

    def _astronaut(self, astronaut_id: int) -> Astronaut:
        a = self.s.get(Astronaut, astronaut_id)
        if a is None:
            raise LookupError("astronaut not found")
        return a

    @staticmethod
    def _actor(a: Astronaut) -> str:
        # Phase 1 has no sign-in: actions come from the astronaut's own companion screen.
        return f"astronaut:{a.code}"

    # ------------------------------------------------------------------ reports
    def report(self, astronaut_id: int, symptom_code: str, severity: str, *, note: str = "",
               onset_at: datetime | None = None, source: str = "manual",
               proposal_id: int | None = None) -> SymptomReport:
        a = self._astronaut(astronaut_id)
        sym = catalog.get(symptom_code)
        if sym is None:
            raise ValueError(f"unknown symptom code '{symptom_code}'")
        if severity not in catalog.SEVERITIES:
            raise ValueError("severity must be one of: mild, moderate, severe")
        if source not in SOURCES:
            raise ValueError(f"unknown report source '{source}'")
        now = utcnow()
        onset = to_naive_utc(onset_at) if onset_at is not None else None
        if onset is not None and onset > now + ONSET_CLOCK_SKEW:
            raise ValueError("onset_at cannot be in the future")
        note = note.strip()[:MAX_NOTE_CHARS]
        r = SymptomReport(astronaut_id=a.id, symptom_code=sym.code, severity=severity, note=note, onset_at=onset,
                          reported_at=now, source=source, proposal_id=proposal_id, status="active",
                          is_simulated=False)
        self.s.add(r)
        self.s.flush()
        audit(self.s, actor=self._actor(a), action="symptom.reported", entity="symptom_report", entity_id=r.id,
              payload={"astronaut_id": a.id, "symptom_code": sym.code, "severity": severity, "source": source,
                       "proposal_id": proposal_id, "onset_at": onset.isoformat() if onset else None})
        how = "confirmed from chat" if source == "chat_confirmed" else "reported"
        text = f"Symptom {how}: {sym.label.lower()}, self-rated {severity}."
        if note:
            text += f' Note: "{note[:MEMORY_NOTE_CHARS]}"'
        self.memory.add(a.id, layer="short", kind="symptom", content=text, source="astronaut",
                        refs=[{"type": "symptom_report", "id": r.id}], occurred_at=now)
        return r

    def resolve(self, report_id: int, note: str = "") -> SymptomReport:
        r = self.s.get(SymptomReport, report_id)
        if r is None:
            raise LookupError("symptom report not found")
        if r.status != "active":
            raise DecisionConflict("this symptom is already resolved")
        a = self._astronaut(r.astronaut_id)
        now = utcnow()
        r.status, r.resolved_at = "resolved", now
        audit(self.s, actor=self._actor(a), action="symptom.resolved", entity="symptom_report", entity_id=r.id,
              payload={"astronaut_id": a.id, "symptom_code": r.symptom_code,
                       "note": note.strip()[:MAX_NOTE_CHARS]})
        self.memory.add(a.id, layer="short", kind="symptom", source="astronaut", occurred_at=now,
                        content=f"Symptom marked resolved: {symptom_label(r.symptom_code).lower()}.",
                        refs=[{"type": "symptom_report", "id": r.id}])
        self.s.flush()
        return r

    def reports(self, astronaut_id: int, status: str | None = None) -> list[SymptomReport]:
        if status is not None and status not in REPORT_STATUSES:
            raise ValueError(f"status must be one of {list(REPORT_STATUSES)}")
        stmt = select(SymptomReport).where(SymptomReport.astronaut_id == astronaut_id)
        if status:
            stmt = stmt.where(SymptomReport.status == status)
        return list(self.s.scalars(stmt.order_by(SymptomReport.reported_at.desc(), SymptomReport.id.desc())
                                   .limit(500)))

    def active(self, astronaut_id: int) -> list[SymptomReport]:
        return self.reports(astronaut_id, "active")

    # ------------------------------------------------------------------ proposals
    def proposals(self, astronaut_id: int, status: str = "pending") -> list[SymptomProposal]:
        if status != "all" and status not in PROPOSAL_STATUSES:
            raise ValueError(f"status must be 'all' or one of {list(PROPOSAL_STATUSES)}")
        stmt = select(SymptomProposal).where(SymptomProposal.astronaut_id == astronaut_id)
        if status != "all":
            stmt = stmt.where(SymptomProposal.status == status)
        return list(self.s.scalars(stmt.order_by(SymptomProposal.id.desc()).limit(200)))

    def propose(self, astronaut_id: int, message: Message, detections: Iterable[Detection],
                detector: str) -> list[SymptomProposal]:
        """Store new pending proposals for one user message.

        Skipped, because none of them is new information and each would stack a duplicate
        confirmation: a code this message already produced (de-duplication by message and
        code), a code already waiting for the astronaut's decision, and a code that is already
        an active report. A quote that is not an exact substring of the message is never stored.
        """
        taken = set(self.s.scalars(select(SymptomProposal.symptom_code).where(
            SymptomProposal.message_id == message.id)))
        taken |= set(self.s.scalars(select(SymptomProposal.symptom_code).where(
            SymptomProposal.astronaut_id == astronaut_id, SymptomProposal.status == "pending")))
        taken |= set(self.s.scalars(select(SymptomReport.symptom_code).where(
            SymptomReport.astronaut_id == astronaut_id, SymptomReport.status == "active")))
        out: list[SymptomProposal] = []
        for d in detections:
            if d.code in taken or catalog.get(d.code) is None or not d.quote or d.quote not in message.content:
                continue
            p = SymptomProposal(astronaut_id=astronaut_id, message_id=message.id, symptom_code=d.code,
                                quote=d.quote, detector=detector, status="pending")
            self.s.add(p)
            out.append(p)
            taken.add(d.code)
        self.s.flush()
        return out

    def detect_keywords(self, astronaut_id: int, message: Message) -> list[SymptomProposal]:
        return self.propose(astronaut_id, message, detect_keywords(message.content), "keyword")

    def detect_llm(self, astronaut_id: int, message: Message, llm: OllamaClient, model: str) -> list[SymptomProposal]:
        """Optional model-based detection. Any failure means "no extra proposals", never a failed turn."""
        try:
            found = detect_with_llm(llm, model, message.content)
        except LLMUnavailable as exc:
            log.info("LLM symptom detection skipped: %s", exc.reason)
            return []
        except Exception:  # optional detection must never lose the astronaut's message or AM's reply
            log.warning("LLM symptom detection failed", exc_info=True)
            return []
        return self.propose(astronaut_id, message, found, "llm")

    def _pending(self, proposal_id: int) -> SymptomProposal:
        p = self.s.get(SymptomProposal, proposal_id)
        if p is None:
            raise LookupError("symptom proposal not found")
        if p.status != "pending":
            raise DecisionConflict(f"this proposal was already {p.status}")
        return p

    def confirm(self, proposal_id: int, severity: str, note: str = "") -> tuple[SymptomProposal, SymptomReport]:
        p = self._pending(proposal_id)
        r = self.report(p.astronaut_id, p.symptom_code, severity, note=note, source="chat_confirmed",
                        proposal_id=p.id)
        p.status, p.decided_at = "confirmed", utcnow()
        audit(self.s, actor=self._actor(self._astronaut(p.astronaut_id)), action="symptom_proposal.confirmed",
              entity="symptom_proposal", entity_id=p.id,
              payload={"report_id": r.id, "symptom_code": p.symptom_code, "severity": severity,
                       "detector": p.detector})
        self.s.flush()
        return p, r

    def dismiss(self, proposal_id: int) -> SymptomProposal:
        p = self._pending(proposal_id)
        p.status, p.decided_at = "dismissed", utcnow()
        audit(self.s, actor=self._actor(self._astronaut(p.astronaut_id)), action="symptom_proposal.dismissed",
              entity="symptom_proposal", entity_id=p.id,
              payload={"symptom_code": p.symptom_code, "detector": p.detector})
        self.s.flush()
        return p
