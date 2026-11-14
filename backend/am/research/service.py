"""Persisted correlation analysis runs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from am.db.models import Astronaut
from am.db.models_research import AnalysisRun, ResearchFinding
from am.research.correlation import CAVEAT, CorrelationParams, analyse
from am.research.series import build_daily_series, current_mission_day


def run_correlation(session: Session, astronaut_id: int, params: CorrelationParams | None = None) -> AnalysisRun:
    a = session.get(Astronaut, astronaut_id)
    if a is None:
        raise LookupError("astronaut not found")
    params = params or CorrelationParams()
    today = current_mission_day(session, astronaut_id)
    # only complete days: today's partial aggregates would distort daily medians
    through = (today - 1) if today else None
    series = build_daily_series(session, astronaut_id, until_day=through)
    findings, n_tests = analyse(series, params)
    run = AnalysisRun(astronaut_id=astronaut_id, kind="correlation", params=params.as_dict(), n_tests=n_tests,
                      n_series=len(series), through_day=through, is_simulated=a.is_simulated)
    session.add(run)
    session.flush()
    for f in findings:
        session.add(ResearchFinding(
            run_id=run.id, astronaut_id=astronaut_id, finding_type=f.finding_type, x_key=f.x_key, y_key=f.y_key,
            x_label=f.x_label, y_label=f.y_label, lag_days=f.lag_days, n_days=f.n, rho=f.rho, p_value=f.p,
            q_value=f.q, rho_detrended=f.rho_diff, p_detrended=f.p_diff, detrended_supported=f.detrended_supported,
            slope_per_day=f.slope_per_day, unit=f.unit, days=f.days, summary=f.summary(), caveat=CAVEAT))
    session.flush()
    session.refresh(run)
    return run
