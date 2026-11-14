"""Scenario fixtures for the Test Console and the automated tests (master prompt v1, Module K).

A fixture back-fills ONE astronaut's mission with SIMULATED data: generated measurements,
daily check-ins that follow a trajectory, and symptom reports. Every row carries
is_simulated=True and data_origin=SIMULATED_GENERATOR. Trajectories and symptoms are arbitrary
demonstration values, not physiological norms or clinical patterns.

Loading moves that astronaut's mission start back so the generated days are mission days
1..N. It is refused when the astronaut already has records, so test data is never mixed into a
history someone typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.db.base import utcnow
from am.db.models import Astronaut, Measurement, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_m6a import DataOrigin
from am.health.ingest import IngestService
from am.simulation.generator import SCENARIOS, generate
from am.symptoms import catalog


@dataclass(frozen=True)
class FixtureSymptom:
    day: int | None  # mission day; None = today (the day the fixture is loaded)
    code: str
    severity: str
    resolved_day: int | None = None
    note: str = "Simulated report created by a scenario fixture."


@dataclass(frozen=True)
class Fixture:
    code: str
    title: str
    scenario: str  # key of am.simulation.generator.SCENARIOS
    # check-in trajectory: kind -> (score on day 1, score on the last full day); linear, clipped 1..5
    checkins: dict[str, tuple[float, float]] = field(default_factory=dict)
    symptoms: tuple[FixtureSymptom, ...] = ()
    seed: int = 21

    @property
    def description(self) -> str:
        return SCENARIOS[self.scenario].description


FIXTURES: dict[str, Fixture] = {f.code: f for f in (
    Fixture("nominal", "Nominal mission", "nominal",
            checkins={"mood": (4.0, 4.0), "stress": (2.0, 2.0), "fatigue": (2.0, 2.0),
                      "sleep_quality": (4.0, 4.0), "motivation": (4.0, 4.0)}, seed=31),
    Fixture("exercise_decline", "Gradual exercise decline", "exercise_decline",
            checkins={"mood": (4.0, 3.2), "stress": (2.0, 2.8), "fatigue": (2.0, 3.8),
                      "sleep_quality": (4.0, 3.6), "motivation": (4.2, 2.6)},
            symptoms=(FixtureSymptom(14, "muscle_soreness", "mild", resolved_day=16),
                      FixtureSymptom(19, "fatigue", "moderate")), seed=32),
    Fixture("poor_sleep", "Poor-sleep period", "poor_sleep",
            checkins={"mood": (4.0, 2.8), "stress": (2.0, 3.6), "fatigue": (2.0, 4.0),
                      "sleep_quality": (4.2, 1.8), "motivation": (4.0, 3.0)},
            symptoms=(FixtureSymptom(12, "insomnia", "moderate"),
                      FixtureSymptom(17, "headache", "mild", resolved_day=18)), seed=33),
    Fixture("acute_symptom_escalation", "Acute symptom escalation", "acute_symptom_escalation",
            checkins={"mood": (4.0, 3.8), "stress": (2.0, 2.4), "fatigue": (2.0, 2.4),
                      "sleep_quality": (4.0, 3.8), "motivation": (4.0, 3.8)},
            symptoms=(FixtureSymptom(None, "abdominal_pain", "severe"),
                      FixtureSymptom(None, "nausea", "moderate")), seed=34),
)}


class FixtureError(Exception):
    """The fixture cannot be loaded; the message is safe to show."""


def _has_records(s: Session, astronaut_id: int) -> bool:
    for model in (Measurement, SelfReport, SymptomReport):
        if s.scalar(select(func.count()).select_from(model).where(model.astronaut_id == astronaut_id)):
            return True
    return False


@dataclass
class FixtureReport:
    fixture: str
    astronaut_id: int
    mission_start: str
    mission_day: int
    measurements: int
    checkins: int
    symptoms: int


def load_fixture(s: Session, ctx, *, astronaut_id: int, code: str, actor: str) -> FixtureReport:
    """Back-fill one astronaut with a SIMULATED scenario. The caller commits."""
    from am.journal.service import JournalService
    from am.missions.service import MissionService
    from am.research.service import run_correlation

    fx = FIXTURES.get(code)
    if fx is None:
        raise FixtureError(f"unknown scenario '{code}'; choose one of {sorted(FIXTURES)}")
    a = s.get(Astronaut, astronaut_id)
    if a is None:
        raise LookupError("astronaut not found")
    if _has_records(s, astronaut_id):
        raise FixtureError("this astronaut already has records; scenarios load only into an empty history")
    scenario = SCENARIOS[fx.scenario]
    end = utcnow()
    start = (end - timedelta(days=scenario.days)).date()
    mission = s.scalars(select(Mission).where(Mission.astronaut_id == astronaut_id)
                        .order_by(Mission.start_date.desc(), Mission.id.desc())).first()
    if mission is None:
        mission = Mission(name="SIMULATED scenario mission", astronaut_id=astronaut_id, start_date=start,
                          planned_days=180, environment="unspecified", is_simulated=True)
        s.add(mission)
    previous_start = mission.start_date
    mission.start_date = start
    s.flush()

    svc = IngestService(s, ctx.thresholds, ctx.policy)
    items = generate(scenario, astronaut_id=astronaut_id, end=end, seed=fx.seed)
    for item in items:
        svc.ingest(item, now=end)
    s.flush()

    today = (end.date() - start).days + 1
    last_full_day = today - 1
    rng = np.random.default_rng(fx.seed)
    n_checkins = 0
    for d in range(1, last_full_day + 1):
        at = datetime.combine(start, time(21, 0)) + timedelta(days=d - 1)
        frac = (d - 1) / max(last_full_day - 1, 1)
        cid = f"fixture-{fx.code}-{astronaut_id}-{d}"
        for kind, (v0, v1) in fx.checkins.items():
            score = int(np.clip(round(v0 + (v1 - v0) * frac + rng.normal(0, 0.35)), 1, 5))
            s.add(SelfReport(astronaut_id=astronaut_id, checkin_id=cid, kind=kind, score=float(score), scale_max=5,
                             note="", is_simulated=True, data_origin=DataOrigin.SIMULATED_GENERATOR.value,
                             recorded_at=at))
            n_checkins += 1
    for sym in fx.symptoms:
        if catalog.get(sym.code) is None:
            raise FixtureError(f"fixture symptom '{sym.code}' is not in the catalog")
        if sym.day is None:
            at = end - timedelta(hours=1)
        else:
            at = datetime.combine(start, time(9, 30)) + timedelta(days=sym.day - 1)
        resolved = (datetime.combine(start, time(20, 0)) + timedelta(days=sym.resolved_day - 1)
                    if sym.resolved_day else None)
        s.add(SymptomReport(astronaut_id=astronaut_id, symptom_code=sym.code, severity=sym.severity,
                            note=sym.note, onset_at=at, reported_at=at, source="simulated",
                            status="resolved" if resolved else "active", resolved_at=resolved, is_simulated=True,
                            data_origin=DataOrigin.SIMULATED_GENERATOR.value))
    s.flush()

    JournalService(s).generate_range(astronaut_id, 1, last_full_day if last_full_day >= 1 else None)
    run_correlation(s, astronaut_id)
    MissionService(s).ensure_today(astronaut_id)
    audit(s, actor=actor, action="test.fixture_loaded", entity="astronaut", entity_id=astronaut_id,
          payload={"fixture": fx.code, "scenario": fx.scenario, "mission_start_before": previous_start.isoformat(),
                   "mission_start": start.isoformat(), "measurements": len(items), "simulated": True})
    return FixtureReport(fixture=fx.code, astronaut_id=astronaut_id, mission_start=start.isoformat(),
                         mission_day=today, measurements=len(items), checkins=n_checkins, symptoms=len(fx.symptoms))
