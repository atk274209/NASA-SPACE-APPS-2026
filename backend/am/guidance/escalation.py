"""'Is this serious?' guidance and escalation events.

The DEMO matrix in config/escalation.demo.yaml turns a level (WATCH, WARNING, CRITICAL)
into plain language: what it means, who to involve and what to do. This module never
decides a level on its own:

* Alert levels come from the deterministic anomaly engine. An unacknowledged alert counts
  at its peak severity until a person reviews it, exactly like the overview status
  (docs/SAFETY.md, section 3), so no two screens disagree about the same alert.
* Symptom levels come from the astronaut's own severity rating and the DEMO
  report-promptly list in the symptom catalog, mapped through the matrix.

Escalation events are raised from AlertService.process, one per alert per level reached,
and only for levels whose matrix entry involves someone other than the astronaut. No LLM
is involved anywhere here, and nothing here diagnoses or recommends treatment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.config import REPO_ROOT
from am.db.base import utcnow
from am.db.enums import AlertStatus, Severity
from am.db.models import Alert, Astronaut, MetricDefinition
from am.db.models_m5 import EscalationEvent, SymptomReport
from am.guidance.schemas import (
    CurrentGuidanceOut,
    CurrentItemOut,
    EscalationAlertOut,
    EscalationOut,
    GuidanceLevelsOut,
    GuidanceOut,
    LevelGuidanceOut,
    PromptSymptomOut,
    SubjectOut,
    SymptomRuleOut,
)
from am.symptoms import catalog

log = logging.getLogger(__name__)

ESCALATION_CONFIG_PATH = REPO_ROOT / "config" / "escalation.demo.yaml"
SYSTEM_ACTOR = "am-system"  # the automatic actor name the alert service also uses
LEVELS: tuple[Severity, ...] = (Severity.WATCH, Severity.WARNING, Severity.CRITICAL)
EVENT_STATUSES = ("open", "acknowledged", "closed")

# If the matrix file cannot be loaded, events are still raised for the levels the shipped
# matrix escalates. A configuration error must never hide an escalation, and it must never
# break measurement ingest (the alert service calls record_escalation on that path).
FALLBACK_EVENT_LEVELS = (Severity.WARNING, Severity.CRITICAL)
FALLBACK_PROCEDURE_ID = "AM-DEMO-ESC-UNAVAILABLE"
FALLBACK_LABEL = "DEMO — NOT CLINICALLY VALIDATED"
MATRIX_UNAVAILABLE = "Escalation matrix unavailable: use the mission's medical contact procedure."

DISCLAIMER = (
    "AM is a research prototype, not a medical device. This guidance is DEMO content that points to the "
    "mission's own medical procedures. It does not diagnose and does not recommend treatment."
)
NOTHING_OPEN_NOTE = (
    "No open alerts or active symptom reports. This reflects recorded data only: if something feels wrong, "
    "record it or tell the crew medical officer."
)
CURRENT_NOTE = (
    "Guidance follows the most serious open item. The mission's own medical procedures always take precedence."
)


# ---------------------------------------------------------------- matrix
class EscalationConfigError(RuntimeError):
    """The escalation matrix file is missing or invalid."""


class EscalationStateError(RuntimeError):
    """The escalation event is not in a state that allows the requested action."""


class LevelEntry(BaseModel):
    procedure_id: str
    plain_meaning: str
    who_to_involve: str
    guidance: str
    raises_event: bool


class SymptomLevels(BaseModel):
    report_promptly: Severity
    by_severity: dict[str, Severity]


class EscalationMatrix(BaseModel):
    version: str
    label: str
    levels: dict[Severity, LevelEntry]
    symptoms: SymptomLevels

    @model_validator(mode="after")
    def _check(self) -> EscalationMatrix:
        if set(self.levels) != set(LEVELS):
            raise ValueError("levels must define exactly WATCH, WARNING and CRITICAL")
        if "DEMO" not in self.label:
            raise ValueError("label must keep the DEMO marker until a medical team validates the matrix")
        if set(self.symptoms.by_severity) != set(catalog.SEVERITIES):
            raise ValueError(f"symptoms.by_severity must map exactly {', '.join(catalog.SEVERITIES)}")
        if any(lvl not in LEVELS for lvl in (self.symptoms.report_promptly, *self.symptoms.by_severity.values())):
            raise ValueError("symptoms must map to WATCH, WARNING or CRITICAL")
        return self


def load_matrix(path: Path | None = None) -> EscalationMatrix:
    """Load and cache the matrix. Raises EscalationConfigError with a readable reason."""
    return _load_matrix((path or ESCALATION_CONFIG_PATH).resolve())


@lru_cache(maxsize=4)
def _load_matrix(path: Path) -> EscalationMatrix:
    # Failures are not cached, so a fixed file is picked up without a restart.
    try:
        return EscalationMatrix.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EscalationConfigError(f"escalation matrix '{path.name}' could not be loaded: {exc}") from exc


def _try_matrix() -> EscalationMatrix | None:
    try:
        return load_matrix()
    except EscalationConfigError as exc:
        log.error("%s", exc)
        return None


def level_out(level: Severity, entry: LevelEntry) -> LevelGuidanceOut:
    return LevelGuidanceOut(level=level.value, procedure_id=entry.procedure_id, plain_meaning=entry.plain_meaning,
                            who_to_involve=entry.who_to_involve, guidance=entry.guidance,
                            raises_event=entry.raises_event)


def symptom_rules(matrix: EscalationMatrix) -> list[SymptomRuleOut]:
    rules = [SymptomRuleOut(when="On the DEMO report-promptly list, whatever the rating",
                            level=matrix.symptoms.report_promptly.value)]
    rules += [SymptomRuleOut(when=f"Rated {sev}", level=matrix.symptoms.by_severity[sev].value)
              for sev in catalog.SEVERITIES]
    return rules


def levels_out(matrix: EscalationMatrix) -> GuidanceLevelsOut:
    return GuidanceLevelsOut(
        version=matrix.version,
        label=matrix.label,
        levels=[level_out(lvl, matrix.levels[lvl]) for lvl in LEVELS],
        symptom_rules=symptom_rules(matrix),
        report_promptly_label=catalog.REPORT_PROMPTLY_LABEL,
        report_promptly_symptoms=[PromptSymptomOut(code=s.code, label=s.label)
                                  for s in catalog.SYMPTOMS if s.report_promptly],
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------- levels
def rank(level: str | Severity) -> int:
    try:
        return Severity(level).rank
    except ValueError:
        return 0


def alert_level(alert: Alert) -> Severity:
    """The level an alert counts at for guidance and the body map.

    Unacknowledged (active) alerts count at their PEAK severity and acknowledged ones at
    their current severity: the overview's status rule, which keeps a critical episode
    visible until a person has reviewed it. Resolved alerts report their peak, for reference.
    """
    sev = Severity(alert.severity if alert.status == AlertStatus.ACKNOWLEDGED.value else alert.peak_severity)
    # Alerts only ever open at WATCH or above; the floor only guards hand-edited rows.
    return sev if sev in LEVELS else Severity.WATCH


def symptom_level(report: SymptomReport, matrix: EscalationMatrix) -> tuple[Severity, list[str]]:
    """Level for a self-reported symptom, with the plain reasons behind it."""
    symptom = catalog.get(report.symptom_code)
    label = symptom_label(report.symptom_code)
    reasons: list[str] = []
    rated = matrix.symptoms.by_severity.get(report.severity)
    if rated is None:
        # Ratings are validated when a report is created; an unknown one gets the most cautious level.
        rated = max(matrix.symptoms.by_severity.values(), key=rank)
        reasons.append(f"The rating '{report.severity}' is not recognised, so AM uses the most cautious level.")
    else:
        reasons.append(f"{label} was rated {report.severity} by the astronaut.")
    levels = [rated]
    if symptom is not None and symptom.report_promptly:
        levels.append(matrix.symptoms.report_promptly)
        reasons.append(f"{label} is on the DEMO report-promptly list, which maps to "
                       f"{matrix.symptoms.report_promptly.value.lower()} whatever the rating.")
    return max(levels, key=rank), reasons


# ---------------------------------------------------------------- naming helpers
def metric_names(session: Session) -> dict[str, str]:
    return {code: name for code, name in session.execute(select(MetricDefinition.code,
                                                                MetricDefinition.display_name)).all()}


def metric_name(names: dict[str, str], code: str) -> str:
    return names.get(code) or code.replace("_", " ").capitalize()


def alert_title(alert: Alert, names: dict[str, str]) -> str:
    return f"{metric_name(names, alert.metric_code)} ({alert.context}), {alert.direction} deviation"


def symptom_label(code: str) -> str:
    symptom = catalog.get(code)
    return symptom.label if symptom else code.replace("_", " ").capitalize()


def _when(ts: datetime | None) -> str:
    return f" on {ts:%Y-%m-%d %H:%M} UTC" if ts else ""


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- escalation events
def record_escalation(
    session: Session,
    alert: Alert,
    level: Severity,
    *,
    measurement_id: int | None,
    data_time: datetime | None,
) -> EscalationEvent | None:
    """Raise the escalation event for `alert` reaching `level`, at most once per level.

    Called by AlertService.process when an alert opens or its peak severity rises. A level
    whose matrix entry has raises_event=false (WATCH, 'Yourself') records nothing, because
    no one else needs to be involved. The guidance text is copied onto the event, so the
    record stays readable as it was raised even if the matrix is edited later.
    """
    matrix = _try_matrix()
    snapshot: dict[str, Any]
    if matrix is None:
        if level not in FALLBACK_EVENT_LEVELS:
            return None
        procedure_id, snapshot = FALLBACK_PROCEDURE_ID, {"matrix_error": True}
    else:
        entry = matrix.levels.get(level)
        if entry is None or not entry.raises_event:
            return None
        procedure_id = entry.procedure_id
        snapshot = {"plain_meaning": entry.plain_meaning, "who_to_involve": entry.who_to_involve,
                    "guidance": entry.guidance, "matrix_version": matrix.version, "label": matrix.label}
    exists = session.scalar(select(EscalationEvent.id).where(EscalationEvent.alert_id == alert.id,
                                                              EscalationEvent.level == level.value))
    if exists is not None:
        return None
    event = EscalationEvent(
        astronaut_id=alert.astronaut_id,
        alert_id=alert.id,
        level=level.value,
        procedure_id=procedure_id,
        actions=[{"kind": "raised", **snapshot, "measurement_id": measurement_id,
                  "data_time": data_time.isoformat() if data_time else None}],
        status="open",
    )
    session.add(event)
    session.flush()  # autoflush is off: later checks in the same ingest batch must see this row
    audit(session, actor=SYSTEM_ACTOR, action="escalation.raised", entity="escalation_event", entity_id=event.id,
          payload={"alert_id": alert.id, "level": level.value, "procedure_id": procedure_id,
                   "measurement_id": measurement_id})
    return event


def acknowledge_event(session: Session, event_id: int, actor: str, note: str = "") -> EscalationEvent:
    """A named person confirms they have seen the escalation and handled it (audited)."""
    event = session.get(EscalationEvent, event_id)
    if event is None:
        raise LookupError("escalation event not found")
    if event.status != "open":
        raise EscalationStateError(f"escalation event is already {event.status}")
    actor, note = actor.strip(), note.strip()
    if not actor:
        raise ValueError("a named actor is required to acknowledge an escalation")
    event.status = "acknowledged"
    event.acknowledged_by = actor
    # JSON columns are not mutation-tracked: assign a new list instead of appending in place.
    event.actions = [*event.actions, {"kind": "acknowledged", "actor": actor, "note": note,
                                      "at": utcnow().isoformat()}]
    audit(session, actor=actor, action="escalation.acknowledged", entity="escalation_event", entity_id=event.id,
          payload={"alert_id": event.alert_id, "level": event.level, "note": note})
    session.flush()
    return event


def list_events(session: Session, astronaut_id: int, *, status: str | None = None,
                alert_id: int | None = None) -> list[EscalationEvent]:
    """Events for one astronaut: open first, then the most serious, then the newest."""
    if status is not None and status not in EVENT_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(EVENT_STATUSES)}")
    stmt = select(EscalationEvent).where(EscalationEvent.astronaut_id == astronaut_id)
    if status is not None:
        stmt = stmt.where(EscalationEvent.status == status)
    if alert_id is not None:
        stmt = stmt.where(EscalationEvent.alert_id == alert_id)
    rows = session.scalars(stmt).all()
    return sorted(rows, key=lambda e: (e.status != "open", -rank(e.level), -e.id))


def events_out(session: Session, events: Iterable[EscalationEvent]) -> list[EscalationOut]:
    events = list(events)
    matrix = _try_matrix()
    alert_ids = {e.alert_id for e in events if e.alert_id is not None}
    alerts = {a.id: a for a in session.scalars(select(Alert).where(Alert.id.in_(alert_ids)))} if alert_ids else {}
    names = metric_names(session) if alerts else {}
    return [_event_out(e, alerts.get(e.alert_id) if e.alert_id else None, names, matrix) for e in events]


def _event_out(e: EscalationEvent, alert: Alert | None, names: dict[str, str],
               matrix: EscalationMatrix | None) -> EscalationOut:
    raised = next((a for a in e.actions if a.get("kind") == "raised"), {})
    ack = next((a for a in reversed(e.actions) if a.get("kind") == "acknowledged"), None)
    # Fallback text for events raised while the matrix was unavailable: today's matrix entry.
    current = matrix.levels.get(Severity(e.level)) if matrix is not None and rank(e.level) else None

    def text(key: str) -> str:
        return raised.get(key) or (getattr(current, key) if current else MATRIX_UNAVAILABLE)

    return EscalationOut(
        id=e.id,
        astronaut_id=e.astronaut_id,
        alert_id=e.alert_id,
        level=e.level,
        procedure_id=e.procedure_id,
        status=e.status,
        plain_meaning=text("plain_meaning"),
        who_to_involve=text("who_to_involve"),
        guidance=text("guidance"),
        label=raised.get("label") or (matrix.label if matrix else FALLBACK_LABEL),
        data_time=_parse_ts(raised.get("data_time")),
        acknowledged_by=e.acknowledged_by,
        acknowledged_at=_parse_ts(ack.get("at")) if ack else None,
        acknowledgement_note=(ack.get("note") or None) if ack else None,
        created_at=e.created_at,
        updated_at=e.updated_at,
        alert=None if alert is None else EscalationAlertOut(
            id=alert.id, metric_code=alert.metric_code, display_name=metric_name(names, alert.metric_code),
            context=alert.context, direction=alert.direction, severity=alert.severity,
            peak_severity=alert.peak_severity, status=alert.status, is_simulated=alert.is_simulated),
    )


# ---------------------------------------------------------------- guidance views
def open_alerts(session: Session, astronaut_id: int) -> list[Alert]:
    return list(session.scalars(select(Alert).where(Alert.astronaut_id == astronaut_id,
                                                    Alert.status != AlertStatus.RESOLVED.value)
                                .order_by(Alert.last_seen_at.desc(), Alert.id.desc())))


def active_symptoms(session: Session, astronaut_id: int) -> list[SymptomReport]:
    return list(session.scalars(select(SymptomReport).where(SymptomReport.astronaut_id == astronaut_id,
                                                            SymptomReport.status == "active")
                                .order_by(SymptomReport.reported_at.desc(), SymptomReport.id.desc())))


def alert_reasons(alert: Alert) -> list[str]:
    peak, current = alert.peak_severity.lower(), alert.severity.lower()
    if alert.status == AlertStatus.ACTIVE.value:
        out = [f"Not yet acknowledged, so it counts at its peak severity ({peak}) until someone reviews it."]
        if alert.severity != alert.peak_severity:
            out.append(f"The latest assessed reading is {current}.")
    elif alert.status == AlertStatus.ACKNOWLEDGED.value:
        peak_note = f"; its peak was {peak}" if alert.severity != alert.peak_severity else ""
        out = [f"Acknowledged, so it counts at its current severity ({current}){peak_note}."]
    else:
        out = [f"Resolved{_when(alert.resolved_at)}. Shown for reference only: it counted at its peak ({peak})."]
    out.append(f"Alert levels are deviations from this astronaut's own baseline ({alert.threshold_label}), "
               "not clinical cut-offs.")
    return out


def _guidance(matrix: EscalationMatrix, level: Severity, subject: SubjectOut, *, applies_now: bool,
              reasons: list[str], escalations: list[EscalationOut]) -> GuidanceOut:
    entry = matrix.levels[level]
    return GuidanceOut(subject=subject, level=level.value, procedure_id=entry.procedure_id,
                       plain_meaning=entry.plain_meaning, who_to_involve=entry.who_to_involve,
                       guidance=entry.guidance, label=matrix.label, matrix_version=matrix.version,
                       applies_now=applies_now, reasons=reasons, escalations=escalations)


def guidance_for_alert(session: Session, alert_id: int) -> GuidanceOut:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise LookupError("alert not found")
    matrix = load_matrix()
    names = metric_names(session)
    subject = SubjectOut(type="alert", id=alert.id, astronaut_id=alert.astronaut_id,
                         title=alert_title(alert, names), status=alert.status, is_simulated=alert.is_simulated)
    events = list_events(session, alert.astronaut_id, alert_id=alert.id)
    return _guidance(matrix, alert_level(alert), subject, applies_now=alert.status != AlertStatus.RESOLVED.value,
                     reasons=alert_reasons(alert), escalations=events_out(session, events))


def guidance_for_symptom(session: Session, report_id: int) -> GuidanceOut:
    report = session.get(SymptomReport, report_id)
    if report is None:
        raise LookupError("symptom report not found")
    matrix = load_matrix()
    level, reasons = symptom_level(report, matrix)
    active = report.status == "active"
    if not active:
        reasons.append(f"Resolved{_when(report.resolved_at)}. Shown for reference only.")
    reasons.append("The rating is the astronaut's own description, not a clinical grade.")
    subject = SubjectOut(type="symptom_report", id=report.id, astronaut_id=report.astronaut_id,
                         title=f"{symptom_label(report.symptom_code)}, rated {report.severity}",
                         status=report.status, is_simulated=report.is_simulated)
    return _guidance(matrix, level, subject, applies_now=active, reasons=reasons, escalations=[])


def current_guidance(session: Session, astronaut_id: int) -> CurrentGuidanceOut:
    """The most serious open item for one astronaut, across open alerts and active symptoms."""
    if session.get(Astronaut, astronaut_id) is None:
        raise LookupError("astronaut not found")
    matrix = load_matrix()
    names = metric_names(session)
    items: list[CurrentItemOut] = []
    for alert in open_alerts(session, astronaut_id):
        items.append(CurrentItemOut(type="alert", id=alert.id, title=alert_title(alert, names),
                                    level=alert_level(alert).value, reason=alert_reasons(alert)[0]))
    for report in active_symptoms(session, astronaut_id):
        level, reasons = symptom_level(report, matrix)
        items.append(CurrentItemOut(type="symptom_report", id=report.id,
                                    title=f"{symptom_label(report.symptom_code)}, rated {report.severity}",
                                    level=level.value, reason=" ".join(reasons)))
    items.sort(key=lambda i: -rank(i.level))  # stable: newest first within a level
    top = Severity(items[0].level) if items else None
    open_count = session.scalar(select(func.count()).select_from(EscalationEvent).where(
        EscalationEvent.astronaut_id == astronaut_id, EscalationEvent.status == "open")) or 0
    return CurrentGuidanceOut(
        astronaut_id=astronaut_id,
        level=top.value if top else None,
        guidance=level_out(top, matrix.levels[top]) if top else None,
        items=items,
        open_escalations=open_count,
        label=matrix.label,
        matrix_version=matrix.version,
        note=CURRENT_NOTE if items else NOTHING_OPEN_NOTE,
    )
