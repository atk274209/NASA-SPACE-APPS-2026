"""Mission-start reference period for descriptive day-by-day comparisons.

Each daily series is compared with its own values on mission days 1..REFERENCE_DAYS.
The reference is fixed, so slow deterioration later in the mission cannot shift it.
These comparisons are DESCRIPTIVE (journal, missions); they never raise alerts.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from am.baseline.stats import MAD_TO_SD, P90_RANGE_TO_SD
from am.research.series import DailySeries

REFERENCE_DAYS = 7
MIN_REFERENCE_DAYS = 5
DESCRIPTIVE_BAND = 1.5  # |z| below this is "within reference" (descriptive label only)


@dataclass(frozen=True)
class Reference:
    median: float
    scale: float
    n_days: int
    days: tuple[int, int]

    def z(self, value: float) -> float:
        return (value - self.median) / self.scale

    def label(self, value: float) -> str:
        z = self.z(value)
        if z <= -DESCRIPTIVE_BAND:
            return "below reference"
        if z >= DESCRIPTIVE_BAND:
            return "above reference"
        return "within reference"


def reference_for(s: DailySeries) -> Reference | None:
    days = [d for d in s.days() if d <= REFERENCE_DAYS]
    if len(days) < MIN_REFERENCE_DAYS:
        return None
    vals = sorted(s.values[d] for d in days)
    med = float(median(vals))
    mad = float(median(abs(v - med) for v in vals))
    rng = (vals[-1] - vals[0]) / P90_RANGE_TO_SD  # few points: full range as a conservative spread estimate
    floor = max(abs(med) * 0.02, 1e-6)
    return Reference(med, max(MAD_TO_SD * mad, rng, floor), len(days), (days[0], days[-1]))
