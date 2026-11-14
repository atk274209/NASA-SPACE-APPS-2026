"""Seeded synthetic astronaut data generator.

EVERYTHING produced here is SIMULATED. Values are drawn from simple parametric
distributions chosen for software demonstration only; they are not derived from, and
must not be presented as, real astronaut measurements or physiological norms. All rows
are written with source kind 'simulated' and is_simulated=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from am.db.enums import MeasurementContext as C
from am.db.enums import SourceKind
from am.health.ingest import MeasurementInput


@dataclass(frozen=True)
class StreamParams:
    """Synthetic stream: mean +/- sd, sampled `per_day` times within active hours."""

    metric: str
    unit: str
    context: str
    mean: float
    sd: float
    per_day: int
    hours: tuple[int, int]  # sampling window [start, end) in hour-of-day
    decimals: int = 0


@dataclass(frozen=True)
class Perturbation:
    metric: str
    context: str
    start_day: float
    end_day: float | None  # None -> until end
    kind: str  # "ramp" (linear to `delta` at end) | "step" (constant `delta`)
    delta: float


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    days: int
    streams: list[StreamParams]
    perturbations: list[Perturbation] = field(default_factory=list)
    # Streams sampled from a separate generator so they never shift the `streams` draws.
    extra_streams: list[StreamParams] = field(default_factory=lambda: list(_M5_STREAMS))


_BASE_STREAMS = [
    StreamParams("heart_rate", "bpm", C.REST.value, 68, 2.5, 24, (7, 23)),
    StreamParams("heart_rate", "bpm", C.SLEEP.value, 57, 2.0, 8, (0, 6)),
    StreamParams("heart_rate", "bpm", C.EXERCISE.value, 128, 7.0, 6, (16, 17)),
    StreamParams("hrv_rmssd", "ms", C.REST.value, 48, 6.0, 4, (7, 11)),
    StreamParams("spo2", "%", C.REST.value, 97.5, 0.6, 4, (8, 20), decimals=1),
    StreamParams("body_temperature", "degC", C.REST.value, 36.7, 0.15, 3, (8, 20), decimals=2),
    StreamParams("sleep_duration", "h", C.DAILY.value, 7.0, 0.5, 1, (7, 8), decimals=2),
    StreamParams("exercise_duration", "min", C.DAILY.value, 120, 15, 1, (21, 22)),
    StreamParams("water_intake", "mL", C.DAILY.value, 2500, 250, 1, (22, 23)),
]

# M5 streams. Like everything here, the parameters are ARBITRARY demonstration values:
# not a physiological norm, not a reference range and not a reference exposure rate.
# They are drawn from their own random generator (see `generate`), so adding them leaves
# every value of the base streams above unchanged for a given seed.
_M5_STREAMS = [
    # Fasting blood glucose, once a day before breakfast.
    StreamParams("blood_glucose", "mg/dL", C.FASTING.value, 92, 6, 1, (6, 7)),
    # Personal dosimeter dose for the day, logged once each evening.
    StreamParams("radiation_dose", "mSv", C.DAILY.value, 0.45, 0.05, 1, (23, 24), decimals=3),
]

# Salt that derives the M5 streams' generator from the scenario seed.
_M5_SEED_SALT = 5

SCENARIOS: dict[str, Scenario] = {
    "nominal": Scenario(
        name="nominal",
        description="SIMULATED: stable streams, no injected deviations.",
        days=10,
        streams=_BASE_STREAMS,
    ),
    "acute_rest_hr": Scenario(
        name="acute_rest_hr",
        description="SIMULATED: ~4 stable days, then resting heart rate stepped up by +47 bpm "
                    "for the final 6 hours (mirrors the 70 -> 115 bpm walkthrough).",
        days=4,
        streams=_BASE_STREAMS,
        perturbations=[Perturbation("heart_rate", C.REST.value, 3.75, None, "step", 47.0)],
    ),
    "gradual_decline": Scenario(
        name="gradual_decline",
        description="SIMULATED: 30 days; from day 12 resting HR ramps up (+14 bpm by end), HRV ramps down, "
                    "exercise duration and sleep decline. Tests that the baseline does not absorb drift.",
        days=30,
        streams=_BASE_STREAMS,
        perturbations=[
            Perturbation("heart_rate", C.REST.value, 12, None, "ramp", 14.0),
            Perturbation("hrv_rmssd", C.REST.value, 12, None, "ramp", -18.0),
            Perturbation("exercise_duration", C.DAILY.value, 12, None, "ramp", -60.0),
            Perturbation("sleep_duration", C.DAILY.value, 16, None, "ramp", -1.5),
        ],
    ),
    # M6a scenario fixtures for the Test Console (master prompt v1, Module K). Like everything
    # here, the sizes of the injected changes are ARBITRARY demonstration values.
    "exercise_decline": Scenario(
        name="exercise_decline",
        description="SIMULATED: 21 days; from day 8 daily exercise duration ramps down (-70 min by the end) "
                    "and resting HR drifts up slightly.",
        days=21,
        streams=_BASE_STREAMS,
        perturbations=[
            Perturbation("exercise_duration", C.DAILY.value, 8, None, "ramp", -70.0),
            Perturbation("heart_rate", C.REST.value, 8, None, "ramp", 5.0),
        ],
    ),
    "poor_sleep": Scenario(
        name="poor_sleep",
        description="SIMULATED: 21 days; from day 10 sleep duration drops by 2 h and sleeping HR is a "
                    "little higher; HRV ramps down.",
        days=21,
        streams=_BASE_STREAMS,
        perturbations=[
            Perturbation("sleep_duration", C.DAILY.value, 10, None, "step", -2.0),
            Perturbation("heart_rate", C.SLEEP.value, 10, None, "step", 5.0),
            Perturbation("hrv_rmssd", C.REST.value, 10, None, "ramp", -12.0),
        ],
    ),
    "acute_symptom_escalation": Scenario(
        name="acute_symptom_escalation",
        description="SIMULATED: stable days, then from day 6.66 of the generated window (the last hours "
                    "before loading) resting HR is stepped up by +25 bpm and body temperature by +0.9 degC "
                    "(paired with symptom reports by the fixture).",
        days=7,
        streams=_BASE_STREAMS,
        perturbations=[
            Perturbation("heart_rate", C.REST.value, 6.66, None, "step", 25.0),
            Perturbation("body_temperature", C.REST.value, 6.66, None, "step", 0.9),
        ],
    ),
}


def _offset(p: Perturbation, day_f: float, total_days: int) -> float:
    end = p.end_day if p.end_day is not None else total_days
    if day_f < p.start_day or (p.end_day is not None and day_f > p.end_day):
        return 0.0
    if p.kind == "step":
        return p.delta
    span = max(end - p.start_day, 1e-9)
    return p.delta * min((day_f - p.start_day) / span, 1.0)  # ramp holds at full delta after `end`


def generate(
    scenario: Scenario,
    *,
    astronaut_id: int,
    end: datetime,
    seed: int = 42,
) -> list[MeasurementInput]:
    start_day = (end - timedelta(days=scenario.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    source = f"am-simulator/{scenario.name}/seed-{seed}"
    out = _sample(scenario, scenario.streams, np.random.default_rng(seed),
                  astronaut_id=astronaut_id, start_day=start_day, end=end, source=source)
    # A second, independent generator: the base streams consume exactly the same draws as
    # before M5, so existing scenarios reproduce value for value.
    out += _sample(scenario, scenario.extra_streams, np.random.default_rng((seed, _M5_SEED_SALT)),
                   astronaut_id=astronaut_id, start_day=start_day, end=end, source=source)
    out.sort(key=lambda x: x.recorded_at)
    return out


def _sample(
    scenario: Scenario,
    streams: list[StreamParams],
    rng: np.random.Generator,
    *,
    astronaut_id: int,
    start_day: datetime,
    end: datetime,
    source: str,
) -> list[MeasurementInput]:
    out: list[MeasurementInput] = []
    for d in range(scenario.days + 1):
        day0 = start_day + timedelta(days=d)
        for sp in streams:
            h0, h1 = sp.hours
            step = (h1 - h0) * 3600 / sp.per_day
            for i in range(sp.per_day):
                t = day0 + timedelta(seconds=h0 * 3600 + step * i + float(rng.uniform(0, step * 0.5)))
                if t > end:
                    continue
                day_f = (t - start_day).total_seconds() / 86400.0
                value = float(rng.normal(sp.mean, sp.sd))
                for p in scenario.perturbations:
                    if p.metric == sp.metric and p.context == sp.context:
                        value += _offset(p, day_f, scenario.days)
                value = max(value, 0.0)
                out.append(
                    MeasurementInput(
                        astronaut_id=astronaut_id,
                        metric_code=sp.metric,
                        value=round(value, sp.decimals) if sp.decimals else float(round(value)),
                        unit=sp.unit,
                        context=sp.context,
                        recorded_at=t,
                        source_name=source,
                        source_kind=SourceKind.SIMULATED,
                        confidence=0.95,
                    )
                )
    return out
