"""Deterministic comfort control loop (master prompt Module P). The LLM never issues a command.

signal -> (mode check) -> bounded step toward the astronaut's comfort -> envelope + rate check
-> apply through the actuator adapter -> verify with the next reading -> log -> tell the astronaut.

Rules (rule_id in every EnvAction / EnvAlert):
  P-COMFORT-STEP     autonomous step of at most `step` toward comfort, inside the autonomous band
  P-MODE             refused: the control is PAUSED or MANUAL (manual control always wins)
  P-ENVELOPE         refused: the step would leave the autonomous band
  P-RATE             refused: more than `max_change_per_hour` of autonomous change in the last hour
  P-FAULT-ACT / P-FAULT-SENSOR   actuator or sensor fault: autonomy pauses, alert raised
  P-VERIFY           the next reading did not confirm the change: alert raised
  P-OUT-OF-ENVELOPE  a reading left the envelope (pressure: the autonomous band): autonomy pauses, alert
  P-PRESSURE-REPORT  an ear/pressure complaint: never adjusted, reviewed by a person
  P-UNRESOLVED       repeated "did not help" answers: reviewed by a person
  P-MANUAL / P-EXCEPTION / P-REVERT / P-MODE-SET   human actions
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.cabin.actuator import ActuatorFault, CabinActuator, SensorFault, SimulatedCabin
from am.cabin.comfort import ComfortHit
from am.cabin.envelope import VarLimits, load_envelope
from am.db.base import utcnow
from am.db.models import Astronaut
from am.db.models_cabin import MODES, CabinZone, ComfortPreference, ComfortSignal, EnvAction, EnvAlert, EnvControl

CABIN_CODE = "CABIN"


class CabinError(Exception):
    """Refused; the message is safe to show."""


# ------------------------------------------------------------------ zones
def ensure_zones(s: Session) -> None:
    env = load_envelope()
    zones = {z.code: z for z in s.scalars(select(CabinZone))}
    if CABIN_CODE not in zones:
        zones[CABIN_CODE] = CabinZone(code=CABIN_CODE, name="Cabin (shared)", astronaut_id=None)
        s.add(zones[CABIN_CODE])
    for a in s.scalars(select(Astronaut)):
        code = f"Q-{a.code}"
        if code not in zones:
            zones[code] = CabinZone(code=code, name=f"Crew quarters: {a.display_name}", astronaut_id=a.id)
            s.add(zones[code])
    s.flush()
    have = set(s.execute(select(EnvControl.zone_id, EnvControl.variable)).all())
    for z in zones.values():
        for name, lim in env.variables.items():
            wanted = (lim.scope == "cabin") == (z.astronaut_id is None)
            if wanted and (z.id, name) not in have:
                s.add(EnvControl(zone_id=z.id, variable=name, mode="AUTONOMOUS", setpoint=lim.default,
                                 last_reading=lim.default, last_read_at=utcnow()))
    s.flush()


def quarters_control(s: Session, astronaut_id: int, variable: str) -> EnvControl | None:
    return s.scalars(select(EnvControl).join(CabinZone, EnvControl.zone_id == CabinZone.id)
                     .where(CabinZone.astronaut_id == astronaut_id, EnvControl.variable == variable)).first()


def cabin_control(s: Session, variable: str = "pressure") -> EnvControl | None:
    return s.scalars(select(EnvControl).join(CabinZone, EnvControl.zone_id == CabinZone.id)
                     .where(CabinZone.code == CABIN_CODE, EnvControl.variable == variable)).first()


def _zone(s: Session, c: EnvControl) -> CabinZone:
    return s.get(CabinZone, c.zone_id)


# ------------------------------------------------------------------ helpers
def _log(s: Session, c: EnvControl, kind: str, rule_id: str, actor: str, *, before=None, after=None, signal_id=None,
         reverts_id=None, reason: str = "", verified=None, detail=None) -> EnvAction:
    z = _zone(s, c)
    a = EnvAction(zone_id=c.zone_id, astronaut_id=z.astronaut_id, variable=c.variable, kind=kind, before=before,
                  after=after, rule_id=rule_id, signal_id=signal_id, reverts_id=reverts_id, actor=actor, reason=reason,
                  verified=verified, detail=detail or {})
    s.add(a)
    s.flush()
    audit(s, actor=actor, action=f"cabin.{kind.lower()}", entity="env_action", entity_id=a.id,
          payload={"zone": z.code, "variable": c.variable, "before": before, "after": after, "rule": rule_id,
                   "reason": reason[:300]})
    return a


def raise_alert(s: Session, c: EnvControl, severity: str, rule_id: str, text: str) -> EnvAlert:
    z = _zone(s, c)
    existing = s.scalars(select(EnvAlert).where(EnvAlert.zone_id == c.zone_id, EnvAlert.variable == c.variable,
                                                EnvAlert.rule_id == rule_id, EnvAlert.status == "open")).first()
    if existing is not None:
        return existing
    al = EnvAlert(zone_id=c.zone_id, astronaut_id=z.astronaut_id, variable=c.variable, severity=severity,
                  rule_id=rule_id, text=text)
    s.add(al)
    s.flush()
    audit(s, actor="am-cabin", action="cabin.alert", entity="env_alert", entity_id=al.id,
          payload={"zone": z.code, "variable": c.variable, "severity": severity, "rule": rule_id})
    return al


def _pause(s: Session, c: EnvControl, reason: str) -> None:
    if c.mode == "AUTONOMOUS":
        c.mode, c.mode_reason = "PAUSED", reason
        _log(s, c, "MODE", "P-MODE-SET", "am-cabin", reason=f"Autonomy paused: {reason}", detail={"mode": "PAUSED"})


def _changed_last_hour(s: Session, c: EnvControl) -> float:
    """Total AUTONOMOUS change in the last hour (human changes are not limited by this rule)."""
    since = utcnow() - timedelta(hours=1)
    rows = s.execute(select(EnvAction.before, EnvAction.after).where(
        EnvAction.zone_id == c.zone_id, EnvAction.variable == c.variable, EnvAction.created_at >= since,
        EnvAction.kind == "AUTO")).all()
    return sum(abs((a or 0) - (b or 0)) for b, a in rows if a is not None and b is not None)


def _apply_and_verify(s: Session, c: EnvControl, lim: VarLimits, target: float, act: CabinActuator) -> tuple[bool, str]:
    """Returns (verified, note). Raises ActuatorFault / SensorFault after pausing and alerting.

    A change that cannot be confirmed by a reading is rolled back (the actuator returns to the old value).
    """
    before = c.setpoint
    try:
        act.apply(c, target)
    except ActuatorFault as exc:
        _pause(s, c, str(exc))
        raise_alert(s, c, "WARNING", "P-FAULT-ACT", str(exc))
        raise
    try:
        reading = act.read(c)
    except SensorFault as exc:
        c.setpoint = before
        _pause(s, c, str(exc))
        raise_alert(s, c, "WARNING", "P-FAULT-SENSOR", str(exc))
        raise
    ok = abs(reading - target) <= lim.verify_tolerance
    if not ok:
        raise_alert(s, c, "WATCH", "P-VERIFY", f"{c.variable}: reading {reading} {lim.unit} did not confirm {target} "
                                               f"{lim.unit} (SIMULATED).")
    return ok, f"reading {reading} {lim.unit}"


# ------------------------------------------------------------------ the autonomous loop
_SAY = {
    ("temperature", "up", "en"): "It felt cold, so I raised your quarters from {b:g} to {a:g} °C.",
    ("temperature", "down", "en"): "It felt too warm, so I lowered your quarters from {b:g} to {a:g} °C.",
    ("lighting", "down", "en"): "The light was too bright, so I dimmed your quarters from {b:g} to {a:g} %.",
    ("lighting", "up", "en"): "It was too dark, so I brightened your quarters from {b:g} to {a:g} %.",
    ("temperature", "up", "tr"): "Üşüdüğünü söyledin, kamaranı {b:g} °C'den {a:g} °C'ye çıkardım.",
    ("temperature", "down", "tr"): "Sıcak geldiğini söyledin, kamaranı {b:g} °C'den {a:g} °C'ye düşürdüm.",
    ("lighting", "down", "tr"): "Işık çok parlaktı, kamaranın ışığını %{b:g}'den %{a:g}'e kıstım.",
    ("lighting", "up", "tr"): "Karanlıktı, kamaranın ışığını %{b:g}'den %{a:g}'e açtım.",
}
_UNDO = {"en": " You can undo it in the comfort strip. Did that help?",
         "tr": " İstemezsen konfor şeridinden geri alabilirsin. İşe yaradı mı?"}


def handle_signal(s: Session, astronaut_id: int, message_id: int | None, hit: ComfortHit, lang: str = "en",
                  act: CabinActuator | None = None) -> str | None:
    """Run one comfort signal through the loop. Returns one sentence for the astronaut (or None)."""
    ensure_zones(s)
    act = act or SimulatedCabin()
    env = load_envelope()
    ctx_name = env.context_for_hour(utcnow().hour)
    sig = ComfortSignal(astronaut_id=astronaut_id, message_id=message_id, variable=hit.variable,
                        direction=hit.direction, context=ctx_name, quote=hit.quote, status="review")
    s.add(sig)
    s.flush()
    if hit.variable == "pressure":  # cabin-wide, safety-critical: never adjusted from a complaint
        c = cabin_control(s)
        raise_alert(s, c, "WATCH", "P-PRESSURE-REPORT",
                    f"Astronaut reported an ear/pressure complaint: \"{hit.quote[:160]}\". Review; not adjusted.")
        return ("I noted the pressure in your ears for the medical officer; cabin pressure is not changed from a "
                "comfort request." if lang == "en" else
                "Kulaklarındaki basıncı sağlık görevlisi için not ettim; kabin basıncı konfor isteğiyle değiştirilmez.")
    c = quarters_control(s, astronaut_id, hit.variable)
    if c is None:
        return None
    lim = env.variables[hit.variable]

    def refuse(rule: str, why: str) -> str | None:
        sig.status = "refused"
        _log(s, c, "REFUSED", rule, "am-cabin", before=c.setpoint, after=c.setpoint, signal_id=sig.id, reason=why)
        return None

    if c.mode != "AUTONOMOUS":
        return refuse("P-MODE", f"control is {c.mode}")
    sign = 1 if hit.direction == "up" else -1
    step = lim.step
    pref = s.scalars(select(ComfortPreference).where(ComfortPreference.astronaut_id == astronaut_id,
                                                     ComfortPreference.variable == hit.variable,
                                                     ComfortPreference.context == ctx_name)).first()
    if pref is not None and (pref.preferred - c.setpoint) * sign > 0:  # move toward what helped before
        step = min(step, abs(pref.preferred - c.setpoint))
    target = round(min(max(c.setpoint + sign * step, lim.auto_min), lim.auto_max), 2)
    if target == c.setpoint:
        return refuse("P-ENVELOPE", f"already at the {'upper' if sign > 0 else 'lower'} limit of the autonomous band")
    if _changed_last_hour(s, c) + abs(target - c.setpoint) > lim.max_change_per_hour + 1e-9:
        return refuse("P-RATE", f"more than {lim.max_change_per_hour:g} {lim.unit} per hour")
    before = c.setpoint
    try:
        verified, note = _apply_and_verify(s, c, lim, target, act)
    except (ActuatorFault, SensorFault) as exc:
        return refuse("P-FAULT-ACT" if isinstance(exc, ActuatorFault) else "P-FAULT-SENSOR", str(exc))
    sig.status = "acted"
    _log(s, c, "AUTO", "P-COMFORT-STEP", "am-cabin", before=before, after=target, signal_id=sig.id,
         reason=f"comfort signal: {hit.direction} ({ctx_name})", verified=verified, detail={"note": note})
    text = _SAY.get((hit.variable, hit.direction, lang)) or _SAY[(hit.variable, hit.direction, "en")]
    return text.format(b=before, a=target) + _UNDO[lang if lang in _UNDO else "en"]


# ------------------------------------------------------------------ human control
def set_value(s: Session, c: EnvControl, value: float, *, actor: str, is_doctor: bool, reason: str = "",
              act: CabinActuator | None = None) -> EnvAction:
    """Manual control (always wins: the control goes to MANUAL). Hard limits are never crossed.

    Pressure outside the autonomous band needs the doctor and a logged reason (EXCEPTION).
    """
    act = act or SimulatedCabin()
    lim = load_envelope().variables[c.variable]
    if not lim.min <= value <= lim.max:
        raise CabinError(f"{value:g} {lim.unit} is outside the envelope ({lim.min:g} to {lim.max:g} {lim.unit}).")
    kind, rule = "MANUAL", "P-MANUAL"
    if c.variable == "pressure":
        if not is_doctor:
            raise CabinError("Only the medical officer can change cabin pressure.")
        if not lim.auto_min <= value <= lim.auto_max:
            if not reason.strip():
                raise CabinError("Pressure outside the narrow band needs your explicit approval with a reason.")
            kind, rule = "EXCEPTION", "P-EXCEPTION"
    before = c.setpoint
    try:
        verified, note = _apply_and_verify(s, c, lim, round(value, 2), act)
    except (ActuatorFault, SensorFault) as exc:
        raise CabinError(str(exc)) from exc
    c.mode, c.mode_reason = "MANUAL", f"set by {actor}"
    return _log(s, c, kind, rule, actor, before=before, after=round(value, 2), reason=reason, verified=verified,
                detail={"note": note})


def set_mode(s: Session, c: EnvControl, mode: str, *, actor: str, reason: str = "") -> EnvAction:
    if mode not in MODES:
        raise CabinError(f"mode must be one of {list(MODES)}")
    if mode == "AUTONOMOUS" and c.fault != "none":
        raise CabinError(f"{c.variable} has an active {c.fault} fault; clear it before resuming autonomy.")
    c.mode, c.mode_reason = mode, reason
    return _log(s, c, "MODE", "P-MODE-SET", actor, before=c.setpoint, after=c.setpoint, reason=reason,
                detail={"mode": mode})


def revert(s: Session, action_id: int, *, actor: str, act: CabinActuator | None = None) -> EnvAction:
    a = s.get(EnvAction, action_id)
    if a is None:
        raise LookupError("action not found")
    if a.kind not in ("AUTO", "MANUAL", "EXCEPTION") or a.before is None:
        raise CabinError("only an applied adjustment can be undone")
    if s.scalar(select(EnvAction.id).where(EnvAction.reverts_id == a.id)) is not None:
        raise CabinError("this adjustment was already undone")
    c = s.scalars(select(EnvControl).where(EnvControl.zone_id == a.zone_id, EnvControl.variable == a.variable)).one()
    lim = load_envelope().variables[c.variable]
    act = act or SimulatedCabin()
    before = c.setpoint
    try:
        verified, note = _apply_and_verify(s, c, lim, a.before, act)
    except (ActuatorFault, SensorFault) as exc:
        raise CabinError(str(exc)) from exc
    c.mode, c.mode_reason = "MANUAL", f"undone by {actor}"  # a human acted: autonomy resumes only when re-enabled
    return _log(s, c, "REVERT", "P-REVERT", actor, before=before, after=a.before, reverts_id=a.id, verified=verified,
                reason=f"undo action {a.id}", detail={"note": note})


def feedback(s: Session, action_id: int, helped: bool, *, actor: str) -> EnvAction:
    a = s.get(EnvAction, action_id)
    if a is None:
        raise LookupError("action not found")
    if a.kind != "AUTO":
        raise CabinError("feedback applies to AMC's own adjustments")
    a.feedback = "helped" if helped else "not_helped"
    sig = s.get(ComfortSignal, a.signal_id) if a.signal_id else None
    env = load_envelope()
    if helped and sig is not None:  # the comfort profile is built only from what helped
        p = s.scalars(select(ComfortPreference).where(ComfortPreference.astronaut_id == sig.astronaut_id,
                                                      ComfortPreference.variable == a.variable,
                                                      ComfortPreference.context == sig.context)).first()
        if p is None:
            s.add(ComfortPreference(astronaut_id=sig.astronaut_id, variable=a.variable, context=sig.context,
                                    preferred=a.after))
        else:
            p.preferred = round((p.preferred * p.samples + a.after) / (p.samples + 1), 2)
            p.samples += 1
    elif not helped:
        since = utcnow() - timedelta(hours=env.unresolved_window_hours)
        n = s.scalar(select(func.count()).select_from(EnvAction).where(
            EnvAction.zone_id == a.zone_id, EnvAction.variable == a.variable, EnvAction.feedback == "not_helped",
            EnvAction.created_at >= since)) or 0
        if n >= env.unresolved_feedback_count:
            c = s.scalars(select(EnvControl).where(EnvControl.zone_id == a.zone_id,
                                                   EnvControl.variable == a.variable)).one()
            raise_alert(s, c, "WATCH", "P-UNRESOLVED",
                        f"Repeated unresolved {a.variable} discomfort ({n} adjustments did not help in "
                        f"{env.unresolved_window_hours} h). Review.")
    audit(s, actor=actor, action="cabin.feedback", entity="env_action", entity_id=a.id, payload={"helped": helped})
    s.flush()
    return a


def sense(s: Session, act: CabinActuator | None = None) -> list[EnvAlert]:
    """Read every control; a reading outside the envelope (pressure: its band) pauses autonomy and alerts."""
    ensure_zones(s)
    act = act or SimulatedCabin()
    env = load_envelope()
    raised: list[EnvAlert] = []
    for c in s.scalars(select(EnvControl)):
        lim = env.variables[c.variable]
        try:
            r = act.read(c)
        except SensorFault as exc:
            _pause(s, c, str(exc))
            raised.append(raise_alert(s, c, "WARNING", "P-FAULT-SENSOR", str(exc)))
            continue
        lo, hi = (lim.auto_min, lim.auto_max) if c.variable == "pressure" else (lim.min, lim.max)
        if not lo <= r <= hi:
            severity = "CRITICAL" if not lim.min <= r <= lim.max else "WARNING"
            _pause(s, c, f"reading {r} {lim.unit} outside {lo:g} to {hi:g} {lim.unit}")
            raised.append(raise_alert(s, c, severity, "P-OUT-OF-ENVELOPE",
                                      f"{c.variable} reading {r} {lim.unit} is outside {lo:g} to {hi:g} {lim.unit} "
                                      f"({env.label})."))
    s.flush()
    return raised
