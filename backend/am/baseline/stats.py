"""Descriptive and robust statistics for personal baselines (pure functions)."""

from __future__ import annotations

import math

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.stats import chi2

MAD_TO_SD = 1.4826  # consistency constant: 1.4826*MAD estimates SD for normal data
P90_RANGE_TO_SD = 3.29  # (p95 - p05) spans ~3.29 SD for normal data


@dataclass(frozen=True)
class SampleStats:
    n: int
    mean: float
    median: float
    sd: float
    mad: float
    p05: float
    p95: float
    min: float
    max: float
    trend_per_day: float | None
    window_start: datetime
    window_end: datetime

    @property
    def span_hours(self) -> float:
        return (self.window_end - self.window_start).total_seconds() / 3600.0


def compute_stats(values: list[float], times: list[datetime]) -> SampleStats:
    if not values or len(values) != len(times):
        raise ValueError("values and times must be non-empty and equal length")
    arr = np.asarray(values, dtype=float)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    sd = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0
    t0 = min(times)
    trend = None
    if arr.size >= 3:
        days = np.asarray([(t - t0).total_seconds() / 86400.0 for t in times])
        if np.ptp(days) > 0:
            trend = float(np.polyfit(days, arr, 1)[0])
    return SampleStats(
        n=int(arr.size),
        mean=float(arr.mean()),
        median=median,
        sd=sd,
        mad=mad,
        p05=float(np.percentile(arr, 5)),
        p95=float(np.percentile(arr, 95)),
        min=float(arr.min()),
        max=float(arr.max()),
        trend_per_day=trend,
        window_start=t0,
        window_end=max(times),
    )


def robust_scale(median: float, mad: float, sd: float, min_scale_fraction: float) -> float:
    """Scale used for deviation scoring, floored to avoid near-zero denominators."""
    floor = abs(median) * min_scale_fraction
    scale = MAD_TO_SD * mad
    if scale <= 0:
        scale = sd
    return max(scale, floor, 1e-9)


def profile_scale(
    median: float,
    mad: float,
    sd: float,
    min_scale_fraction: float,
    provisional: bool,
    p05: float | None = None,
    p95: float | None = None,
    n: int | None = None,
) -> float:
    """Scale for a stored profile.

    * MAD collapses on quantized data (e.g. integer bpm: MAD=1 while SD~2.5), so the
      central-90% range estimator (p95 - p05) / 3.29 is also considered; it is robust to
      the 5% most extreme values on each side and insensitive to quantization.
    * Provisional baselines come from few samples, so their spread and centre are
      uncertain. Their scale is a predictive one: the one-sided 95% upper confidence
      bound of the SD (chi-square) combined with the standard error of the median
      (~1.2533 * SD / sqrt(n)).
    The largest estimate is used (conservative: fewer false alarms from a too-tight scale).
    """
    scale = robust_scale(median, mad, sd, min_scale_fraction)
    if p05 is not None and p95 is not None and p95 > p05:
        scale = max(scale, (p95 - p05) / P90_RANGE_TO_SD)
    if provisional and n is not None and n >= 2:
        base = max(scale, sd)
        sd_ucb = base * math.sqrt((n - 1) / chi2.ppf(0.05, n - 1))
        se_median = 1.2533 * base / math.sqrt(n)
        scale = math.sqrt(sd_ucb**2 + se_median**2)
    return scale


def robust_z(value: float, median: float, scale: float) -> float:
    return (value - median) / scale
