"""Metric registry.

plausible_min / plausible_max are deliberately WIDE engineering sanity bounds used to
catch sensor faults and data-entry errors (e.g. heart rate 0 or 900). They are not
clinical normal ranges and are never used for alert classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from am.db.enums import MeasurementContext as C
from am.db.models import MetricDefinition

SANITY_NOTE = "AMC engineering sanity bound for error rejection; NOT a clinical reference range."

PHYSIO = [C.REST.value, C.EXERCISE.value, C.SLEEP.value, C.OTHER.value]
GLUCOSE_CONTEXTS = [C.FASTING.value, C.POSTPRANDIAL.value, C.OTHER.value]

# Plain-language labels for grouping indicators in entry forms.
CATEGORY_LABELS = {
    "cardiovascular": "Cardiovascular",
    "immune": "Immune and temperature",
    "metabolic": "Metabolic",
    "musculoskeletal": "Musculoskeletal and activity",
    "sleep": "Sleep",
    "nutrition": "Nutrition and hydration",
    "radiation": "Radiation",
}

# What each measurement context means, so a reading is filed under the right baseline.
# Descriptions are deliberately qualitative: AMC does not define clinical fasting windows.
CONTEXT_LABELS: dict[str, tuple[str, str]] = {
    C.REST.value: ("Resting", "At rest, for example seated or lying quietly."),
    C.EXERCISE.value: ("Exercise", "During or right after exercise."),
    C.SLEEP.value: ("Sleep", "While asleep."),
    C.OTHER.value: ("Other", "Any other situation."),
    C.DAILY.value: ("Daily total", "One value that covers the whole day."),
    C.FASTING.value: ("Fasting", "Before eating, for example before breakfast."),
    C.POSTPRANDIAL.value: ("After a meal", "After eating."),
}


@dataclass(frozen=True)
class MetricSpec:
    code: str
    category: str
    display_name: str
    unit: str
    allowed_contexts: list[str]
    plausible_min: float | None
    plausible_max: float | None
    baseline_enabled: bool = True
    unit_aliases: tuple[str, ...] = field(default=())
    # Why this metric's sanity bounds sit where they do (appended to SANITY_NOTE).
    bound_note: str = ""

    @property
    def sanity_note(self) -> str:
        return f"{SANITY_NOTE} {self.bound_note}".strip()


METRICS: list[MetricSpec] = [
    # Cardiovascular
    MetricSpec("heart_rate", "cardiovascular", "Heart rate", "bpm", PHYSIO, 20, 250,
               unit_aliases=("BPM", "beats/min", "1/min")),
    MetricSpec("hrv_rmssd", "cardiovascular", "HRV (RMSSD)", "ms", PHYSIO, 1, 300),
    MetricSpec("systolic_bp", "cardiovascular", "Systolic blood pressure", "mmHg", PHYSIO, 50, 260,
               unit_aliases=("mmhg", "mm Hg")),
    MetricSpec("diastolic_bp", "cardiovascular", "Diastolic blood pressure", "mmHg", PHYSIO, 25, 160,
               unit_aliases=("mmhg", "mm Hg")),
    MetricSpec("spo2", "cardiovascular", "Peripheral oxygen saturation", "%", PHYSIO, 50, 100,
               unit_aliases=("percent", "pct")),
    MetricSpec("respiratory_rate", "cardiovascular", "Respiratory rate", "breaths/min", PHYSIO, 3, 70,
               unit_aliases=("brpm", "rpm")),
    MetricSpec("body_temperature", "immune", "Body temperature", "degC", PHYSIO, 30, 43,
               unit_aliases=("C", "°C", "celsius")),
    # Musculoskeletal / activity
    MetricSpec("exercise_duration", "musculoskeletal", "Exercise duration", "min", [C.DAILY.value], 0, 600,
               unit_aliases=("minutes",)),
    MetricSpec("step_count", "musculoskeletal", "Step count", "count", [C.DAILY.value], 0, 100000,
               unit_aliases=("steps",)),
    MetricSpec("body_mass", "musculoskeletal", "Body mass", "kg", [C.OTHER.value, C.DAILY.value], 30, 200),
    # Sleep
    MetricSpec("sleep_duration", "sleep", "Sleep duration", "h", [C.DAILY.value], 0, 24,
               unit_aliases=("hours", "hr")),
    # Nutrition
    MetricSpec("water_intake", "nutrition", "Water intake", "mL", [C.DAILY.value], 0, 10000,
               unit_aliases=("ml",)),
    MetricSpec("energy_intake", "nutrition", "Energy intake", "kcal", [C.DAILY.value], 0, 10000),
    # Metabolic. The bounds only catch entry errors, most often a mmol/L value typed while
    # mg/dL is selected (5.5 mmol/L stored as 5.5 mg/dL) or a mg/dL value typed while
    # mmol/L is selected (99 becomes about 1784 mg/dL). They are not a clinical range and
    # never classify a reading: deviations are judged against the astronaut's own baseline
    # by the DEMO rule in thresholds.demo.yaml.
    MetricSpec("blood_glucose", "metabolic", "Blood glucose", "mg/dL", GLUCOSE_CONTEXTS, 10, 1000,
               bound_note="Wide bounds that catch entry errors such as a mmol/L value entered as mg/dL."),
    # Radiation: personal dosimeter dose for one day. Logged and summed only, so there is
    # no personal baseline and no alert rule. The upper bound is deliberately very wide so
    # that a real reading is never rejected; it only catches gross entry errors such as a
    # misplaced decimal point. It is not an exposure limit.
    MetricSpec("radiation_dose", "radiation", "Radiation dose", "mSv", [C.DAILY.value], 0, 10000,
               baseline_enabled=False,
               bound_note="Not an exposure limit; AMC does not evaluate exposure limits."),
]

_BY_CODE = {m.code: m for m in METRICS}


def get_spec(code: str) -> MetricSpec | None:
    return _BY_CODE.get(code)


def normalize_unit(spec: MetricSpec, unit: str) -> str | None:
    """Return the canonical unit if `unit` matches it or one of its aliases, else None."""
    u = unit.strip()
    if u == spec.unit or u.lower() == spec.unit.lower():
        return spec.unit
    if u in spec.unit_aliases or u.lower() in {a.lower() for a in spec.unit_aliases}:
        return spec.unit
    return None


def seed_metric_registry(session: Session) -> None:
    """Upsert the registry into the database (idempotent)."""
    for spec in METRICS:
        row = session.get(MetricDefinition, spec.code)
        if row is None:
            row = MetricDefinition(code=spec.code)
            session.add(row)
        row.category = spec.category
        row.display_name = spec.display_name
        row.unit = spec.unit
        row.allowed_contexts = list(spec.allowed_contexts)
        row.plausible_min = spec.plausible_min
        row.plausible_max = spec.plausible_max
        row.plausibility_note = spec.sanity_note
        row.baseline_enabled = spec.baseline_enabled
    session.flush()
