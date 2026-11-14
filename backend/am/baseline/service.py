"""Personal baseline lifecycle: creation, controlled adaptation, freezing, audit.

Safety rules (see config/baseline_policy.yaml and docs/SAFETY.md):
  * Only good-quality data is used. After a baseline exists, only data that the
    anomaly engine classified NORMAL against it is eligible for adaptation.
  * Each adaptation step may move the median by at most `max_median_shift_scale`
    robust-scale units, and cumulative movement from the anchor (version 1) is limited
    by `max_cumulative_shift_scale`. Exceeding either -> FREEZE, never silent absorb.
  * New data whose median sits beyond `drift_freeze_scale` from the current median
    freezes the baseline.
  * An active alert >= `freeze_on_active_alert_severity` freezes the baseline.
  * Unfreezing / manual rebuild requires a named actor and reason (audited).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.baseline.stats import SampleStats, compute_stats, profile_scale
from am.db.enums import AlertStatus, AssessmentStatus, BaselineStatus, DataQuality, Severity
from am.db.models import Alert, BaselineAudit, BaselineProfile, Measurement
from am.policy import BaselinePolicy, ThresholdConfig

SYSTEM_ACTOR = "am-system"


@dataclass
class BaselineEvent:
    action: str  # created | adapted | frozen | capped | none
    profile_id: int | None
    reason: str


class BaselineService:
    def __init__(self, session: Session, policy: BaselinePolicy, thresholds: ThresholdConfig):
        self.s = session
        self.policy = policy
        self.thresholds = thresholds

    # ------------------------------------------------------------------ queries
    def active_profile(self, astronaut_id: int, metric: str, context: str) -> BaselineProfile | None:
        return self.s.scalars(
            select(BaselineProfile)
            .where(
                BaselineProfile.astronaut_id == astronaut_id,
                BaselineProfile.metric_code == metric,
                BaselineProfile.context == context,
                BaselineProfile.status != BaselineStatus.SUPERSEDED.value,
            )
            .order_by(BaselineProfile.version.desc())
            .limit(1)
        ).first()

    def anchor_profile(self, astronaut_id: int, metric: str, context: str) -> BaselineProfile | None:
        return self.s.scalars(
            select(BaselineProfile)
            .where(
                BaselineProfile.astronaut_id == astronaut_id,
                BaselineProfile.metric_code == metric,
                BaselineProfile.context == context,
                BaselineProfile.is_anchor.is_(True),
            )
            .order_by(BaselineProfile.version.desc())
            .limit(1)
        ).first()

    def scale_of(self, p: BaselineProfile) -> float:
        rule = self.thresholds.rule_for(p.metric_code)
        frac = rule.min_scale_fraction if rule else 0.02
        return profile_scale(p.median, p.mad, p.sd, frac, p.status == BaselineStatus.PROVISIONAL.value,
                             p.p05, p.p95, p.n)

    def _eligible_query(self, astronaut_id: int, metric: str, context: str):
        return select(Measurement).where(
            Measurement.astronaut_id == astronaut_id,
            Measurement.metric_code == metric,
            Measurement.context == context,
            Measurement.quality == DataQuality.GOOD.value,
            or_(
                Measurement.severity == Severity.NORMAL.value,
                Measurement.assessment_status == AssessmentStatus.NO_BASELINE.value,
            ),
        )

    # ------------------------------------------------------------------ helpers
    def _stage_for(self, st: SampleStats) -> BaselineStatus | None:
        e, p = self.policy.established, self.policy.provisional
        if st.n >= e.min_samples and st.span_hours >= e.min_span_hours:
            return BaselineStatus.ESTABLISHED
        if st.n >= p.min_samples and st.span_hours >= p.min_span_hours:
            return BaselineStatus.PROVISIONAL
        return None

    def _log(self, astronaut_id, metric, context, profile_id, action, reason, actor, **details):
        self.s.add(
            BaselineAudit(
                astronaut_id=astronaut_id,
                metric_code=metric,
                context=context,
                profile_id=profile_id,
                action=action,
                reason=reason,
                details=details,
                actor=actor,
            )
        )
        audit(self.s, actor=actor, action=f"baseline.{action}", entity="baseline_profile",
              entity_id=profile_id, payload={"metric": metric, "context": context, "reason": reason, **details})

    def _new_version(
        self,
        astronaut_id: int,
        metric: str,
        context: str,
        st: SampleStats,
        status: BaselineStatus,
        previous: BaselineProfile | None,
        median_override: float | None = None,
    ) -> BaselineProfile:
        version = (previous.version + 1) if previous else 1
        prof = BaselineProfile(
            astronaut_id=astronaut_id,
            metric_code=metric,
            context=context,
            version=version,
            status=status.value,
            n=st.n,
            mean=st.mean,
            median=st.median if median_override is None else median_override,
            sd=st.sd,
            mad=st.mad,
            p05=st.p05,
            p95=st.p95,
            min=st.min,
            max=st.max,
            trend_per_day=st.trend_per_day,
            window_start=st.window_start,
            window_end=st.window_end,
            supersedes_id=previous.id if previous else None,
            policy_version=self.policy.version,
        )
        if previous:
            previous.status = BaselineStatus.SUPERSEDED.value
        self.s.add(prof)
        self.s.flush()
        return prof

    def _freeze(self, p: BaselineProfile, reason: str, actor: str = SYSTEM_ACTOR, **details) -> BaselineEvent:
        p.is_frozen = True
        p.frozen_reason = reason
        self._log(p.astronaut_id, p.metric_code, p.context, p.id, "frozen", reason, actor, **details)
        return BaselineEvent("frozen", p.id, reason)

    # ------------------------------------------------------------------ lifecycle
    def try_create_initial(self, astronaut_id: int, metric: str, context: str) -> BaselineEvent:
        rows = self.s.scalars(self._eligible_query(astronaut_id, metric, context)).all()
        if not rows:
            return BaselineEvent("none", None, "no eligible data")
        st = compute_stats([r.value for r in rows], [r.recorded_at for r in rows])
        stage = self._stage_for(st)
        if stage is None:
            return BaselineEvent("none", None, f"insufficient data (n={st.n}, span={st.span_hours:.2f}h)")
        prof = self._new_version(astronaut_id, metric, context, st, stage, None)
        prof.is_anchor = True
        reason = f"initial {stage.value} baseline from n={st.n} over {st.span_hours:.1f}h"
        self._log(astronaut_id, metric, context, prof.id, "created", reason, SYSTEM_ACTOR, n=st.n)
        return BaselineEvent("created", prof.id, reason)

    def maybe_update(self, astronaut_id: int, metric: str, context: str) -> BaselineEvent:
        current = self.active_profile(astronaut_id, metric, context)
        if current is None:
            return self.try_create_initial(astronaut_id, metric, context)
        if current.is_frozen:
            return BaselineEvent("none", current.id, "frozen")

        ad = self.policy.adaptation
        new_rows = self.s.scalars(
            self._eligible_query(astronaut_id, metric, context).where(
                Measurement.recorded_at > current.window_end,
                Measurement.severity == Severity.NORMAL.value,
            )
        ).all()
        if len(new_rows) < ad.update_every_n_new_normal:
            return BaselineEvent("none", current.id, f"waiting ({len(new_rows)}/{ad.update_every_n_new_normal})")

        # Rule: freeze while a significant alert is open on this stream.
        freeze_rank = ad.freeze_on_active_alert_severity.rank
        open_alerts = self.s.scalars(
            select(Alert).where(
                Alert.astronaut_id == astronaut_id,
                Alert.metric_code == metric,
                Alert.context == context,
                Alert.status != AlertStatus.RESOLVED.value,
            )
        ).all()
        blocking = [a for a in open_alerts if Severity(a.peak_severity).rank >= freeze_rank]
        if blocking:
            return self._freeze(current, "open alert at or above freeze severity",
                                alert_ids=[a.id for a in blocking])

        scale = self.scale_of(current)
        new_st = compute_stats([r.value for r in new_rows], [r.recorded_at for r in new_rows])
        drift = (new_st.median - current.median) / scale
        if abs(drift) > ad.drift_freeze_scale:
            return self._freeze(current, f"drift detected: new-data median {drift:+.2f} scale units from baseline",
                                drift_scale=drift, n_new=new_st.n)

        latest = max(r.recorded_at for r in new_rows)
        window_start = latest - timedelta(days=ad.window_days)
        window_rows = self.s.scalars(
            self._eligible_query(astronaut_id, metric, context).where(Measurement.recorded_at >= window_start)
        ).all()
        st = compute_stats([r.value for r in window_rows], [r.recorded_at for r in window_rows])
        stage = self._stage_for(st) or BaselineStatus.PROVISIONAL

        # Rate limit per update.
        max_shift = ad.max_median_shift_scale * scale
        target = st.median
        capped = False
        if abs(target - current.median) > max_shift:
            target = current.median + max_shift * (1 if target > current.median else -1)
            capped = True

        # Cumulative limit relative to the anchor (initial or rebuilt version).
        anchor = self.anchor_profile(astronaut_id, metric, context)
        if anchor is not None:
            anchor_scale = self.scale_of(anchor)
            cumulative = (target - anchor.median) / anchor_scale
            if abs(cumulative) > ad.max_cumulative_shift_scale:
                return self._freeze(current, f"cumulative shift {cumulative:+.2f} scale units from anchor "
                                             f"v{anchor.version} exceeds limit; manual review required",
                                    cumulative_scale=cumulative)

        prof = self._new_version(astronaut_id, metric, context, st, stage, current, median_override=target)
        reason = f"adapted from v{current.version} using n={st.n} NORMAL samples ({stage.value})"
        if capped:
            reason += f"; median shift capped at {ad.max_median_shift_scale} scale units"
        self._log(astronaut_id, metric, context, prof.id, "adapted", reason, SYSTEM_ACTOR,
                  previous_id=current.id, raw_median=st.median, applied_median=target, capped=capped)
        return BaselineEvent("adapted", prof.id, reason)

    # ------------------------------------------------------------------ manual operations
    def unfreeze(self, profile_id: int, actor: str, reason: str) -> BaselineProfile:
        p = self.s.get(BaselineProfile, profile_id)
        if p is None:
            raise LookupError("baseline profile not found")
        if not p.is_frozen:
            raise ValueError("baseline is not frozen")
        p.is_frozen = False
        p.frozen_reason = None
        self._log(p.astronaut_id, p.metric_code, p.context, p.id, "unfrozen", reason, actor)
        return p

    def rebuild(self, astronaut_id: int, metric: str, context: str, *, start: datetime, end: datetime,
                actor: str, reason: str) -> BaselineProfile:
        """Operator-approved rebuild from an explicit window (e.g. after medical review)."""
        rows = self.s.scalars(
            select(Measurement).where(
                Measurement.astronaut_id == astronaut_id,
                Measurement.metric_code == metric,
                Measurement.context == context,
                Measurement.quality == DataQuality.GOOD.value,
                Measurement.recorded_at >= start,
                Measurement.recorded_at <= end,
            )
        ).all()
        if not rows:
            raise ValueError("no good-quality data in window")
        st = compute_stats([r.value for r in rows], [r.recorded_at for r in rows])
        stage = self._stage_for(st)
        if stage is None:
            raise ValueError(f"insufficient data for a baseline (n={st.n}, span={st.span_hours:.2f}h)")
        prev = self.active_profile(astronaut_id, metric, context)
        prof = self._new_version(astronaut_id, metric, context, st, stage, prev)
        prof.is_anchor = True
        self._log(astronaut_id, metric, context, prof.id, "rebuilt", reason, actor,
                  window_start=start.isoformat(), window_end=end.isoformat(), n=st.n)
        return prof

    def count_profiles(self, astronaut_id: int) -> int:
        return self.s.scalar(
            select(func.count()).select_from(BaselineProfile).where(BaselineProfile.astronaut_id == astronaut_id)
        ) or 0
