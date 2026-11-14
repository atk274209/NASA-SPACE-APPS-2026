"""Crew and mission management, and manual measurement entry (owner: feature F1).

Manual entries run through the same pipeline as sensor data (convert, validate, assess,
alert). This module adds what a person entering their own data needs: an entry
specification for the form, a result that shows the unit conversion, and their recent
entries. Missions carry an environment code; AM attaches no physiological numbers to it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.auth.guard import doctor_only, signed_in
from am.api.deps import AppContext, get_ctx, get_db
from am.audit import audit
from am.db.base import utcnow
from am.db.enums import DataQuality, MeasurementContext, SourceKind
from am.db.models import Alert, AlertEvidence, Astronaut, DataSource, Measurement, Mission
from am.health.ingest import IngestResult, IngestService, MeasurementInput
from am.health.metrics import CATEGORY_LABELS, CONTEXT_LABELS, METRICS, MetricSpec, get_spec
from am.health.units import canonical_label, conversions_for, find_conversion
from am.health.validation import DEGRADED_CONFIDENCE, DEGRADED_SIGNAL_QUALITY, MAX_FUTURE_SKEW
from am.schemas import BaselineEventOut

router = APIRouter(tags=["crew"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]

DEFAULT_SOURCE_NAME = "manual entry"
RADIATION_METRIC = "radiation_dose"
RADIATION_UNIT = next(m.unit for m in METRICS if m.code == RADIATION_METRIC)  # fails fast if unregistered
RADIATION_NOTE = "AM does not evaluate exposure limits."
# Dose sums are rounded to 6 decimals (1 nSv) only to strip floating-point noise from the
# addition; dosimeter readings are far coarser than that.
DOSE_DECIMALS = 6

EnvironmentCode = Literal[
    "leo_station", "lunar_surface", "deep_space_transit", "mars_surface", "ground_analog", "unspecified"
]

# Plain descriptions only: no physiological numbers or expected effects.
ENVIRONMENTS: dict[str, tuple[str, str]] = {
    "leo_station": (
        "Low Earth orbit station",
        "An orbiting research station close to Earth, such as the International Space Station. "
        "Crew live in microgravity, with frequent contact with ground teams and regular resupply.",
    ),
    "lunar_surface": (
        "Lunar surface",
        "Living and working on the Moon: partial gravity, lunar dust, and a longer distance to Earth "
        "than a station in orbit.",
    ),
    "deep_space_transit": (
        "Deep space transit",
        "Travel beyond low Earth orbit, for example toward the Moon or Mars. Microgravity, outside the "
        "protection of Earth's magnetic field, with growing communication delays.",
    ),
    "mars_surface": (
        "Mars surface",
        "Living and working on Mars: partial gravity, long communication delays with Earth and very "
        "limited resupply.",
    ),
    "ground_analog": (
        "Ground analog",
        "A mission simulated on Earth, such as an isolation habitat or a polar research station. "
        "Normal gravity; used to study isolation and confinement.",
    ),
    "unspecified": ("Not specified", "The mission environment has not been recorded."),
}


# ---------------------------------------------------------------- schemas
class EntryUnitOut(BaseModel):
    unit: str
    label: str
    canonical: bool
    # canonical value = (entered value + offset) * factor; identity for the canonical unit
    factor: float
    offset: float
    note: str
    source: str
    aliases: list[str]


class EntryMetricOut(BaseModel):
    code: str
    display_name: str
    category: str
    category_label: str
    unit: str
    unit_label: str
    accepted_units: list[EntryUnitOut]
    contexts: list[str]
    plausible_min: float | None
    plausible_max: float | None
    sanity_note: str
    bound_note: str  # why this metric's bounds sit where they do ("" when nothing to add)
    baseline_enabled: bool
    alert_rule_id: str | None  # DEMO personal-deviation rule, None when the metric is logged only


class EntryChoiceOut(BaseModel):
    code: str
    label: str
    description: str = ""


class EntrySpecOut(BaseModel):
    metrics: list[EntryMetricOut]
    categories: list[EntryChoiceOut]
    contexts: list[EntryChoiceOut]
    default_source_name: str
    max_future_minutes: float
    degraded_confidence_below: float
    degraded_signal_quality: list[str]
    threshold_label: str


class ManualEntryIn(BaseModel):
    # Non-finite numbers are refused by the ingest pipeline with a plain 422 message; a
    # schema-level refusal would echo NaN back in the error body, which is not valid JSON.
    astronaut_id: int
    metric_code: str = Field(min_length=1, max_length=64)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    context: str = Field(min_length=1, max_length=16)
    recorded_at: datetime | None = None  # None: now (UTC)
    source_name: str = Field(default=DEFAULT_SOURCE_NAME, max_length=128)
    confidence: float | None = None
    signal_quality: str | None = Field(default=None, max_length=32)


class EntryConversionOut(BaseModel):
    entered_value: float
    entered_unit: str
    entered_label: str
    canonical_value: float
    canonical_unit: str
    canonical_label: str
    formula: str
    source: str


class ManualEntryRowOut(BaseModel):
    id: int
    astronaut_id: int
    metric_code: str
    display_name: str
    value: float
    unit: str
    unit_label: str
    conversion: EntryConversionOut | None
    context: str
    recorded_at: datetime
    ingested_at: datetime
    source_name: str
    is_simulated: bool
    quality: str
    accepted: bool
    confidence: float | None
    validation_notes: list[str]
    assessment_status: str | None
    severity: str | None
    deviation_score: float | None
    alert_id: int | None  # the alert this reading is evidence for, if any


class EntryAssessmentOut(BaseModel):
    status: str
    severity: str | None
    instant_severity: str | None
    z: float | None
    direction: str | None
    persistence_count: int
    baseline_profile_id: int | None
    rule_id: str | None
    reasons: list[str]


class EntryAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    severity: str
    peak_severity: str
    status: str
    threshold_label: str


class ManualEntryOut(BaseModel):
    accepted: bool
    entry: ManualEntryRowOut
    assessment: EntryAssessmentOut
    alert: EntryAlertOut | None
    baseline_event: BaselineEventOut | None
    threshold_label: str


class MissionCreateIn(BaseModel):
    astronaut_id: int
    name: str = Field(min_length=1, max_length=128)
    start_date: date
    planned_days: int | None = Field(default=None, ge=1)
    environment: EnvironmentCode
    actor: str = Field(default="operator", min_length=1, max_length=64)


class MissionDetailOut(BaseModel):
    id: int
    name: str
    astronaut_id: int
    start_date: date
    planned_days: int | None
    environment: str
    environment_label: str
    is_simulated: bool
    created_at: datetime
    is_current: bool  # the newest mission: AM counts mission days from its start date


class MissionEnvironmentOut(BaseModel):
    code: str
    label: str
    description: str


class RadiationDayOut(BaseModel):
    utc_date: date
    mission_day: int | None  # relative to the current mission's start; None before it
    dose: float  # sum of that day's dosimeter entries
    entries: int
    cumulative: float  # running sum up to and including this day
    simulated: bool


class RadiationCumulativeOut(BaseModel):
    astronaut_id: int
    unit: str
    days: list[RadiationDayOut]
    total: float
    entries: int
    mission_start: date | None
    simulated: bool
    note: str


# ---------------------------------------------------------------- helpers
def _astronaut(db: Session, astronaut_id: int) -> Astronaut:
    a = db.get(Astronaut, astronaut_id)
    if a is None:
        raise HTTPException(404, "astronaut not found")
    return a


def _current_mission(db: Session, astronaut_id: int) -> Mission | None:
    # Same rule as the overview: the newest mission is the current one.
    return db.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                      .order_by(Mission.id.desc()).limit(1)).first()


def _context_choice(code: str) -> EntryChoiceOut:
    label, description = CONTEXT_LABELS.get(code, (code, ""))
    return EntryChoiceOut(code=code, label=label, description=description)


def _units(spec: MetricSpec) -> list[EntryUnitOut]:
    canonical = EntryUnitOut(unit=spec.unit, label=canonical_label(spec.unit), canonical=True, factor=1.0,
                             offset=0.0, note="Stored as entered.", source="", aliases=list(spec.unit_aliases))
    return [canonical] + [
        EntryUnitOut(unit=c.unit, label=c.label, canonical=False, factor=c.factor, offset=c.offset, note=c.note,
                     source=c.source, aliases=list(c.aliases))
        for c in conversions_for(spec.code)
    ]


def _conversion(m: Measurement) -> EntryConversionOut | None:
    if m.original_value is None or m.original_unit is None:
        return None
    conv = find_conversion(m.metric_code, m.original_unit)
    return EntryConversionOut(
        entered_value=m.original_value,
        entered_unit=m.original_unit,
        entered_label=conv.label if conv else m.original_unit,
        canonical_value=m.value,
        canonical_unit=m.unit,
        canonical_label=canonical_label(m.unit),
        formula=conv.note if conv else "",
        source=conv.source if conv else "",
    )


def _row(m: Measurement, source_name: str, alert_id: int | None) -> ManualEntryRowOut:
    spec = get_spec(m.metric_code)
    return ManualEntryRowOut(
        id=m.id,
        astronaut_id=m.astronaut_id,
        metric_code=m.metric_code,
        display_name=spec.display_name if spec else m.metric_code,
        value=m.value,
        unit=m.unit,
        unit_label=canonical_label(m.unit),
        conversion=_conversion(m),
        context=m.context,
        recorded_at=m.recorded_at,
        ingested_at=m.ingested_at,
        source_name=source_name,
        is_simulated=m.is_simulated,
        quality=m.quality,
        accepted=m.quality != DataQuality.REJECTED.value,
        confidence=m.confidence,
        validation_notes=list(m.validation_notes or []),
        assessment_status=m.assessment_status,
        severity=m.severity,
        deviation_score=m.deviation_score,
        alert_id=alert_id,
    )


def _entry_out(db: Session, r: IngestResult, threshold_label: str) -> ManualEntryOut:
    m, a = r.measurement, r.assessment
    alert = db.get(Alert, r.alert_id) if r.alert_id is not None else None
    return ManualEntryOut(
        accepted=m.quality != DataQuality.REJECTED.value,
        entry=_row(m, m.source.name, r.alert_id),
        assessment=EntryAssessmentOut(
            status=a.status.value,
            severity=a.severity.value if a.severity else None,
            instant_severity=a.instant_severity.value if a.instant_severity else None,
            z=a.z,
            direction=a.direction,
            persistence_count=a.persistence_count,
            baseline_profile_id=a.baseline_profile_id,
            rule_id=a.rule_id,
            reasons=a.reasons,
        ),
        alert=EntryAlertOut.model_validate(alert) if alert else None,
        baseline_event=BaselineEventOut(**r.baseline_event.__dict__) if r.baseline_event else None,
        threshold_label=threshold_label,
    )


def _mission_out(m: Mission, current_id: int | None) -> MissionDetailOut:
    return MissionDetailOut(
        id=m.id,
        name=m.name,
        astronaut_id=m.astronaut_id,
        start_date=m.start_date,
        planned_days=m.planned_days,
        environment=m.environment,
        environment_label=ENVIRONMENTS.get(m.environment, (m.environment, ""))[0],
        is_simulated=m.is_simulated,
        created_at=m.created_at,
        is_current=m.id == current_id,
    )


# ---------------------------------------------------------------- entry
@router.get("/entry-spec", response_model=EntrySpecOut)
@signed_in
def entry_spec(ctx: CTX) -> EntrySpecOut:
    """Everything a manual-entry form needs, taken from the registry the ingest pipeline uses."""
    categories = list(dict.fromkeys(m.category for m in METRICS))
    return EntrySpecOut(
        metrics=[
            EntryMetricOut(
                code=m.code,
                display_name=m.display_name,
                category=m.category,
                category_label=CATEGORY_LABELS.get(m.category, m.category),
                unit=m.unit,
                unit_label=canonical_label(m.unit),
                accepted_units=_units(m),
                contexts=list(m.allowed_contexts),
                plausible_min=m.plausible_min,
                plausible_max=m.plausible_max,
                sanity_note=m.sanity_note,
                bound_note=m.bound_note,
                baseline_enabled=m.baseline_enabled,
                alert_rule_id=rule.rule_id if (rule := ctx.thresholds.rule_for(m.code)) else None,
            )
            for m in METRICS
        ],
        categories=[EntryChoiceOut(code=c, label=CATEGORY_LABELS.get(c, c)) for c in categories],
        contexts=[_context_choice(c.value) for c in MeasurementContext],
        default_source_name=DEFAULT_SOURCE_NAME,
        max_future_minutes=MAX_FUTURE_SKEW.total_seconds() / 60,
        degraded_confidence_below=DEGRADED_CONFIDENCE,
        degraded_signal_quality=list(DEGRADED_SIGNAL_QUALITY),
        threshold_label=ctx.thresholds.label,
    )


@router.post("/manual-entries", response_model=ManualEntryOut, status_code=201)
def create_manual_entry(body: ManualEntryIn, db: DB, ctx: CTX) -> ManualEntryOut:
    """Record one reading typed in by a person (source kind `manual`).

    A reading that fails validation is still stored, marked rejected with its reasons and
    audited, exactly as for sensor data: the response then says why.
    """
    svc = IngestService(db, ctx.thresholds, ctx.policy)
    inp = MeasurementInput(
        astronaut_id=body.astronaut_id,
        metric_code=body.metric_code,
        value=body.value,
        unit=body.unit,
        context=body.context,
        recorded_at=body.recorded_at or utcnow(),
        source_name=body.source_name.strip() or DEFAULT_SOURCE_NAME,
        source_kind=SourceKind.MANUAL,
        confidence=body.confidence,
        signal_quality=(body.signal_quality or "").strip() or None,
    )
    try:
        result = svc.ingest(inp)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _entry_out(db, result, ctx.thresholds.label)


@router.get("/manual-entries", response_model=list[ManualEntryRowOut])
def list_manual_entries(
    db: DB, astronaut_id: int, limit: Annotated[int, Query(ge=1, le=200)] = 10
) -> list[ManualEntryRowOut]:
    """Most recently entered manual readings first, rejected ones included (with reasons)."""
    _astronaut(db, astronaut_id)
    rows = db.execute(
        select(Measurement, DataSource.name)
        .join(DataSource, Measurement.source_id == DataSource.id)
        .where(Measurement.astronaut_id == astronaut_id, DataSource.kind == SourceKind.MANUAL.value)
        .order_by(Measurement.id.desc())
        .limit(limit)
    ).all()
    ids = [m.id for m, _ in rows]
    alert_of: dict[int, int] = {}
    if ids:
        evidence = select(AlertEvidence.measurement_id, AlertEvidence.alert_id).where(
            AlertEvidence.measurement_id.in_(ids))
        alert_of = {mid: aid for mid, aid in db.execute(evidence)}
    return [_row(m, name, alert_of.get(m.id)) for m, name in rows]


# ---------------------------------------------------------------- radiation
@router.get("/radiation/cumulative", response_model=RadiationCumulativeOut)
def radiation_cumulative(db: DB, astronaut_id: int) -> RadiationCumulativeOut:
    """Daily and running sums of logged dosimeter doses.

    Arithmetic only: AM records what the dosimeter reported and adds it up. It does not
    compare doses with any exposure limit.
    """
    _astronaut(db, astronaut_id)
    mission = _current_mission(db, astronaut_id)
    start = mission.start_date if mission else None
    rows = db.execute(
        select(Measurement.value, Measurement.recorded_at, Measurement.is_simulated)
        .where(Measurement.astronaut_id == astronaut_id, Measurement.metric_code == RADIATION_METRIC,
               Measurement.context == MeasurementContext.DAILY.value,
               Measurement.quality != DataQuality.REJECTED.value)
        .order_by(Measurement.recorded_at, Measurement.id)
    ).all()

    by_day: dict[date, list[tuple[float, bool]]] = {}
    for value, recorded_at, simulated in rows:
        by_day.setdefault(recorded_at.date(), []).append((value, simulated))
    days: list[RadiationDayOut] = []
    running = 0.0
    for d in sorted(by_day):
        items = by_day[d]
        dose = sum(v for v, _ in items)
        running += dose
        mission_day = (d - start).days + 1 if start else None
        days.append(RadiationDayOut(
            utc_date=d,
            mission_day=mission_day if mission_day is not None and mission_day >= 1 else None,
            dose=round(dose, DOSE_DECIMALS),
            entries=len(items),
            cumulative=round(running, DOSE_DECIMALS),
            simulated=any(s for _, s in items),
        ))
    return RadiationCumulativeOut(
        astronaut_id=astronaut_id,
        unit=RADIATION_UNIT,
        days=days,
        total=round(running, DOSE_DECIMALS),
        entries=len(rows),
        mission_start=start,
        simulated=any(s for _, _, s in rows),
        note=RADIATION_NOTE,
    )


# ---------------------------------------------------------------- missions
@router.get("/missions/environments", response_model=list[MissionEnvironmentOut])
@signed_in
def mission_environments() -> list[MissionEnvironmentOut]:
    return [MissionEnvironmentOut(code=code, label=label, description=desc)
            for code, (label, desc) in ENVIRONMENTS.items()]


@router.get("/missions", response_model=list[MissionDetailOut])
def list_missions(db: DB, astronaut_id: int) -> list[MissionDetailOut]:
    """An astronaut's missions, newest (current) first, with their environment."""
    _astronaut(db, astronaut_id)
    missions = db.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                          .order_by(Mission.id.desc())).all()
    current_id = missions[0].id if missions else None
    return [_mission_out(m, current_id) for m in missions]


@router.post("/missions", response_model=MissionDetailOut, status_code=201)
@doctor_only
def create_mission(body: MissionCreateIn, db: DB) -> MissionDetailOut:
    """Create a mission; it becomes the astronaut's current mission (day 1 = start date, UTC).

    A mission on a simulated crew record is marked simulated too, so generated and real
    records never mix.
    """
    astronaut = _astronaut(db, body.astronaut_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank")
    if body.start_date > utcnow().date():
        # The newest mission becomes the current one, so a future start would give
        # mission day 0 or less to every view that counts days.
        raise HTTPException(422, "start_date is in the future; AM counts mission days from the start date (UTC)")
    mission = Mission(
        name=name,
        astronaut_id=astronaut.id,
        start_date=body.start_date,
        planned_days=body.planned_days,
        environment=body.environment,
        is_simulated=astronaut.is_simulated,
    )
    db.add(mission)
    db.flush()
    audit(db, actor=body.actor, action="mission.created", entity="mission", entity_id=mission.id,
          payload={"astronaut_id": astronaut.id, "name": mission.name, "start_date": body.start_date.isoformat(),
                   "planned_days": body.planned_days, "environment": body.environment})
    db.commit()
    return _mission_out(mission, mission.id)
