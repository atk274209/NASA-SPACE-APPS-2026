"""Daily duty roster: share the station's work out by each astronaut's energy and morale.

See am.db.models_duty for what is read (never the private diary). Scores and bands are DEMO
planning heuristics for workload, not clinical measures. The split is deterministic; the local
model only writes each astronaut a short note from their point of view, after the fact.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import mean
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from am.ai.ollama import LLMUnavailable, OllamaClient
from am.ai.safety_text import detect_language
from am.audit import audit
from am.db.base import utcnow
from am.db.models import Alert, Astronaut, Measurement
from am.db.models_ai import Conversation, Message, SelfReport
from am.db.models_duty import DutyAssignment, DutyPlan, DutyReadiness
from am.db.models_m5 import SymptomReport

log = logging.getLogger(__name__)

LABEL = "DEMO planning heuristic for workload, not a clinical measure"
WINDOW = timedelta(hours=36)
CAPACITY = {"full": 6, "steady": 4, "light": 2}
NEUTRAL = 60.0


@dataclass(frozen=True)
class Task:
    code: str
    title: str
    detail: str
    kind: str  # physical | focus | social | restorative
    load: int
    minutes: int


# The station's shared work for a day (DEMO catalogue).
STATION_TASKS: tuple[Task, ...] = (
    Task("eva_prep", "EVA suit and tool check", "Check suit seals, batteries and tethers for the next spacewalk.",
         "physical", 3, 90),
    Task("robotics", "Robotic arm session", "Run the planned arm manoeuvres with ground support.", "focus", 3, 120),
    Task("life_support", "Life-support filter change", "Swap the scheduled air-revitalisation filter.",
         "physical", 2, 60),
    Task("experiment", "Science experiment run", "Run today's experiment steps and log the results.", "focus", 2, 90),
    Task("cargo", "Cargo transfer and inventory", "Move supplies from the visiting vehicle and update the inventory.",
         "physical", 2, 75),
    Task("comms_ops", "Ground communications pass", "Lead the planning conference with mission control.",
         "focus", 1, 30),
    Task("housekeeping", "Station housekeeping", "Weekly clean of the shared modules.", "physical", 1, 40),
    Task("plants", "Plant habitat care", "Water and photograph the crops in the plant habitat.", "restorative", 1, 30),
    Task("earth_photo", "Earth observation photography", "Photograph today's science targets from the cupola.",
         "restorative", 1, 45),
    Task("outreach", "Call with students on Earth", "A short live call with a school.", "social", 1, 30),
    Task("galley", "Crew dinner preparation", "Prepare the shared crew dinner.", "social", 1, 45),
)
PERSONAL = {
    "call_home": Task("call_home", "Private call home", "A protected window to talk with family or friends.",
                      "restorative", 0, 30),
    "rest_block": Task("rest_block", "Protected rest block", "Off-duty time in your quarters. No tasks will be added.",
                       "restorative", 0, 60),
}


@dataclass
class Person:
    astronaut: Astronaut
    energy: float | None
    morale: float | None
    band: str
    inputs: dict[str, Any]
    reasons: list[str]
    left: int = 0
    tasks: list[Task] = field(default_factory=list)

    @property
    def readiness(self) -> float:
        known = [x for x in (self.energy, self.morale) if x is not None]
        return mean(known) if known else NEUTRAL


# ------------------------------------------------------------------ reading how people are
def _good(v: float) -> float:
    return (v - 1) / 4 * 100


def _bad(v: float) -> float:
    return (5 - v) / 4 * 100


def assess(s: Session, a: Astronaut) -> Person:
    since = utcnow() - WINDOW
    latest: dict[str, float] = {}
    for r in s.scalars(select(SelfReport).where(SelfReport.astronaut_id == a.id, SelfReport.kind != "note",
                                                SelfReport.recorded_at >= since).order_by(SelfReport.recorded_at)):
        latest[r.kind] = r.score  # newest wins
    sleep = s.scalars(select(Measurement).where(Measurement.astronaut_id == a.id,
                                                Measurement.metric_code == "sleep_duration",
                                                Measurement.recorded_at >= since)
                      .order_by(Measurement.recorded_at.desc())).first()
    energy_parts = []
    if "fatigue" in latest:
        energy_parts.append(_bad(latest["fatigue"]))
    if "sleep_quality" in latest:
        energy_parts.append(_good(latest["sleep_quality"]))
    if sleep is not None:
        energy_parts.append(min(max((sleep.value - 4) / 4, 0), 1) * 100)  # DEMO: 4 h -> 0, 8 h -> 100
    morale_parts = [_good(latest[k]) for k in ("mood", "motivation") if k in latest]
    if "stress" in latest:
        morale_parts.append(_bad(latest["stress"]))
    energy = round(mean(energy_parts)) if energy_parts else None
    morale = round(mean(morale_parts)) if morale_parts else None
    reasons: list[str] = []
    known = [x for x in (energy, morale) if x is not None]
    score = mean(known) if known else NEUTRAL
    band = "full" if score >= 70 else "steady" if score >= 45 else "light"
    if not known:
        reasons.append("No check-in or sleep record in the last 36 hours: planned as a steady day.")
    else:
        if energy is not None:
            reasons.append(f"Energy {energy}/100 from {'fatigue, ' if 'fatigue' in latest else ''}"
                           f"{'sleep quality, ' if 'sleep_quality' in latest else ''}"
                           f"{'recorded sleep' if sleep is not None else ''}".rstrip(", ") + ".")
        if morale is not None:
            reasons.append(f"Morale {morale}/100 from the latest check-in.")
    symptoms = list(s.scalars(select(SymptomReport).where(SymptomReport.astronaut_id == a.id,
                                                          SymptomReport.status == "active")))
    if any(r.severity == "severe" for r in symptoms):
        band = "light"
        reasons.append("A symptom rated severe is active: light duties only.")
    elif symptoms and band == "full":
        band = "steady"
        reasons.append("An active symptom: no heavy load today.")
    alerts = list(s.scalars(select(Alert).where(Alert.astronaut_id == a.id, Alert.status != "resolved",
                                                Alert.severity.in_(("WARNING", "CRITICAL")))))
    if alerts:
        band = "light"
        reasons.append("An open warning or critical alert: light duties until the doctor has reviewed it.")
    inputs = {"checkin": latest, "sleep_h": sleep.value if sleep is not None else None,
              "active_symptoms": len(symptoms), "open_alerts": len(alerts), "label": LABEL}
    return Person(a, energy, morale, band, inputs, reasons)


# ------------------------------------------------------------------ sharing out the work
def _fit(p: Person, t: Task) -> tuple[float, str]:
    """Higher is a better match. Returns (score, reason shown with the assignment)."""
    score = p.left - 0.6 * len(p.tasks)
    reason = "Fits the time and energy you have left today."
    low_morale = p.morale is not None and p.morale < 55
    if t.kind in ("restorative", "social") and low_morale:
        score += 3
        reason = "Morale is lower today: a calmer task that tends to lift the day."
    elif t.kind == "physical" and t.load >= 2 and (p.energy or 0) >= 70:
        score += 2
        reason = "High energy today: a good day for the hands-on work."
    elif t.kind == "focus" and t.load >= 2 and (p.morale or 0) >= 65 and (p.energy or 0) >= 55:
        score += 1.5
        reason = "Clear head and good spirits: suits a demanding focus task."
    if t.kind == "physical" and t.load >= 2 and p.energy is not None and p.energy < 45:
        score -= 5
    return score, reason


def allocate(people: list[Person], postponed: list[Task] | None = None) -> list[tuple[Person, Task, str]]:
    """Share the tasks out. A task nobody has room for goes to someone not on a light day; if everyone
    is on a light day, a heavier task is postponed (listed in `postponed`) rather than pushed on anyone."""
    for p in people:
        p.left = CAPACITY[p.band]
    out: list[tuple[Person, Task, str]] = []
    postponed = postponed if postponed is not None else []
    for t in sorted(STATION_TASKS, key=lambda t: (-t.load, t.code)):
        allowed = [p for p in people if p.left >= t.load and not (p.band == "light" and t.load > 1)]
        if allowed:
            best = max(allowed, key=lambda p: (_fit(p, t)[0], -p.astronaut.id))
            reason = _fit(best, t)[1]
        else:  # nobody has room: someone not on a light day takes it, and the plan says so
            able = [p for p in people if p.band != "light"] or ([p for p in people] if t.load <= 1 else [])
            if not able:
                postponed.append(t)
                continue
            best = max(able, key=lambda p: (p.left, p.readiness, -p.astronaut.id))
            reason = "Over today's planned load: nobody had room left. Worth a look by the crew."
        best.left -= t.load
        best.tasks.append(t)
        out.append((best, t, reason))
    for p in people:  # personal time AM protects, from the astronaut's side
        if p.morale is not None and p.morale < 55:
            out.append((p, PERSONAL["call_home"], "Morale is lower today: AM kept a call-home window free."))
        if p.band == "light":
            out.append((p, PERSONAL["rest_block"], "A light day: AM protected a rest block."))
    return out


# ------------------------------------------------------------------ notes in the astronaut's shoes
NOTE_PROMPT = """Put yourself in this astronaut's place on a long space mission. In 2 or 3 short sentences,
second person, warm and motivating, tell them how AM shared out today's duties around how they feel,
and what to look forward to. Use only the facts given. No medical advice, no diagnosis, no numbers
that are not given. Reply in {language}."""

TEMPLATES = {
    "full": {"en": "You came into today with energy to spare, so AM gave you the meatier work. Pace yourself and "
                   "enjoy getting real things done up here.",
             "tr": "Bugüne enerjin yerinde başladın, bu yüzden AM sana daha dolu işleri verdi. Temponu koru ve "
                   "burada gerçek işler başarmanın tadını çıkar."},
    "steady": {"en": "A steady day: AM kept your load balanced so you can do it well without running on empty.",
               "tr": "Dengeli bir gün: AM yükünü dengede tuttu; kendini tüketmeden iyi iş çıkarabilesin diye."},
    "light": {"en": "Today AM kept things light for you, with time protected for yourself. Rest is part of the "
                    "mission too, and the crew has the rest covered.",
              "tr": "Bugün AM işleri senin için hafif tuttu ve kendine zaman ayırdı. Dinlenmek de görevin bir "
                    "parçası; gerisini ekip hallediyor."},
}


def _language(s: Session, astronaut_id: int) -> str:
    last = s.scalar(select(Message.content).join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.astronaut_id == astronaut_id, Message.role == "user")
                    .order_by(Message.id.desc()).limit(1))
    return detect_language(last) if last else "en"


def write_note(s: Session, r: DutyReadiness, llm: OllamaClient | None, model: str | None) -> DutyReadiness:
    if r.note_by == "llm" or llm is None or not model:
        return r
    lang = _language(s, r.astronaut_id)
    tasks = s.scalars(select(DutyAssignment).where(DutyAssignment.plan_id == r.plan_id,
                                                   DutyAssignment.astronaut_id == r.astronaut_id)).all()
    facts = {"band": r.band, "energy": r.energy, "morale": r.morale, "why": r.reasons,
             "duties": [{"title": t.title, "why": t.reason} for t in tasks]}
    try:
        reply = llm.chat(model, [{"role": "system", "content": NOTE_PROMPT.format(
            language="Turkish" if lang == "tr" else "English")}, {"role": "user", "content": str(facts)}],
            temperature=0.5)
    except LLMUnavailable:
        return r
    text = reply.content.strip()
    if text:
        r.note, r.note_by = text[:700], "llm"
    return r


# ------------------------------------------------------------------ plans
_PLAN_LOCK = threading.Lock()  # one writer at a time: several screens ask for today's plan together


def plan_today(s: Session, *, actor: str = "am", replace: bool = False) -> DutyPlan:
    """Today's plan, created (or with replace, recreated) and committed under a process-wide lock."""
    today = utcnow().date()
    plan = s.scalar(select(DutyPlan).where(DutyPlan.plan_date == today))
    if plan is not None and not replace:
        return plan
    s.commit()  # end any read snapshot before waiting for the lock
    with _PLAN_LOCK:
        plan = _plan_today(s, today, actor, replace)
        s.commit()
        return plan


