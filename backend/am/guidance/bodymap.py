"""Body map: one status per body system for an astronaut, from stored records only.

Each system in catalog.BODY_SYSTEMS gets a status derived from the same levels the
guidance panel uses (am.guidance.escalation), so the map, the guidance panel and the
overview never disagree about one alert or symptom. With the shipped DEMO matrix:

* red      an open alert counting at CRITICAL on a mapped indicator, or an active symptom
           rated severe or on the DEMO report-promptly list
* yellow   an open alert counting at WARNING, or an active symptom rated mild or moderate
* watch    only WATCH-level alerts are open
* green    something is recorded for the system and none of the above applies
* no_data  nothing is recorded for the system

The map only reflects what was recorded. Green means "no open alert and no active symptom
report", never "healthy", and a system without records is shown as such, never inferred.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.ai.checkin import CHECKIN_ITEMS
from am.db.base import utcnow
from am.db.enums import AlertStatus, DataQuality, Severity
from am.db.models import Alert, Astronaut, Measurement
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import KnowledgeDocument
from am.guidance.escalation import (
    EscalationMatrix,
    active_symptoms,
    alert_level,
    load_matrix,
    metric_name,
    metric_names,
    open_alerts,
    rank,
    symptom_label,
    symptom_level,
)
from am.guidance.schemas import (
    BodyMapOut,
    BodyStatus,
    BodySystemOut,
    ReasonOut,
    StatusRuleOut,
    SystemAlertOut,
    SystemDocumentOut,
    SystemReadingOut,
    SystemSymptomOut,
)
from am.symptoms import catalog

# Indicator-to-system mapping (M5 spec, F7). Symptoms map through catalog.Symptom.body_system.
# Eyes and digestion have no measured indicator in AMC, so only symptom reports inform them.
SYSTEM_METRICS: dict[str, tuple[str, ...]] = {
    "neuro_behavioral": ("sleep_duration",),
    "eyes": (),
    "cardiovascular": ("heart_rate", "hrv_rmssd", "systolic_bp", "diastolic_bp"),
    "respiratory": ("spo2", "respiratory_rate"),
    "gastrointestinal": (),
    "musculoskeletal": ("exercise_duration", "step_count"),
    "immune_skin": ("body_temperature",),
    "metabolic": ("blood_glucose", "water_intake", "energy_intake", "body_mass"),
}
SELF_REPORT_SYSTEM = "neuro_behavioral"  # daily check-in self-ratings (mood, stress, ...) belong here
MAPPED_METRICS: tuple[str, ...] = tuple(m for metrics in SYSTEM_METRICS.values() for m in metrics)

# Related reading: knowledge-base topics (knowledge/sources/*.json) per system. A navigation
# aid that says nothing about this astronaut. No curated source covers breathing yet, and
# none is specific to digestion, so digestion points to the nutrition topic (closest match).
SYSTEM_TOPICS: dict[str, tuple[str, ...]] = {
    "neuro_behavioral": ("behavioral_health", "sleep", "circadian"),
    "eyes": ("sans",),
    "cardiovascular": ("cardiovascular",),
    "respiratory": (),
    "gastrointestinal": ("nutrition",),
    "musculoskeletal": ("bone", "muscle", "exercise"),
    "immune_skin": ("immune",),
    "metabolic": ("nutrition",),
}
MAX_DOCUMENTS = 4

STATUS_BY_LEVEL: dict[Severity, BodyStatus] = {
    Severity.CRITICAL: "red",
    Severity.WARNING: "yellow",
    Severity.WATCH: "watch",
}
RULES_NOTE = ("DEMO rules. Unacknowledged alerts count at their peak until someone reviews them. Green means "
              "nothing is open for that system, not that it is healthy.")

assert set(SYSTEM_METRICS) == set(catalog.BODY_SYSTEMS) == set(SYSTEM_TOPICS)


def status_rules(matrix: EscalationMatrix) -> list[StatusRuleOut]:
    """Legend text generated from the matrix, so it always matches what the code does."""

    def symptoms_at(level: Severity) -> str:
        parts = []
        rated = [sev for sev in catalog.SEVERITIES if matrix.symptoms.by_severity[sev] == level]
        if rated:
            parts.append("rated " + " or ".join(rated))
        if matrix.symptoms.report_promptly == level:
            parts.append("on the DEMO report-promptly list")
        return f", or an active symptom {' or '.join(parts)}" if parts else ""

    return [
        StatusRuleOut(status="red", meaning=f"An open alert counting at critical{symptoms_at(Severity.CRITICAL)}."),
        StatusRuleOut(status="yellow", meaning=f"An open alert counting at warning{symptoms_at(Severity.WARNING)}."),
        StatusRuleOut(status="watch", meaning=f"Only watch-level alerts are open{symptoms_at(Severity.WATCH)}."),
        StatusRuleOut(status="green", meaning="Something is recorded for this system and none of the above applies."),
        StatusRuleOut(status="no_data", meaning="Nothing is recorded for this system yet."),
    ]


# ---------------------------------------------------------------- queries
def _latest_readings(session: Session, astronaut_id: int) -> dict[str, list[Measurement]]:
    """Latest accepted reading per (metric, context), by recording time rather than insert order."""
    rn = func.row_number().over(
        partition_by=(Measurement.metric_code, Measurement.context),
        order_by=(Measurement.recorded_at.desc(), Measurement.id.desc()),
    ).label("rn")
    ranked = (select(Measurement.id, rn)
              .where(Measurement.astronaut_id == astronaut_id, Measurement.metric_code.in_(MAPPED_METRICS),
                     Measurement.quality != DataQuality.REJECTED.value)
              .subquery())
    rows = session.scalars(select(Measurement).where(Measurement.id.in_(select(ranked.c.id).where(ranked.c.rn == 1))))
    out: dict[str, list[Measurement]] = {}
    for m in sorted(rows, key=lambda r: (r.metric_code, r.context)):
        out.setdefault(m.metric_code, []).append(m)
    return out


def _latest_self_reports(session: Session, astronaut_id: int) -> list[SelfReport]:
    rn = func.row_number().over(partition_by=SelfReport.kind,
                                order_by=(SelfReport.recorded_at.desc(), SelfReport.id.desc())).label("rn")
    ranked = (select(SelfReport.id, rn)
              .where(SelfReport.astronaut_id == astronaut_id, SelfReport.kind.in_(tuple(CHECKIN_ITEMS)))
              .subquery())
    rows = session.scalars(select(SelfReport).where(SelfReport.id.in_(select(ranked.c.id).where(ranked.c.rn == 1))))
    order = list(CHECKIN_ITEMS)
    return sorted(rows, key=lambda r: order.index(r.kind))


def _last_report_by_symptom(session: Session, astronaut_id: int) -> dict[str, datetime]:
    """Most recent report time per symptom code, active or resolved (both count as recorded data)."""
    rows = session.execute(select(SymptomReport.symptom_code, func.max(SymptomReport.reported_at))
                           .where(SymptomReport.astronaut_id == astronaut_id)
                           .group_by(SymptomReport.symptom_code)).all()
    return {code: ts for code, ts in rows if ts is not None}


# ---------------------------------------------------------------- per-system assembly
def _coverage(code: str, names: dict[str, str]) -> str:
    metrics = SYSTEM_METRICS[code]
    if not metrics:
        return "Symptom reports only. AMC has no measured indicator for this system."
    extra = " and check-in self-ratings" if code == SELF_REPORT_SYSTEM else ""
    return f"Indicators: {', '.join(metric_name(names, m) for m in metrics)}. Also symptom reports{extra}."


def _reading_out(m: Measurement, names: dict[str, str]) -> SystemReadingOut:
    return SystemReadingOut(source="measurement", record_id=m.id, code=m.metric_code,
                            display_name=metric_name(names, m.metric_code), context=m.context, value=m.value,
                            unit=m.unit, recorded_at=m.recorded_at, severity=m.severity,
                            assessment_status=m.assessment_status, is_simulated=m.is_simulated)


def _self_report_out(r: SelfReport) -> SystemReadingOut:
    return SystemReadingOut(source="self_report", record_id=r.id, code=f"self_report:{r.kind}",
                            display_name=f"{CHECKIN_ITEMS[r.kind][0]} (self-rating)", context="check-in",
                            value=r.score, unit=f"/{r.scale_max}", recorded_at=r.recorded_at, severity=None,
                            assessment_status=None, is_simulated=r.is_simulated)


def _alert_out(a: Alert, names: dict[str, str]) -> SystemAlertOut:
    return SystemAlertOut(id=a.id, metric_code=a.metric_code, display_name=metric_name(names, a.metric_code),
                          context=a.context, direction=a.direction, severity=a.severity,
                          peak_severity=a.peak_severity, status=a.status, level=alert_level(a).value,
                          last_seen_at=a.last_seen_at, is_simulated=a.is_simulated)


def _symptom_out(r: SymptomReport, matrix: EscalationMatrix) -> SystemSymptomOut:
    symptom = catalog.get(r.symptom_code)
    return SystemSymptomOut(id=r.id, symptom_code=r.symptom_code, label=symptom_label(r.symptom_code),
                            severity=r.severity, report_promptly=bool(symptom and symptom.report_promptly),
                            level=symptom_level(r, matrix)[0].value, reported_at=r.reported_at,
                            onset_at=r.onset_at, source=r.source, is_simulated=r.is_simulated)


def _documents(code: str, docs: list[KnowledgeDocument]) -> list[SystemDocumentOut]:
    topics = SYSTEM_TOPICS[code]
    matched = [(d, [t for t in topics if t in (d.topics or [])]) for d in docs]
    matched = [(d, ts) for d, ts in matched if ts]
    matched.sort(key=lambda x: (-len(x[1]), x[0].title))
    return [SystemDocumentOut(slug=d.slug, title=d.title, organization=d.organization, url=d.url,
                              matched_topics=ts) for d, ts in matched[:MAX_DOCUMENTS]]


def _reasons(alerts: list[SystemAlertOut], symptoms: list[SystemSymptomOut], has_data: bool) -> list[ReasonOut]:
    out: list[ReasonOut] = []
    for a in alerts:
        review = "not yet acknowledged" if a.status == AlertStatus.ACTIVE.value else "acknowledged"
        out.append(ReasonOut(text=f"Open alert: {a.display_name} ({a.context}), {a.direction} deviation, counting at "
                                  f"{a.level.lower()} ({review}).",
                             level=a.level, ref_type="alert", ref_id=a.id))
    for s in symptoms:
        promptly = " (DEMO report-promptly list)" if s.report_promptly else ""
        out.append(ReasonOut(text=f"Active symptom: {s.label}, rated {s.severity}{promptly}.",
                             level=s.level, ref_type="symptom_report", ref_id=s.id))
    out.sort(key=lambda r: -rank(r.level or ""))  # stable: keeps alerts before symptoms within a level
    if not out:
        out.append(ReasonOut(text="Records exist, and there is no open alert or active symptom report."
                             if has_data else "Nothing recorded for this system yet."))
    return out


def build_body_map(session: Session, astronaut_id: int) -> BodyMapOut:
    astronaut = session.get(Astronaut, astronaut_id)
    if astronaut is None:
        raise LookupError("astronaut not found")
    matrix = load_matrix()
    names = metric_names(session)
    readings = _latest_readings(session, astronaut_id)
    self_reports = _latest_self_reports(session, astronaut_id)
    alerts = open_alerts(session, astronaut_id)
    symptoms = active_symptoms(session, astronaut_id)
    last_report = _last_report_by_symptom(session, astronaut_id)
    docs = list(session.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.title)))

    systems: list[BodySystemOut] = []
    for code, label in catalog.BODY_SYSTEMS.items():
        metrics = SYSTEM_METRICS[code]
        symptom_codes = {s.code for s in catalog.symptoms_for_system(code)}
        rows = [_reading_out(m, names) for metric in metrics for m in readings.get(metric, [])]
        if code == SELF_REPORT_SYSTEM:
            rows += [_self_report_out(r) for r in self_reports]
        sys_alerts = [_alert_out(a, names) for a in alerts if a.metric_code in metrics]
        sys_symptoms = [_symptom_out(r, matrix) for r in symptoms if r.symptom_code in symptom_codes]
        times = [r.recorded_at for r in rows] + [ts for c, ts in last_report.items() if c in symptom_codes]
        last_data_at = max(times, default=None)
        levels = [Severity(a.level) for a in sys_alerts] + [Severity(s.level) for s in sys_symptoms]
        top = max(levels, key=rank) if levels else None
        status: BodyStatus = STATUS_BY_LEVEL[top] if top else ("green" if last_data_at else "no_data")
        systems.append(BodySystemOut(
            code=code,
            label=label,
            status=status,
            level=top.value if top else None,
            coverage=_coverage(code, names),
            reasons=_reasons(sys_alerts, sys_symptoms, last_data_at is not None),
            metrics=rows,
            active_symptoms=sys_symptoms,
            open_alerts=sys_alerts,
            documents=_documents(code, docs),
            last_data_at=last_data_at,
        ))
    return BodyMapOut(
        astronaut_id=astronaut_id,
        generated_at=utcnow(),
        simulated=astronaut.is_simulated,
        label=matrix.label,
        rules=status_rules(matrix),
        rules_note=RULES_NOTE,
        knowledge_indexed=bool(docs),
        systems=systems,
    )
