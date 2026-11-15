"""Safety envelope (config/cabin_envelope.demo.yaml): SIMULATED DEMONSTRATION LIMITS."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from am.config import REPO_ROOT

ENVELOPE_PATH = REPO_ROOT / "config" / "cabin_envelope.demo.yaml"
VARIABLES = ("temperature", "lighting", "pressure")


@dataclass(frozen=True)
class VarLimits:
    name: str
    unit: str
    scope: str  # zone | cabin
    default: float
    min: float
    max: float
    step: float
    max_change_per_hour: float
    noise: float
    verify_tolerance: float
    autonomous_min: float | None = None
    autonomous_max: float | None = None

    @property
    def auto_min(self) -> float:
        return self.autonomous_min if self.autonomous_min is not None else self.min

    @property
    def auto_max(self) -> float:
        return self.autonomous_max if self.autonomous_max is not None else self.max


@dataclass(frozen=True)
class Envelope:
    version: str
    label: str
    variables: dict[str, VarLimits]
    contexts: dict[str, list[int]]
    unresolved_feedback_count: int
    unresolved_window_hours: int

    def context_for_hour(self, hour: int) -> str:
        s0, s1 = self.contexts["sleep"]
        return "sleep" if (hour >= s0 or hour < s1) else "work"


@lru_cache
def load_envelope() -> Envelope:
    d: dict[str, Any] = yaml.safe_load(ENVELOPE_PATH.read_text(encoding="utf-8"))
    vs = {}
    for name in VARIABLES:
        v = d["variables"][name]
        lim = VarLimits(name=name, **v)
        if not (lim.min <= lim.auto_min <= lim.default <= lim.auto_max <= lim.max) or lim.step <= 0:
            raise ValueError(f"cabin envelope for {name} is inconsistent")
        vs[name] = lim
    return Envelope(version=d["version"], label=d["label"], variables=vs, contexts=d["contexts"],
                    unresolved_feedback_count=d["unresolved_feedback_count"],
                    unresolved_window_hours=d["unresolved_window_hours"])
