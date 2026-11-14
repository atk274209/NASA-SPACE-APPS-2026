"""Measurement validation and data-quality assignment (pure functions).

The entered unit is converted to the canonical unit first (see `am.health.units`), so
every check below runs on the canonical value. An unknown unit is rejected, never guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from am.db.enums import DataQuality
from am.health.metrics import MetricSpec
from am.health.units import Canonical, accepted_unit_codes, to_canonical

MAX_FUTURE_SKEW = timedelta(minutes=5)
DEGRADED_CONFIDENCE = 0.5  # source-reported confidence below this marks data DEGRADED
DEGRADED_SIGNAL_QUALITY = ("degraded", "poor", "low")  # source labels that mark data DEGRADED


@dataclass
class ValidationResult:
    quality: DataQuality
    unit: str | None
    notes: list[str] = field(default_factory=list)
    # The value the checks ran on: the canonical value when the unit was accepted, else the
    # value as entered (it is rejected in that case).
    value: float | None = None
    # Set only when the entered unit was converted; carries the value and unit as entered.
    canonical: Canonical | None = None

    @property
    def accepted(self) -> bool:
        return self.quality != DataQuality.REJECTED

    @property
    def original_value(self) -> float | None:
        return self.canonical.original_value if self.canonical else None

    @property
    def original_unit(self) -> str | None:
        return self.canonical.original_unit if self.canonical else None


def _fmt(x: float) -> str:
    return f"{x:g}"


def validate_measurement(
    spec: MetricSpec | None,
    *,
    value: float,
    unit: str,
    context: str,
    recorded_at: datetime,
    now: datetime,
    confidence: float | None = None,
    signal_quality: str | None = None,
) -> ValidationResult:
    notes: list[str] = []
    if spec is None:
        return ValidationResult(DataQuality.REJECTED, None, ["unknown_metric"], value=value)

    if value is None:  # treat a missing number like NaN: rejected below, never converted
        value = math.nan
    canonical = to_canonical(spec, value, unit)
    converted = canonical if canonical is not None and canonical.conversion is not None else None
    checked = canonical.value if canonical is not None else value
    if canonical is None:
        notes.append(f"unit_mismatch: got '{unit}', expected one of {accepted_unit_codes(spec)}")
    if not math.isfinite(checked):
        notes.append("non_finite_value")
    else:
        # Bounds are checked on the canonical value, so a converted entry is judged exactly
        # like one typed in the canonical unit.
        if spec.plausible_min is not None and checked < spec.plausible_min:
            notes.append(f"below_sanity_bound: {_fmt(checked)} {spec.unit} < {_fmt(spec.plausible_min)} {spec.unit}")
        if spec.plausible_max is not None and checked > spec.plausible_max:
            notes.append(f"above_sanity_bound: {_fmt(checked)} {spec.unit} > {_fmt(spec.plausible_max)} {spec.unit}")
    if context not in spec.allowed_contexts:
        notes.append(f"context_not_allowed: '{context}' not in {spec.allowed_contexts}")
    if recorded_at - now > MAX_FUTURE_SKEW:
        notes.append("timestamp_in_future")
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        notes.append("confidence_out_of_range")

    unit_out = canonical.unit if canonical is not None else None
    if notes:
        return ValidationResult(DataQuality.REJECTED, unit_out, notes, value=checked, canonical=converted)

    quality = DataQuality.GOOD
    if signal_quality is not None and signal_quality.lower() in DEGRADED_SIGNAL_QUALITY:
        quality = DataQuality.DEGRADED
        notes.append(f"source_signal_quality: {signal_quality}")
    if confidence is not None and confidence < DEGRADED_CONFIDENCE:
        quality = DataQuality.DEGRADED
        notes.append(f"low_confidence: {confidence}")
    return ValidationResult(quality, unit_out, notes, value=checked, canonical=converted)
