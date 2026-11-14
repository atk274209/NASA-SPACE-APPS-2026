"""Historical correlation engine (deterministic statistics, no LLM).

For one astronaut's daily series:
  * TREND: Spearman rho between mission day and value + Theil-Sen slope.
  * ASSOCIATION: Spearman rho between series X (shifted by lag 0..max_lag days, X leading)
    and series Y, computed on raw values AND on first differences. Shared monotonic
    trends make raw correlations look strong; an association that disappears after
    differencing is labelled 'shared_trend_only'.
  * Benjamini-Hochberg FDR control across all tests of a run.

Evidence levels used (never causal):
  trend                  monotonic change over mission days
  correlation            same-day association
  temporal_association   X precedes Y by lag >= 1 day
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np
from scipy.stats import spearmanr, theilslopes

from am.research.series import DailySeries

CAVEAT = ("Statistical association in a single astronaut's observational data. It does not establish "
          "causation and may reflect shared trends, confounders or chance.")


@dataclass
class CorrelationParams:
    min_days: int = 8
    max_lag: int = 3
    fdr_q: float = 0.10
    min_abs_rho: float = 0.5
    detrended_q: float = 0.05  # stricter FDR level for the detrended confirmation

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Finding:
    finding_type: str  # trend | correlation | temporal_association
    x_key: str | None
    y_key: str
    x_label: str | None
    y_label: str
    lag_days: int
    n: int
    rho: float
    p: float
    q: float = 1.0
    rho_diff: float | None = None
    p_diff: float | None = None
    detrended_supported: bool | None = None
    slope_per_day: float | None = None
    unit: str = ""
    days: list[int] = field(default_factory=list)

    @property
    def significant(self) -> bool:
        return True  # set by filter

    def summary(self) -> str:
        dir_word = "increased" if self.rho > 0 else "decreased"
        if self.finding_type == "trend":
            return (f"{self.y_label} {dir_word} over mission days {self.days[0]}–{self.days[-1]} "
                    f"(Spearman rho {self.rho:+.2f}, Theil-Sen slope {self.slope_per_day:+.3g} {self.unit}/day, "
                    f"n={self.n} days, FDR q={self.q:.3f}).")
        rel = "moved in the same direction as" if self.rho > 0 else "moved opposite to"
        when = "on the same day" if self.lag_days == 0 else f"{self.lag_days} day(s) later"
        robust = ("Association persists after removing shared trends (first differences, "
                  f"rho {self.rho_diff:+.2f})." if self.detrended_supported
                  else "Association is NOT present after removing shared trends; it may only reflect two "
                       "parallel trends.")
        return (f"{self.y_label} {rel} {self.x_label} {when} (Spearman rho {self.rho:+.2f}, n={self.n} days, "
                f"FDR q={self.q:.3f}). {robust}")


def _spearman(a: list[float], b: list[float]) -> tuple[float, float]:
    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan"), float("nan")
    r = spearmanr(a, b)
    return float(r.statistic), float(r.pvalue)


def bh_adjust(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values); NaN p-values stay NaN."""
    idx = [i for i, p in enumerate(pvals) if not math.isnan(p)]
    m = len(idx)
    out = [float("nan")] * len(pvals)
    if not m:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvals[i] * m / rank)
        out[i] = running
    return out


def analyse(series: dict[str, DailySeries], params: CorrelationParams | None = None) -> tuple[list[Finding], int]:
    """Return (significant findings, number of tests performed)."""
    p = params or CorrelationParams()
    usable = {k: s for k, s in series.items() if len(s.values) >= p.min_days}
    tests: list[Finding] = []

    for s in usable.values():
        days = s.days()
        vals = [s.values[d] for d in days]
        rho, pv = _spearman([float(d) for d in days], vals)
        if math.isnan(rho):
            continue
        slope = float(theilslopes(vals, days).slope)
        tests.append(Finding("trend", None, s.key, None, s.label, 0, len(days), rho, pv, slope_per_day=slope,
                             unit=s.unit, days=days))

    for a, b in combinations(sorted(usable), 2):
        for x_key, y_key in ((a, b), (b, a)):
            x, y = usable[x_key], usable[y_key]
            for lag in range(0, p.max_lag + 1):
                if lag == 0 and x_key > y_key:
                    continue  # same-day test is symmetric; run once
                days = [d for d in y.days() if (d - lag) in x.values]
                if len(days) < p.min_days:
                    continue
                xs = [x.values[d - lag] for d in days]
                ys = [y.values[d] for d in days]
                rho, pv = _spearman(xs, ys)
                if math.isnan(rho):
                    continue
                # first differences over consecutive days only
                dx, dy = [], []
                for i in range(1, len(days)):
                    if days[i] - days[i - 1] == 1:
                        dx.append(xs[i] - xs[i - 1])
                        dy.append(ys[i] - ys[i - 1])
                rd, pd = _spearman(dx, dy) if len(dx) >= p.min_days - 1 else (float("nan"), float("nan"))
                tests.append(Finding("correlation" if lag == 0 else "temporal_association", x_key, y_key, x.label,
                                     y.label, lag, len(days), rho, pv, rho_diff=None if math.isnan(rd) else rd,
                                     p_diff=None if math.isnan(pd) else pd, unit=y.unit, days=days))

    qs = bh_adjust([t.p for t in tests])
    for t, q in zip(tests, qs, strict=True):
        t.q = q
    sig = [t for t in tests if not math.isnan(t.q) and t.q <= p.fdr_q and abs(t.rho) >= p.min_abs_rho]
    # Detrended confirmation, itself FDR-controlled across all candidate pairs/lags.
    pairs = [t for t in sig if t.finding_type != "trend"]
    q_diff = bh_adjust([t.p_diff if t.p_diff is not None else float("nan") for t in pairs])
    for t, qd in zip(pairs, q_diff, strict=True):
        t.detrended_supported = bool(not math.isnan(qd) and qd <= p.detrended_q and t.rho_diff is not None
                                     and np.sign(t.rho_diff) == np.sign(t.rho))
    # keep only the strongest lag per ordered pair to avoid near-duplicate findings
    best: dict[tuple, Finding] = {}
    for t in sig:
        k = (t.finding_type == "trend", t.x_key, t.y_key) if t.finding_type == "trend" else \
            ("pair", *sorted((t.x_key or "", t.y_key)))
        if k not in best or t.q < best[k].q:
            best[k] = t
    ordered = sorted(best.values(), key=lambda t: (t.finding_type != "trend", t.q))
    return ordered, len(tests)
