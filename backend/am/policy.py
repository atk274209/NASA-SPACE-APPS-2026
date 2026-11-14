"""Typed loaders for the YAML safety configuration (thresholds + baseline policy)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from am.db.enums import Severity


class Persistence(BaseModel):
    lookback: int = 6
    max_age_hours: float = 6.0
    required: dict[Severity, int] = Field(default_factory=lambda: {Severity.WARNING: 2, Severity.CRITICAL: 3})


class MetricRule(BaseModel):
    rule_id: str
    direction: Literal["both", "high", "low"] = "both"
    bands: dict[Severity, float]
    persistence: Persistence
    degraded_quality_cap: Severity = Severity.WATCH
    min_scale_fraction: float = 0.02

    @model_validator(mode="after")
    def _check_bands(self) -> "MetricRule":
        w, wa, c = (self.bands[s] for s in (Severity.WATCH, Severity.WARNING, Severity.CRITICAL))
        if not (0 < w < wa < c):
            raise ValueError(f"bands must be strictly increasing for {self.rule_id}")
        return self


class ThresholdConfig(BaseModel):
    version: str
    label: str
    rules: dict[str, MetricRule]

    def rule_for(self, metric_code: str) -> MetricRule | None:
        return self.rules.get(metric_code)


def load_thresholds(path: Path) -> ThresholdConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    rules: dict[str, MetricRule] = {}
    for code, override in (raw.get("metrics") or {}).items():
        merged = {**defaults, **(override or {})}
        merged.setdefault("rule_id", f"{code}_personal_deviation")
        rules[code] = MetricRule.model_validate(merged)
    return ThresholdConfig(version=str(raw["version"]), label=str(raw["label"]), rules=rules)


class StageRequirement(BaseModel):
    min_samples: int
    min_span_hours: float


class Adaptation(BaseModel):
    update_every_n_new_normal: int = 50
    window_days: float = 14
    max_median_shift_scale: float = 0.5
    drift_freeze_scale: float = 1.0
    max_cumulative_shift_scale: float = 2.0
    freeze_on_active_alert_severity: Severity = Severity.WARNING


class BaselinePolicy(BaseModel):
    version: str
    provisional: StageRequirement
    established: StageRequirement
    adaptation: Adaptation


def load_baseline_policy(path: Path) -> BaselinePolicy:
    return BaselinePolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