def _plan_today(s: Session, today, actor: str, replace: bool) -> DutyPlan:
    plan = s.scalar(select(DutyPlan).where(DutyPlan.plan_date == today))
    if plan is not None and not replace:
        return plan
    if plan is not None:
        s.execute(delete(DutyAssignment).where(DutyAssignment.plan_id == plan.id))
        s.execute(delete(DutyReadiness).where(DutyReadiness.plan_id == plan.id))
        plan.created_by, plan.created_at = actor, utcnow()
    else:
        plan = DutyPlan(plan_date=today, created_by=actor)
        s.add(plan)
    s.flush()
    from am.endday.pipeline import crew_on

    ids = [c.astronaut_id for c in crew_on(s, today)] or list(s.scalars(select(Astronaut.id).order_by(Astronaut.id)))
    people = [assess(s, s.get(Astronaut, i)) for i in ids]
    postponed: list[Task] = []
    rows = allocate(people, postponed) if people else []
    plan.postponed = [{"code": t.code, "title": t.title, "load": t.load} for t in postponed]
    for p in people:
        s.add(DutyReadiness(plan_id=plan.id, astronaut_id=p.astronaut.id, energy=p.energy, morale=p.morale,
                            capacity=CAPACITY[p.band], band=p.band, inputs=p.inputs, reasons=p.reasons,
                            note=TEMPLATES[p.band][_language(s, p.astronaut.id)], note_by="template"))
    for p, t, reason in rows:
        s.add(DutyAssignment(plan_id=plan.id, astronaut_id=p.astronaut.id, task_code=t.code, title=t.title,
                             detail=t.detail, kind=t.kind, load=t.load, minutes=t.minutes, reason=reason))
    audit(s, actor=actor, action="duty.plan", entity="duty_plan", entity_id=plan.id,
          payload={"date": today.isoformat(), "crew": len(people), "assignments": len(rows), "replace": replace})
    s.flush()
    return plan
