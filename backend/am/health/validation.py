"""Measurement validation and data-quality assignment (pure functions)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from am.db.enums import DataQuality
from am.health.metrics import MetricSpec, normalize_unit

MAX_FUTURE_SKEW = timedelta(minutes=5)
DEGRADED_CONFIDENCE = 0.5  # source-reported confidence below this marks data DEGRADED


@dataclass
class ValidationResult:
    quality: DataQuality
    unit: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.quality != DataQuality.REJECTED


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
        return ValidationResult(DataQuality.REJECTED, None, ["unknown_metric"])

    canonical = normalize_unit(spec, unit)
    if canonical is None:
        notes.append(f"unit_mismatch: got '{unit}', expected '{spec.unit}'")
    if value is None or not math.isfinite(value):
        notes.append("non_finite_value")
    else:
        if spec.plausible_min is not None and value < spec.plausible_min:
            notes.append(f"below_sanity_bound: {value} < {spec.plausible_min}")
        if spec.plausible_max is not None and value > spec.plausible_max:
            notes.append(f"above_sanity_bound: {value} > {spec.plausible_max}")
    if context not in spec.allowed_contexts:
        notes.append(f"context_not_allowed: '{context}' not in {spec.allowed_contexts}")
    if recorded_at - now > MAX_FUTURE_SKEW:
        notes.append("timestamp_in_future")
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        notes.append("confidence_out_of_range")

    if notes:
        return ValidationResult(DataQuality.REJECTED, canonical, notes)

    quality = DataQuality.GOOD
    if signal_quality is not None and signal_quality.lower() in {"degraded", "poor", "low"}:
        quality = DataQuality.DEGRADED
        notes.append(f"source_signal_quality: {signal_quality}")
    if confidence is not None and confidence < DEGRADED_CONFIDENCE:
        quality = DataQuality.DEGRADED
        notes.append(f"low_confidence: {confidence}")
    return ValidationResult(quality, canonical, notes)
