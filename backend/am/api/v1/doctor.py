"""Doctor (Earth-side console): live feed of what astronauts write, with yellow/red triage flags.

The level shown is the higher of the deterministic rule level and a validated AI suggestion
(am.triage). Doctor-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.audit import audit
from am.auth.guard import CurrentUser, doctor_only, register_owner
from am.db.base import utcnow
from am.db.enums import AlertStatus
from am.db.models import Alert, Astronaut, Mission
from am.db.models_ai import Message
from am.db.models_m6a import AstronautProfile
from am.db.models_m6b import LEVELS, ReportFlag, level_rank
from am.exams.service import exams_for

router = APIRouter(tags=["doctor"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]
register_owner("flag_id", ReportFlag)


class FeedItemOut(BaseModel):
    id: int
    astronaut_id: int
    astronaut_code: str
    astronaut_name: str
    source_type: str
    source_id: int
    conversation_id: int | None
    text: str
    is_simulated: bool
    created_at: datetime
    level: str
    rule_level: str
    rule_ids: list[str]
    rule_reasons: list[str]
    ai_status: str
    ai_level: str | None
    ai_reason: str
    ai_quote: str
    ai_model: str | None
    approval_status: str | None
    approval_by: str | None
    approval_at: datetime | None
    approval_note: str
    seen_at: datetime | None
    seen_by: str | None


def _item(f: ReportFlag, a: Astronaut, conversation_id: int | None) -> FeedItemOut:
    return FeedItemOut(
        id=f.id, astronaut_id=f.astronaut_id, astronaut_code=a.code, astronaut_name=a.display_name,
        source_type=f.source_type, source_id=f.source_id, conversation_id=conversation_id, text=f.text,
        is_simulated=f.is_simulated, created_at=f.created_at, level=f.level, rule_level=f.rule_level,
        rule_ids=f.rule_ids or [], rule_reasons=f.rule_reasons or [], ai_status=f.ai_status, ai_level=f.ai_level,
        ai_reason=f.ai_reason or "", ai_quote=f.ai_quote or "", ai_model=f.ai_model,
        approval_status=f.approval_status, approval_by=f.approval_by, approval_at=f.approval_at,
        approval_note=f.approval_note or "", seen_at=f.seen_at, seen_by=f.seen_by)


def _items(db: Session, rows: list[ReportFlag]) -> list[FeedItemOut]:
    crew = {a.id: a for a in db.scalars(select(Astronaut).where(Astronaut.id.in_({r.astronaut_id for r in rows})))}
    msg_ids = [r.source_id for r in rows if r.source_type == "message"]
    convs = dict(db.execute(select(Message.id, Message.conversation_id).where(Message.id.in_(msg_ids))).all()) \
        if msg_ids else {}
    return [_item(r, crew[r.astronaut_id], convs.get(r.source_id) if r.source_type == "message" else None)
            for r in rows]


@router.get("/doctor/feed", response_model=list[FeedItemOut])
@doctor_only
def feed(db: DB, after_id: int | None = None, limit: int = 50, min_level: str | None = None,
         unseen: bool = False, astronaut_id: int | None = None):
    """Newest first. Poll with after_id = the highest id already shown to get only new entries."""
    if min_level is not None and min_level not in LEVELS:
        raise HTTPException(422, f"min_level must be one of {list(LEVELS)}")
    stmt = select(ReportFlag).order_by(ReportFlag.id.desc()).limit(max(1, min(limit, 200)))
    if after_id is not None:
        stmt = stmt.where(ReportFlag.id > after_id)
    if unseen:
        stmt = stmt.where(ReportFlag.seen_at.is_(None))
    if astronaut_id is not None:
        stmt = stmt.where(ReportFlag.astronaut_id == astronaut_id)
    rows = db.scalars(stmt).all()
    if min_level:
        rows = [r for r in rows if level_rank(r.level) >= level_rank(min_level)]
    return _items(db, list(rows))


@router.post("/doctor/feed/{flag_id}/seen", response_model=FeedItemOut)
@doctor_only
def mark_seen(flag_id: int, db: DB, principal: CurrentUser):
    f = db.get(ReportFlag, flag_id)
    if f is None:
        raise HTTPException(404, "Feed entry not found.")
    if f.seen_at is None:
        f.seen_at, f.seen_by = utcnow(), principal.username
        audit(db, actor=principal.username, action="doctor.feed_seen", entity="report_flag", entity_id=f.id,
              payload={"astronaut_id": f.astronaut_id, "level": f.level, "rule_level": f.rule_level,
                       "ai_level": f.ai_level})
        db.commit()
    return _items(db, [f])[0]


class CrewMemberOut(BaseModel):
    astronaut_id: int
    code: str
    display_name: str
    callsign: str
    crew_role: str
    is_simulated: bool
    mission_day: int | None
    chronic_conditions: list[str]
    unseen_red: int
    unseen_yellow: int
    open_alerts: int
    overdue_exams: int
    level: str  # highest unseen feed level
    latest: FeedItemOut | None


@router.get("/doctor/crew", response_model=list[CrewMemberOut])
@doctor_only
def crew(db: DB):
    out = []
    today = utcnow().date()
    for a in db.scalars(select(Astronaut).order_by(Astronaut.code)):
        prof = db.get(AstronautProfile, a.id)
        m = db.scalars(select(Mission).where(Mission.astronaut_id == a.id)
                       .order_by(Mission.start_date.desc(), Mission.id.desc())).first()
        unseen = db.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == a.id,
                                                     ReportFlag.seen_at.is_(None))).all()
        latest = db.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == a.id)
                            .order_by(ReportFlag.id.desc()).limit(1)).first()
        open_alerts = db.scalar(select(func.count()).select_from(Alert).where(
            Alert.astronaut_id == a.id, Alert.status != AlertStatus.RESOLVED.value)) or 0
        levels = [f.level for f in unseen]
        _, exam_views = exams_for(db, a.id)
        out.append(CrewMemberOut(
            astronaut_id=a.id, code=a.code, display_name=a.display_name,
            callsign=prof.callsign if prof else "", crew_role=prof.crew_role if prof else "",
            is_simulated=a.is_simulated, mission_day=(today - m.start_date).days + 1 if m else None,
            chronic_conditions=list(prof.chronic_conditions) if prof else [],
            unseen_red=levels.count("red"), unseen_yellow=levels.count("yellow"), open_alerts=open_alerts,
            overdue_exams=sum(v.state == "overdue" for v in exam_views),
            level=max(levels, key=level_rank) if levels else "none",
            latest=_items(db, [latest])[0] if latest else None))
    return out


# ------------------------------------------------------------------ doctor approval (Earth side)
class ApproveIn(BaseModel):
    note: str = Field(default="", max_length=2000, description="what the doctor decided or advised (stored, audited)")


@router.post("/doctor/feed/{flag_id}/approve", response_model=FeedItemOut)
@doctor_only
def approve(flag_id: int, body: ApproveIn, db: DB, principal: CurrentUser):
    """The doctor reviewed a case that was waiting for approval. Marks it seen as well."""
    f = db.get(ReportFlag, flag_id)
    if f is None:
        raise HTTPException(404, "Feed entry not found.")
    if f.approval_status != "awaiting":
        raise HTTPException(409, "This entry is not waiting for approval.")
    now = utcnow()
    f.approval_status, f.approval_by, f.approval_at, f.approval_note = "approved", principal.username, now,         body.note.strip()
    if f.seen_at is None:
        f.seen_at, f.seen_by = now, principal.username
    audit(db, actor=principal.username, action="doctor.approved", entity="report_flag", entity_id=f.id,
          payload={"astronaut_id": f.astronaut_id, "level": f.level, "note": f.approval_note[:500]})
    db.commit()
    return _items(db, [f])[0]


class ApprovalQueueOut(BaseModel):
    awaiting: list[FeedItemOut]
    open_escalations: list[dict]


@router.get("/doctor/approvals", response_model=ApprovalQueueOut)
@doctor_only
def approvals(db: DB):
    """Everything waiting for the doctor: rule-red entries and open alert escalations."""
    from am.guidance import escalation
    from am.db.models_m5 import EscalationEvent

    rows = db.scalars(select(ReportFlag).where(ReportFlag.approval_status == "awaiting")
                      .order_by(ReportFlag.id.desc())).all()
    events = db.scalars(select(EscalationEvent).where(EscalationEvent.status == "open")
                        .order_by(EscalationEvent.id.desc())).all()
    crew = {a.id: a for a in db.scalars(select(Astronaut))}
    out_events = []
    for e in escalation.events_out(db, list(events)):
        d = e.model_dump(mode="json")
        a = crew.get(d.get("astronaut_id"))
        d["astronaut_code"], d["astronaut_name"] = (a.code, a.display_name) if a else ("", "")
        out_events.append(d)
    return ApprovalQueueOut(awaiting=_items(db, list(rows)), open_escalations=out_events)


# ------------------------------------------------------------------ the astronaut's side
class CareItemOut(BaseModel):
    id: int
    source_type: str
    text: str
    created_at: datetime
    approval_status: str | None
    approval_by: str | None
    approval_at: datetime | None
    approval_note: str


class CareStatusOut(BaseModel):
    awaiting: list[CareItemOut]
    recently_approved: list[CareItemOut]
    protocol_label: str
    protocol: dict  # the DEMO escalation matrix's CRITICAL entry: who to involve and what to do meanwhile
    am_core_notices: list[dict] = []  # when AM Core's sealed log was opened, by whom and why (never its content)


@router.get("/care/status", response_model=CareStatusOut)
def care_status(db: DB, astronaut_id: int):
    """What of the astronaut's own reports waits for the doctor, and the approved protocol meanwhile.

    Only the deterministic approval state is shown here; AI suggestions are for the doctor.
    """
    from am.amcore.access import crew_notices
    from am.guidance.escalation import load_matrix
    from am.db.enums import Severity

    def item(f: ReportFlag) -> CareItemOut:
        return CareItemOut(id=f.id, source_type=f.source_type, text=f.text, created_at=f.created_at,
                           approval_status=f.approval_status, approval_by=f.approval_by,
                           approval_at=f.approval_at, approval_note=f.approval_note or "")

    awaiting = db.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == astronaut_id,
                                                   ReportFlag.approval_status == "awaiting")
                          .order_by(ReportFlag.id.desc())).all()
    approved = db.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == astronaut_id,
                                                   ReportFlag.approval_status == "approved")
                          .order_by(ReportFlag.approval_at.desc()).limit(5)).all()
    matrix = load_matrix()
    entry = matrix.levels[Severity.CRITICAL]
    return CareStatusOut(awaiting=[item(f) for f in awaiting], recently_approved=[item(f) for f in approved],
                         protocol_label=matrix.label,
                         protocol={"procedure_id": entry.procedure_id, "plain_meaning": entry.plain_meaning,
                                   "who_to_involve": entry.who_to_involve, "guidance": entry.guidance},
                         am_core_notices=crew_notices(db))


# ------------------------------------------------------------------ explainable risk (Module N)
class FactorOut(BaseModel):
    kind: str          # alert | report | trend
    level: str         # none | yellow | red
    text: str
    rule_ids: list[str]
    record_refs: list[dict]


class ExplainOut(BaseModel):
    astronaut_id: int
    level: str
    label: str
    factors: list[FactorOut]


_SEV_TO_LEVEL = {"CRITICAL": "red", "WARNING": "yellow", "WATCH": "yellow"}
TREND_METRICS = (("heart_rate", "rest"), ("spo2", "rest"), ("body_temperature", "rest"))


@router.get("/explain", response_model=ExplainOut)
def explain(db: DB, ctx: CTX, astronaut_id: int):
    """Why this astronaut's DEMO risk level is what it is: every factor with its rule ids and records.

    Deterministic. The level is a DEMONSTRATION risk level (the highest factor), not a clinical
    probability; no percentage of risk is ever shown.
    """
    from datetime import timedelta

    from am.baseline.service import BaselineService
    from am.db.enums import DataQuality
    from am.db.models import Measurement

    factors: list[FactorOut] = []
    for al in db.scalars(select(Alert).where(Alert.astronaut_id == astronaut_id,
                                             Alert.status != AlertStatus.RESOLVED.value).order_by(Alert.id)):
        sev = al.peak_severity if al.status == "active" else al.severity
        factors.append(FactorOut(
            kind="alert", level=_SEV_TO_LEVEL.get(sev, "none"),
            text=f"Open alert: {al.metric_code.replace('_', ' ')} ({al.context}), {sev} at peak; {al.explanation}",
            rule_ids=[al.rule_id], record_refs=[{"type": "alert", "id": al.id}]
            + [{"type": "measurement", "id": e.measurement_id} for e in al.evidence[-5:]]))
    since = utcnow() - timedelta(days=1)
    for f in db.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == astronaut_id, ReportFlag.created_at >= since,
                                                 ReportFlag.rule_level != "none").order_by(ReportFlag.id.desc()).limit(10)):
        factors.append(FactorOut(kind="report", level=f.rule_level,
                                 text=f"Reported in the last 24 h: \"{f.text[:160]}\" ({' '.join(f.rule_reasons)})",
                                 rule_ids=f.rule_ids or [], record_refs=[{"type": f.source_type, "id": f.source_id}]))
    bsvc = BaselineService(db, ctx.policy, ctx.thresholds)
    three_days = utcnow() - timedelta(days=3)
    for metric, context in TREND_METRICS:
        prof = bsvc.active_profile(astronaut_id, metric, context)
        if prof is None or not prof.median:
            continue
        rows = db.scalars(select(Measurement).where(
            Measurement.astronaut_id == astronaut_id, Measurement.metric_code == metric, Measurement.context == context,
            Measurement.recorded_at >= three_days, Measurement.quality != DataQuality.REJECTED.value)).all()
        if len(rows) < 3:
            continue
        mean = sum(r.value for r in rows) / len(rows)
        pct = (mean - prof.median) / prof.median * 100
        if abs(pct) < 5:
            continue
        factors.append(FactorOut(
            kind="trend", level="none",
            text=f"{metric.replace('_', ' ')} ({context}): 3-day mean {mean:.1f} is {pct:+.0f}% from the personal "
                 f"baseline median {prof.median:.1f} (n={len(rows)} readings). Observation only; it sets no level.",
            rule_ids=["X-TREND-3D"], record_refs=[{"type": "baseline_profile", "id": prof.id}]))
    level = max((f.level for f in factors), key=level_rank, default="none")
    return ExplainOut(astronaut_id=astronaut_id, level=level, factors=factors,
                      label="DEMO risk level from deterministic rules (SIMULATED thresholds, not clinically "
                            "validated). Not a probability.")


# ------------------------------------------------------------------ crew analytics (bento board)
@router.get("/doctor/analytics", response_model=dict)
@doctor_only
def crew_analytics_route(db: DB):
    """Four live cards for the Crew page: water, activity by hour, problems by body system, wellbeing."""
    from am.analytics.service import crew_analytics

    return crew_analytics(db)
