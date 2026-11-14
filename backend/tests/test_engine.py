"""Acceptance tests for M1: measurement -> baseline -> anomaly -> alert."""

from datetime import timedelta

from sqlalchemy import select

from am.db.enums import AlertStatus, AssessmentStatus, BaselineStatus, Severity
from am.db.models import Alert, AuditLog, BaselineAudit, BaselineProfile
from tests.conftest import NOW, feed_baseline, hr

START = NOW - timedelta(days=60)


def _ingest(ingest, *a, **kw):
    r = ingest.ingest(hr(*a, **kw), now=NOW + timedelta(days=365))
    ingest.s.commit()
    return r


# ---------------------------------------------------------------- baseline creation
def test_three_hour_window_creates_provisional_baseline(ingest, astronaut):
    feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)  # ~3 h
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert p is not None
    assert p.status == BaselineStatus.PROVISIONAL.value
    assert p.version == 1 and p.is_anchor
    assert 68 < p.median < 72
    assert p.n == ctx_min_samples(ingest)  # created as soon as the provisional policy is met


def test_insufficient_data_creates_no_baseline(ingest, astronaut):
    feed_baseline(ingest, astronaut.id, start=START, n=10, every_min=5)
    assert ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest") is None


def test_long_window_creates_established_baseline(ingest, astronaut):
    feed_baseline(ingest, astronaut.id, start=START, n=240, every_min=20)  # 80 h
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert p.status == BaselineStatus.ESTABLISHED.value


