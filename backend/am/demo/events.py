"""SIMULATED space events for live demonstrations. See am.db.models_demo for the idea.

Each event kind changes one real input of AM (a cabin sensor reading, a sensor fault, wearable
readings, a symptom report) and then records, in plain words, what AM's own subsystems did in
response. Nothing in the reaction is scripted: if AM does not react, the event says so.
Cabin faults clear themselves when the event ends; health records stay, as they would.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.audit import audit
from am.db.base import utcnow
from am.db.models import Alert, Astronaut
from am.db.models_demo import SpaceEvent
from am.db.models_m6b import ReportFlag
from am.cabin.envelope import load_envelope

log = logging.getLogger(__name__)

ACTOR = "am-demo"
STARTUP_DELAY_S = 8.0


@dataclass(frozen=True)
class Kind:
    code: str
    title: str
    weight: int
    minutes: int  # how long the event stays active on screen (cabin faults clear at the end)
    needs_astronaut: bool
    run: Callable[[Session, Any, random.Random, SpaceEvent], None]


# ------------------------------------------------------------------ helpers
def _crew(s: Session) -> list[Astronaut]:
    from am.endday.pipeline import crew_on

    today = utcnow().date()
    ids = [c.astronaut_id for c in crew_on(s, today)]
    if not ids:
        ids = list(s.scalars(select(Astronaut.id).order_by(Astronaut.id)))
    return [s.get(Astronaut, i) for i in ids]


def _cabin_event(s: Session, ev: SpaceEvent, control, *, drift: float | None = None, fault: str | None = None) -> None:
    from am.cabin import controller as cab

    if drift is not None:
        control.drift = drift
    if fault is not None:
        control.fault = fault
    cab._log(s, control, "FAULT", "P-DEMO-EVENT", ACTOR, reason=f"SIMULATED demo event: {ev.title}")
    mode_before = control.mode
    alerts = cab.sense(s)
    mine = [a for a in alerts if a.zone_id == control.zone_id and a.variable == control.variable]
    ev.detail = {**ev.detail, "control_id": control.id, "mode_before": mode_before}
    if mine:
        a = mine[0]
        ev.reactions = ev.reactions + [f"Cabin monitor raised a {a.severity.lower()} alert: {a.text}"]
        ev.refs = ev.refs + [{"type": "env_alert", "id": a.id}]
    else:
        ev.reactions = ev.reactions + ["The cabin monitor read the sensors and found nothing outside its limits."]
    if mode_before == "AUTONOMOUS" and control.mode == "PAUSED":
        ev.reactions = ev.reactions + [f"AM paused its own {control.variable} control and handed it to the crew "
                                       "until the reading is trustworthy again."]


def _clear_cabin(s: Session, ev: SpaceEvent) -> None:
    from am.cabin import controller as cab
    from am.db.models_cabin import EnvControl

    c = s.get(EnvControl, ev.detail.get("control_id")) if ev.detail.get("control_id") else None
    if c is None:
        return
    c.drift, c.fault = 0.0, "none"
    cab._log(s, c, "FAULT", "P-DEMO-EVENT", ACTOR, reason=f"SIMULATED demo event ended: {ev.title}")
    if ev.detail.get("mode_before") == "AUTONOMOUS" and c.mode == "PAUSED":
        cab.set_mode(s, c, "AUTONOMOUS", actor=ACTOR, reason="SIMULATED demo event ended; reading back in limits")
        ev.reactions = ev.reactions + [f"Readings back in limits: AM resumed {c.variable} control."]
    cab.sense(s)


def _health_reaction(s: Session, ev: SpaceEvent, alert_ids: set[int]) -> None:
    if not alert_ids:
        ev.reactions = ev.reactions + ["The deviation engine compared the readings with this astronaut's own baseline "
                                       "and opened no alert."]
        return
    for a in s.scalars(select(Alert).where(Alert.id.in_(alert_ids))):
        ev.reactions = ev.reactions + [
            f"Deviation engine: {a.metric_code.replace('_', ' ')} alert, {a.severity.lower()} "
            f"(personal baseline, DEMO thresholds). The doctor sees it in Alerts."]
        ev.refs = ev.refs + [{"type": "alert", "id": a.id}]


def _flag_reaction(s: Session, ev: SpaceEvent, report_id: int) -> None:
    f = s.scalars(select(ReportFlag).where(ReportFlag.source_type == "symptom_report",
                                           ReportFlag.source_id == report_id)).first()
    if f is None:
        return
    ev.refs = ev.refs + [{"type": "report_flag", "id": f.id}]
    text = f"Doctor's live feed: entry flagged {f.rule_level} by the rules"
    if f.approval_status == "awaiting":
        text += "; it waits for the doctor's approval and the astronaut is told it was sent"
    ev.reactions = ev.reactions + [text + "."]


# ------------------------------------------------------------------ event kinds
def _solar(s, ctx, rng, ev):
    ev.severity, ev.summary = "warning", (
        "SIMULATED: a burst of solar energetic particles reaches the station. Radiation dose is not "
        "simulated in this prototype.")
    ev.reactions = ["All crew were shown the notice to follow the mission's radiation shelter procedure (DEMO).",
                    "AM keeps monitoring every crew member as usual; no health value was invented."]


def _ground_link(s, ctx, rng, ev):
    ev.severity, ev.summary = "info", "SIMULATED: the link with mission control on Earth is lost for a while."
    ev.reactions = ["AM keeps working on board: monitoring, alerts, the doctor's feed and the diary need no "
                    "connection to Earth.",
                    "Nothing waits for the ground: records are stored locally and sync when the link returns."]


def _heat(s, ctx, rng, ev):
    from am.cabin.controller import ensure_zones, quarters_control

    ensure_zones(s)
    a = s.get(Astronaut, ev.astronaut_id)
    c = quarters_control(s, a.id, "temperature")
    ev.severity = "warning"
    ev.summary = f"SIMULATED: the thermal loop in {a.display_name}'s quarters fails and the room heats up."
    lim = load_envelope().variables["temperature"]
    _cabin_event(s, ev, c, drift=round(lim.max + 1.5 - c.setpoint, 1))  # past the hard limit, whatever the setpoint


def _pressure(s, ctx, rng, ev):
    from am.cabin.controller import cabin_control, ensure_zones

    ensure_zones(s)
    c = cabin_control(s, "pressure")
    ev.severity = "warning"
    ev.summary = "SIMULATED: cabin pressure drifts down slowly, as after a small leak (micrometeoroid suspected)."
    lim = load_envelope().variables["pressure"]
    _cabin_event(s, ev, c, drift=round(lim.auto_min - 0.4 - c.setpoint, 2))  # below the autonomous band
    ev.reactions = ev.reactions + ["Pressure is cabin-wide and safety-critical: AM never corrects it outside its "
                                   "narrow band on its own; a person decides."]


def _light_sensor(s, ctx, rng, ev):
    from am.cabin.controller import ensure_zones, quarters_control

    ensure_zones(s)
    a = s.get(Astronaut, ev.astronaut_id)
    c = quarters_control(s, a.id, "lighting")
    ev.severity = "watch"
    ev.summary = f"SIMULATED: the light sensor in {a.display_name}'s quarters stops returning valid readings."
    _cabin_event(s, ev, c, fault="sensor")


def _wearable(preset: str, summary: str):
    def run(s, ctx, rng, ev):
        from am.simulation.wearable import NoWindow, simulate_wearable

        a = s.get(Astronaut, ev.astronaut_id)
        ev.severity, ev.summary = "watch", summary.format(name=a.display_name)
        try:
            res = simulate_wearable(s, ctx.thresholds, ctx.policy, a.id, preset, 3)
        except NoWindow:
            ev.reactions = ["The mission day has just started: no readings to add yet."]
            return
        ev.reactions = [f"AM received {res.readings} SIMULATED wearable readings from the last hours."]
        _health_reaction(s, ev, res.alert_ids)
    return run


def _symptom(codes: tuple[str, ...], severity: str, level: str, summary: str):
    def run(s, ctx, rng, ev):
        from am.symptoms import catalog
        from am.symptoms.service import SymptomService

        a = s.get(Astronaut, ev.astronaut_id)
        code = rng.choice(codes)
        label = catalog.get(code).label
        ev.severity, ev.summary = level, summary.format(name=a.display_name, symptom=label.lower())
        r = SymptomService(s).report(a.id, code, severity, note="SIMULATED demo event", source="simulated")
        r.is_simulated = True
        ev.reactions = [f"Symptom report recorded: {label.lower()}, rated {severity}."]
        ev.refs = [{"type": "symptom_report", "id": r.id}]
        s.flush()
        _flag_reaction(s, ev, r.id)
    return run


KINDS: tuple[Kind, ...] = (
    Kind("solar_particle_event", "Solar particle event", 2, 6, False, _solar),
    Kind("ground_link_loss", "Link with Earth lost", 2, 5, False, _ground_link),
    Kind("quarters_heat", "Quarters overheating", 3, 4, True, _heat),
    Kind("pressure_drop", "Slow cabin pressure drop", 2, 4, False, _pressure),
    Kind("light_sensor_fault", "Light sensor fault", 2, 4, True, _light_sensor),
    Kind("elevated_heart_rate", "Heart rate rising", 3, 5, True,
         _wearable("elevated_heart_rate", "SIMULATED: {name}'s smart shirt shows a higher heart rate than usual.")),
    Kind("fever_signs", "Temperature rising", 2, 5, True,
         _wearable("fever", "SIMULATED: {name}'s smart shirt shows body temperature and heart rate going up.")),
    Kind("symptom_report", "Symptom reported", 3, 4, True,
         _symptom(("headache", "nausea", "insomnia", "back_pain", "nasal_congestion", "eye_irritation"),
                  "moderate", "watch", "SIMULATED: {name} reports {symptom}.")),
    Kind("urgent_symptom", "Urgent symptom", 1, 6, True,
         _symptom(("chest_pain", "shortness_of_breath"), "moderate", "critical",
                  "SIMULATED: {name} reports {symptom}.")),
)
BY_CODE = {k.code: k for k in KINDS}


# ------------------------------------------------------------------ running events
def trigger(s: Session, ctx: Any, code: str | None = None, *, actor: str = ACTOR,
            rng: random.Random | None = None) -> SpaceEvent:
    rng = rng or random.Random()
    if code is not None and code not in BY_CODE:
        raise ValueError(f"unknown event kind '{code}'")
    kind = BY_CODE[code] if code else rng.choices(KINDS, weights=[k.weight for k in KINDS])[0]
    crew = _crew(s)
    if kind.needs_astronaut and not crew:
        raise ValueError("no crew to involve")
    now = utcnow()
    ev = SpaceEvent(kind=kind.code, title=kind.title, triggered_by=actor, started_at=now,
                    ends_at=now + timedelta(minutes=kind.minutes), reactions=[], refs=[], detail={},
                    astronaut_id=rng.choice(crew).id if kind.needs_astronaut else None)
    s.add(ev)
    s.flush()
    kind.run(s, ctx, rng, ev)
    audit(s, actor=actor, action="demo.event", entity="space_event", entity_id=ev.id,
          payload={"kind": kind.code, "astronaut_id": ev.astronaut_id})
    s.flush()
    return ev


def resolve_due(s: Session) -> list[SpaceEvent]:
    done = []
    now = utcnow()
    for ev in s.scalars(select(SpaceEvent).where(SpaceEvent.status == "active", SpaceEvent.ends_at <= now)):
        _clear_cabin(s, ev)
        ev.status, ev.resolved_at = "resolved", now
        done.append(ev)
    s.flush()
    return done


class DemoDirector:
    """Starts one event shortly after start-up, then one every `interval` seconds (with jitter)."""

    def __init__(self, ctx: Any, interval: float, startup_delay: float = STARTUP_DELAY_S):
        self.ctx = ctx
        self.interval = max(30.0, interval)
        self.startup_delay = startup_delay
        self.enabled = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="am-demo-events", daemon=True)
        self._rng = random.Random()

    def start(self) -> DemoDirector:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _tick(self, fire: bool) -> None:
        with self.ctx.session_factory() as s:
            resolve_due(s)
            if fire and self.enabled:
                trigger(s, self.ctx, rng=self._rng)
            s.commit()

    def _run(self) -> None:
        # Real (monotonic) time: End Day can move the mission clock by a day.
        next_fire = time.monotonic() + self.startup_delay
        while not self._stop.wait(2.0):
            try:
                fire = time.monotonic() >= next_fire
                self._tick(fire)
                if fire:
                    next_fire = time.monotonic() + self.interval * self._rng.uniform(0.7, 1.3)
            except Exception:
                log.warning("demo event loop error", exc_info=True)
                next_fire = time.monotonic() + self.interval
