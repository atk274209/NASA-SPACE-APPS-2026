import numpy as np
import pytest

from am.research.correlation import CorrelationParams, analyse, bh_adjust
from am.research.series import DailySeries


def series(key, values, unit="u"):
    s = DailySeries(key, key, unit, "measurement", "daily median")
    s.values = {d + 1: float(v) for d, v in enumerate(values)}
    return s


def test_bh_adjust_matches_reference():
    # reference: R p.adjust(c(0.01, 0.04, 0.03, 0.2), "BH") -> 0.04 0.0533 0.0533 0.2
    q = bh_adjust([0.01, 0.04, 0.03, 0.2])
    assert q == pytest.approx([0.04, 0.0533333, 0.0533333, 0.2], rel=1e-4)
    assert np.isnan(bh_adjust([float("nan"), 0.5])[0])


def test_independent_noise_yields_no_findings():
    rng = np.random.default_rng(0)
    s = {k: series(k, rng.normal(0, 1, 30)) for k in ("a:x", "b:x", "c:x")}
    findings, n_tests = analyse(s)
    assert n_tests > 10
    assert findings == []


def test_lagged_association_is_found_and_survives_detrending():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 40)
    y = np.r_[rng.normal(0, 1, 2), x[:-2]] * 2 + rng.normal(0, 0.3, 40)  # y follows x by 2 days
    findings, _ = analyse({"x:rest": series("x:rest", x), "y:rest": series("y:rest", y)})
    pair = [f for f in findings if f.finding_type != "trend"]
    assert len(pair) == 1
    f = pair[0]
    assert f.finding_type == "temporal_association" and f.lag_days == 2 and f.x_key == "x:rest"
    assert f.rho > 0.8 and f.detrended_supported is True
    assert "2 day(s) later" in f.summary()


def test_two_unrelated_trends_are_flagged_as_shared_trend_only():
    rng = np.random.default_rng(2)
    d = np.arange(30)
    a = 70 + 0.5 * d + rng.normal(0, 1, 30)
    b = 120 - 2.0 * d + rng.normal(0, 4, 30)
    findings, _ = analyse({"hr:rest": series("hr:rest", a), "ex:daily": series("ex:daily", b)})
    trends = {f.y_key for f in findings if f.finding_type == "trend"}
    assert trends == {"hr:rest", "ex:daily"}
    pair = [f for f in findings if f.finding_type != "trend"]
    assert pair and all(f.detrended_supported is False for f in pair)
    assert "NOT present after removing shared trends" in pair[0].summary()


def test_short_series_are_skipped():
    findings, n = analyse({"a:x": series("a:x", range(5)), "b:x": series("b:x", range(5))},
                          CorrelationParams(min_days=8))
    assert findings == [] and n == 0