# ---------------------------------------------------------------- anomaly + alert
def test_resting_115_against_70_baseline_escalates_with_persistence(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    baseline = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")

    gap = timedelta(hours=7)  # outside persistence lookback -> earlier noise cannot contribute
    last = last + gap
    r1 = _ingest(ingest, astronaut.id, 115, last + timedelta(minutes=10))
    assert r1.assessment.instant_severity == Severity.CRITICAL
    assert r1.assessment.severity == Severity.WATCH  # single reading: surfaced, not escalated
    assert r1.alert_id is not None

    r2 = _ingest(ingest, astronaut.id, 116, last + timedelta(minutes=20))
    assert r2.assessment.severity == Severity.WARNING
    r3 = _ingest(ingest, astronaut.id, 114, last + timedelta(minutes=30))
    assert r3.assessment.severity == Severity.CRITICAL
    assert r1.alert_id == r2.alert_id == r3.alert_id

    alert = db.get(Alert, r1.alert_id)
    assert alert.peak_severity == Severity.CRITICAL.value
    assert alert.status == AlertStatus.ACTIVE.value
    assert alert.threshold_label.startswith("DEMO")
    evidence_ids = [e.measurement_id for e in alert.evidence]
    assert evidence_ids[-3:] == [r1.measurement.id, r2.measurement.id, r3.measurement.id]
    assert alert.baseline_profile_id == baseline.id

    # baseline untouched by the abnormal readings
    assert ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest").id == baseline.id


def test_exercise_reading_not_compared_to_rest_baseline(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    r = _ingest(ingest, astronaut.id, 115, last + timedelta(minutes=10), context="exercise")
    assert r.assessment.status == AssessmentStatus.NO_BASELINE
    assert r.alert_id is None
    assert db.scalars(select(Alert).where(Alert.context == "exercise")).first() is None


def test_single_moderate_deviation_does_not_alert(ingest, astronaut):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    scale = ingest.baselines.scale_of(p)
    # between WATCH and WARNING bands, 7 h after the feed so no earlier noise is in the window
    r = _ingest(ingest, astronaut.id, round(p.median + 2.5 * scale, 1), last + timedelta(hours=7))
    assert r.assessment.instant_severity == Severity.WATCH
    assert r.assessment.severity == Severity.NORMAL
    assert r.alert_id is None


def test_degraded_quality_is_capped(ingest, astronaut):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    sev = []
    for i in range(4):
        r = _ingest(ingest, astronaut.id, 118, last + timedelta(minutes=10 * (i + 1)), confidence=0.3)
        sev.append(r.assessment.severity)
    assert max(s.rank for s in sev) == Severity.WATCH.rank


def test_direction_low_rule_ignores_high_values(ingest, astronaut):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5, mean=50, sd=5,
                         metric="hrv_rmssd", unit="ms")
    for i in range(3):
        r = _ingest(ingest, astronaut.id, 120, last + timedelta(minutes=10 * (i + 1)), metric="hrv_rmssd", unit="ms")
        assert r.assessment.severity == Severity.NORMAL


def test_rejected_measurement_is_stored_and_audited(ingest, astronaut, db):
    r = _ingest(ingest, astronaut.id, 900, START)
    assert r.measurement.quality == "rejected"
    assert r.measurement.assessment_status == AssessmentStatus.SKIPPED_REJECTED.value
    logs = db.scalars(select(AuditLog).where(AuditLog.action == "measurement.rejected")).all()
    assert len(logs) == 1 and logs[0].entity_id == str(r.measurement.id)


# ---------------------------------------------------------------- baseline safety
def test_open_alert_freezes_baseline(ingest, astronaut):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    for i in range(3):
        _ingest(ingest, astronaut.id, 115, last + timedelta(minutes=10 * (i + 1)))
    # normal data afterwards would normally trigger adaptation; open CRITICAL alert must freeze instead
    feed_baseline(ingest, astronaut.id, start=last + timedelta(hours=1), n=50, every_min=5, seed=7)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert p.is_frozen and p.version == 1
    assert "open alert" in p.frozen_reason


def test_drift_freezes_instead_of_adapting(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    scale = ingest.baselines.scale_of(p)
    shifted = p.median + 1.5 * scale  # each reading below WATCH band -> NORMAL, but collectively drifted
    feed_baseline(ingest, astronaut.id, start=last + timedelta(minutes=5), n=50, every_min=5,
                  mean=shifted, sd=0.2, seed=3)
    p2 = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert p2.id == p.id and p2.is_frozen
    assert "drift" in p2.frozen_reason
    actions = [a.action for a in db.scalars(select(BaselineAudit)).all()]
    assert actions == ["created", "frozen"]


def test_small_change_adapts_with_rate_limit_and_audit(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    scale = ingest.baselines.scale_of(p)
    feed_baseline(ingest, astronaut.id, start=last + timedelta(minutes=5), n=50, every_min=5,
                  mean=p.median + 0.5 * scale, sd=0.5, seed=4)
    p2 = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert p2.version == 2 and p2.supersedes_id == p.id and not p2.is_frozen
    assert abs(p2.median - p.median) <= 0.5 * scale + 1e-9
    assert db.get(BaselineProfile, p.id).status == BaselineStatus.SUPERSEDED.value
    assert [a.action for a in db.scalars(select(BaselineAudit)).all()] == ["created", "adapted"]


def test_slow_creep_is_bounded_by_cumulative_limit(ingest, astronaut, ctx):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    anchor = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    anchor_scale = ingest.baselines.scale_of(anchor)
    limit = ctx.policy.adaptation.max_cumulative_shift_scale
    t = last
    for k in range(25):
        cur = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
        if cur.is_frozen:
            break
        step = ingest.baselines.scale_of(cur)
        t = feed_baseline(ingest, astronaut.id, start=t + timedelta(minutes=5), n=50, every_min=5,
                          mean=cur.median + 0.8 * step, sd=0.2, seed=100 + k)
    final = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    assert final.is_frozen, "creeping baseline must eventually freeze for manual review"
    assert (final.median - anchor.median) / anchor_scale <= limit + 1e-9


def test_unfreeze_requires_frozen_and_is_audited(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    import pytest

    with pytest.raises(ValueError):
        ingest.baselines.unfreeze(p.id, "flight-surgeon", "not frozen")
    feed_baseline(ingest, astronaut.id, start=last + timedelta(minutes=5), n=50, every_min=5,
                  mean=p.median + 1.5 * ingest.baselines.scale_of(p), sd=0.2, seed=3)
    ingest.baselines.unfreeze(p.id, "flight-surgeon", "reviewed: expected adaptation")
    db.commit()
    audit = db.scalars(select(BaselineAudit).where(BaselineAudit.action == "unfrozen")).one()
    assert audit.actor == "flight-surgeon"


def ctx_min_samples(ingest) -> int:
    return ingest.baselines.policy.provisional.min_samples
