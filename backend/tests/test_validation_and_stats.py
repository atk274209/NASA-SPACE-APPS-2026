from datetime import datetime, timedelta

import pytest

from am.baseline.stats import compute_stats, robust_scale
from am.db.enums import DataQuality
from am.health.metrics import get_spec
from am.health.validation import validate_measurement

NOW = datetime(2026, 11, 14, 12)
HR = get_spec("heart_rate")


def v(value=70.0, unit="bpm", context="rest", t=NOW, **kw):
    return validate_measurement(HR, value=value, unit=unit, context=context, recorded_at=t, now=NOW, **kw)


def test_valid_measurement_is_good():
    r = v()
    assert r.quality == DataQuality.GOOD and r.accepted and r.unit == "bpm"


def test_unit_alias_normalized():
    assert v(unit="BPM").unit == "bpm"


@pytest.mark.parametrize(
    "kwargs, note",
    [
        ({"unit": "mmHg"}, "unit_mismatch"),
        ({"value": 900}, "above_sanity_bound"),
        ({"value": 0}, "below_sanity_bound"),
        ({"value": float("nan")}, "non_finite_value"),
        ({"context": "daily"}, "context_not_allowed"),
        ({"t": NOW + timedelta(hours=1)}, "timestamp_in_future"),
        ({"confidence": 1.5}, "confidence_out_of_range"),
    ],
)
def test_rejections(kwargs, note):
    r = v(**kwargs)
    assert r.quality == DataQuality.REJECTED
    assert any(n.startswith(note) for n in r.notes)


def test_low_confidence_or_poor_signal_is_degraded():
    assert v(confidence=0.3).quality == DataQuality.DEGRADED
    assert v(signal_quality="poor").quality == DataQuality.DEGRADED


def test_unknown_metric_rejected():
    r = validate_measurement(None, value=1, unit="x", context="rest", recorded_at=NOW, now=NOW)
    assert r.quality == DataQuality.REJECTED


def test_compute_stats():
    times = [NOW + timedelta(hours=i) for i in range(5)]
    st = compute_stats([60, 62, 64, 66, 100], times)
    assert st.median == 64
    assert st.mad == 2
    assert st.min == 60 and st.max == 100
    assert st.span_hours == 4
    assert st.trend_per_day is not None and st.trend_per_day > 0


def test_robust_scale_floor():
    # constant data -> MAD and SD are 0 -> floored at |median| * fraction
    assert robust_scale(70.0, 0.0, 0.0, 0.02) == pytest.approx(1.4)
    assert robust_scale(70.0, 2.0, 5.0, 0.02) == pytest.approx(2.9652)
