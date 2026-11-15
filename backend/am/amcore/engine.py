"""AM Core daily cycle: facts -> deterministic assessment -> AM's own decisions -> sealed reports.

Runs in End Day for the closed date. Everything here is deterministic; the local model only writes the
narrative of a report later, from the stored facts (am.amcore.narrative). Behavioural indicators come
from the astronaut's own self-ratings and what they said to AM; they are observations, not clinical
assessments. AM Core never decides medical or emergency status: those stay with the rules, the live
feed and the doctor.

Rule ids:
  A-ATTN-PRIORITY  rule-red report, open WARNING/CRITICAL alert, or two or more declining self-ratings
  A-ATTN-WATCH     a yellow report, an open WATCH alert, one declining self-rating, an overdue exam
  A-TREND          a self-rating's 3-day mean moved by >= TREND_DELTA from the 3 days before (DEMO rule)
  A-DOCTOR         notice for the doctor: priority attention, or check-ins missed two days running
  A-CHECKIN        check-in plan for tomorrow (AM opens the conversation)
  A-SLEEP          sleep self-rating declining: keep sleep logging prompts first
  A-CREW-COHESION  conflict or isolation mentioned by two or more crew members
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.db.enums import AlertStatus
from am.db.models import Alert, Astronaut
from am.db.models_ai import Conversation, Message, SelfReport
from am.db.models_amcore import AmDecision, AmReport
from am.db.models_cabin import ComfortSignal, EnvAction
from am.db.models_m5 import SymptomReport
from am.db.models_m6 import AiNote
from am.db.models_m6a import AstronautProfile
from am.db.models_m6b import ReportFlag, SupplementLog
from am.db.models_research import DailyTask, JournalEntry
from am.symptoms.detect import detect_keywords

TREND_DELTA = 1.0  # DEMO rule on the 1-5 self-rating scale, not a clinical threshold
HIGHER_IS_BETTER = {"mood": True, "motivation": True, "sleep_quality": True, "stress": False, "fatigue": False}
BEHAVIOURAL = ("low_mood", "anxiety", "irritability", "loneliness", "insomnia", "self_harm_thoughts")
COHESION = ("irritability", "loneliness")


def _bounds(d: date):
    from datetime import datetime, time

    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


def _scores(s: Session, astronaut_id: int, d0, d1) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for r in s.scalars(select(SelfReport).where(SelfReport.astronaut_id == astronaut_id, SelfReport.recorded_at >= d0,
                                                SelfReport.recorded_at < d1, SelfReport.kind.in_(HIGHER_IS_BETTER))):
        out.setdefault(r.kind, []).append(r.score)
    return out


def _trend(s: Session, astronaut_id: int, d: date) -> dict[str, Any]:
    recent0, _ = _bounds(d - timedelta(days=2))
    prev0, _ = _bounds(d - timedelta(days=5))
    _, end = _bounds(d)
    recent, previous = _scores(s, astronaut_id, recent0, end), _scores(s, astronaut_id, prev0, recent0)
    out = {}
    for kind, better_high in HIGHER_IS_BETTER.items():
        r, p = recent.get(kind, []), previous.get(kind, [])
        if len(r) < 2 or len(p) < 2:
            continue
        rm, pm = sum(r) / len(r), sum(p) / len(p)
        change = rm - pm
        if abs(change) < TREND_DELTA:
            direction = "stable"
        else:
            direction = "improving" if (change > 0) == better_high else "declining"
        out[kind] = {"recent_mean": round(rm, 1), "previous_mean": round(pm, 1), "direction": direction}
    return out


def astronaut_facts(s: Session, a: Astronaut, d: date, mission_day: int) -> dict[str, Any]:
    from am.exams.service import exams_for
    from am.nutrition.service import day_total, load_config

    d0, d1 = _bounds(d)
    prof = s.get(AstronautProfile, a.id)
    journal = s.scalars(select(JournalEntry).where(JournalEntry.astronaut_id == a.id,
                                                   JournalEntry.mission_day == mission_day)).first()
    alerts = s.scalars(select(Alert).where(Alert.astronaut_id == a.id,
                                           Alert.status != AlertStatus.RESOLVED.value)).all()
    flags = s.scalars(select(ReportFlag).where(ReportFlag.astronaut_id == a.id, ReportFlag.created_at >= d0,
                                               ReportFlag.created_at < d1)).all()
    active = s.scalars(select(SymptomReport).where(SymptomReport.astronaut_id == a.id,
                                                   SymptomReport.status == "active")).all()
    _, exam_views = exams_for(s, a.id)
    target = load_config()["hydration"]["daily_target_ml"]
    water = day_total(s, a.id, mission_day)
    supps = list(s.scalars(select(SupplementLog.supplement_code).where(SupplementLog.astronaut_id == a.id,
                                                                       SupplementLog.mission_day == mission_day)))
    today_scores = {k: v[-1] for k, v in _scores(s, a.id, d0, d1).items()}
    said = list(s.scalars(select(Message.content).join(Conversation, Message.conversation_id == Conversation.id)
                          .where(Conversation.astronaut_id == a.id, Message.role == "user", Message.created_at >= d0,
                                 Message.created_at < d1)))
    notes = Counter(s.scalars(select(AiNote.category).where(AiNote.astronaut_id == a.id, AiNote.noted_at >= d0,
                                                            AiNote.noted_at < d1)))
    topics = {r.symptom_code for r in active if r.symptom_code in BEHAVIOURAL}
    for text in said:
        topics |= {x.code for x in detect_keywords(text) if x.code in BEHAVIOURAL}
    tasks = s.scalars(select(DailyTask).where(DailyTask.astronaut_id == a.id, DailyTask.mission_day == mission_day)).all()
    signals = s.scalars(select(ComfortSignal).where(ComfortSignal.astronaut_id == a.id, ComfortSignal.created_at >= d0,
                                                    ComfortSignal.created_at < d1)).all()
    actions = s.scalars(select(EnvAction).where(EnvAction.astronaut_id == a.id, EnvAction.created_at >= d0,
                                                EnvAction.created_at < d1)).all()
    return {
        "astronaut": {"id": a.id, "code": a.code, "name": a.display_name, "mission_day": mission_day,
                      "known_conditions_fictional": list(prof.chronic_conditions) if prof else []},
        "simulated": a.is_simulated,
        "health": {
            "day_level": journal.day_level if journal else None,
            "journal_entry_id": journal.id if journal else None,
            "open_alerts": [{"id": x.id, "metric": x.metric_code, "context": x.context, "severity": x.severity,
                             "peak": x.peak_severity, "rule": x.rule_id} for x in alerts],
            "reports_today": {"red": sum(f.rule_level == "red" for f in flags),
                              "yellow": sum(f.rule_level == "yellow" for f in flags), "total": len(flags)},
            "awaiting_approval": sum(f.approval_status == "awaiting" for f in flags),
            "active_symptoms": [{"code": r.symptom_code, "severity": r.severity, "report_id": r.id} for r in active],
            "overdue_exams": [v.exam.exam_code for v in exam_views if v.state == "overdue"],
            "hydration": {"total_ml": water, "target_ml": target, "met": water >= target},
            "supplements_taken": supps,
        },
        "behaviour": {
            "self_ratings_today": today_scores,
            "trend": _trend(s, a.id, d),
            "messages_to_am": len(said),
            "notes_by_category": dict(notes),
            "behavioural_topics": sorted(topics),
            "missions": {"done": sum(t.status == "done" for t in tasks), "total": len(tasks)},
            "checked_in": bool(today_scores),
        },
        "habitat": {
            "comfort_signals": [{"variable": x.variable, "direction": x.direction, "status": x.status} for x in signals],
            "adjustments": sum(x.kind == "AUTO" for x in actions),
            "not_helped": sum(x.feedback == "not_helped" for x in actions),
        },
    }


def assess(facts: dict[str, Any], checked_in_yesterday: bool) -> dict[str, Any]:
    h, b = facts["health"], facts["behaviour"]
    priority, watch = [], []
    if h["reports_today"]["red"]:
        priority.append(f"{h['reports_today']['red']} report(s) today flagged red by the rules")
    sev = {x["severity"] for x in h["open_alerts"]} | {x["peak"] for x in h["open_alerts"]}
    if sev & {"WARNING", "CRITICAL"}:
        priority.append("open alert at WARNING or CRITICAL")
    declining = [k for k, v in b["trend"].items() if v["direction"] == "declining"]
    if len(declining) >= 2:
        priority.append("self-ratings declining: " + ", ".join(declining))
    elif declining:
        watch.append(f"self-rating declining: {declining[0]}")
    if h["reports_today"]["yellow"]:
        watch.append(f"{h['reports_today']['yellow']} report(s) today flagged yellow")
    if "WATCH" in sev:
        watch.append("open alert at WATCH")
    if h["overdue_exams"]:
        watch.append("overdue exam(s): " + ", ".join(h["overdue_exams"]))
    level = "priority" if priority else "watch" if watch else "routine"
    behaviour_lines = [f"{k.replace('_', ' ')}: {v['direction']} (3-day mean {v['recent_mean']} vs {v['previous_mean']})"
                       for k, v in b["trend"].items()]
    if b["behavioural_topics"]:
        behaviour_lines.append("topics raised: " + ", ".join(t.replace("_", " ") for t in b["behavioural_topics"]))
    behaviour_lines.append(f"talked to AM {b['messages_to_am']} time(s); checked in: {'yes' if b['checked_in'] else 'no'}")
    return {"attention": level, "reasons": priority + watch, "declining": declining,
            "missed_checkins_two_days": not b["checked_in"] and not checked_in_yesterday,
            "behaviour": behaviour_lines,
            "label": "AM's observations from stored records and self-ratings. Not a diagnosis; behavioural lines "
                     "are self-report trends (DEMO rule), not clinical assessments."}


def decide(s: Session, d: date, a_id: int, facts: dict[str, Any], assessment: dict[str, Any]) -> list[AmDecision]:
    out: list[AmDecision] = []
    name = facts["astronaut"]["name"]
    lvl = assessment["attention"]
    rule = {"priority": "A-ATTN-PRIORITY", "watch": "A-ATTN-WATCH"}.get(lvl, "A-ATTN-ROUTINE")
    out.append(AmDecision(close_date=d, astronaut_id=a_id, category="attention", level=lvl,
                          decision=f"Attention for {name} tomorrow: {lvl}.",
                          rationale="; ".join(assessment["reasons"]) or "nothing flagged",
                          rule_id=rule, visible_to_doctor=True, executed=True))
    cadence = "two check-ins (morning and evening)" if lvl != "routine" else "one check-in"
    out.append(AmDecision(close_date=d, astronaut_id=a_id, category="checkin", level=lvl,
                          decision=f"Plan {cadence} with {name} tomorrow.", rationale=f"attention {lvl}",
                          rule_id="A-CHECKIN", visible_to_doctor=False, executed=False))
    if lvl == "priority" or assessment["missed_checkins_two_days"]:
        why = "priority attention" if lvl == "priority" else "no check-in two days running"
        out.append(AmDecision(close_date=d, astronaut_id=a_id, category="doctor_notice", level=lvl,
                              decision=f"Recommend the medical officer contacts {name}.", rationale=why,
                              rule_id="A-DOCTOR", visible_to_doctor=True, executed=False))
    if "sleep_quality" in assessment["declining"]:
        out.append(AmDecision(close_date=d, astronaut_id=a_id, category="mission_focus", level=lvl,
                              decision=f"Keep sleep logging prompts first in {name}'s missions.",
                              rationale="sleep self-rating declining", rule_id="A-SLEEP",
                              visible_to_doctor=True, executed=True))
    return out


def crew_report_facts(members: list[dict[str, Any]], assessments: dict[int, dict[str, Any]]) -> dict[str, Any]:
    topics = Counter(t for m in members for t in m["behaviour"]["behavioural_topics"])
    cohesion = {t: n for t, n in topics.items() if t in COHESION}
    total_tasks = sum(m["behaviour"]["missions"]["total"] for m in members)
    return {
        "crew_size": len(members),
        "attention": {m["astronaut"]["code"]: assessments[m["astronaut"]["id"]]["attention"] for m in members},
        "checked_in": sum(m["behaviour"]["checked_in"] for m in members),
        "missions_done_pct": round(100 * sum(m["behaviour"]["missions"]["done"] for m in members) / total_tasks)
        if total_tasks else None,
        "hydration_target_met": sum(m["health"]["hydration"]["met"] for m in members),
        "red_reports": sum(m["health"]["reports_today"]["red"] for m in members),
        "awaiting_approval": sum(m["health"]["awaiting_approval"] for m in members),
        "declining_self_ratings": Counter(k for m in members for k, v in m["behaviour"]["trend"].items()
                                          if v["direction"] == "declining"),
        "behavioural_topics": dict(topics),
        "cohesion_topics": cohesion,
        "habitat_signals": sum(len(m["habitat"]["comfort_signals"]) for m in members),
        "habitat_not_helped": sum(m["habitat"]["not_helped"] for m in members),
    }


def template_narrative(kind: str, facts: dict[str, Any], assessment: dict[str, Any]) -> str:
    """Deterministic narrative used until (or when) the local model writes one."""
    if kind == "crew_day":
        att = ", ".join(f"{c}: {lvl}" for c, lvl in facts["attention"].items())
        return (f"Crew status. Attention: {att}. {facts['checked_in']} of {facts['crew_size']} checked in; "
                f"{facts['hydration_target_met']} met the water target; missions done "
                f"{facts['missions_done_pct'] if facts['missions_done_pct'] is not None else '—'} %. "
                f"Red reports: {facts['red_reports']}, awaiting approval: {facts['awaiting_approval']}. "
                f"Crew attitude: {', '.join(f'{k} x{v}' for k, v in facts['behavioural_topics'].items()) or 'no behavioural topics raised'}. "
                f"Habitat: {facts['habitat_signals']} comfort signal(s), {facts['habitat_not_helped']} unresolved.")
    h = facts["health"]
    return (f"{facts['astronaut']['name']}, mission day {facts['astronaut']['mission_day']}. Attention: "
            f"{assessment['attention']} ({'; '.join(assessment['reasons']) or 'nothing flagged'}). "
            f"Health: day level {h['day_level'] or '—'}, {len(h['open_alerts'])} open alert(s), "
            f"{len(h['active_symptoms'])} active symptom(s), water {h['hydration']['total_ml']:.0f} of "
            f"{h['hydration']['target_ml']} mL. Behaviour: {'; '.join(assessment['behaviour'])}.")


def run_cycle(s: Session, d: date, crew: list[Any]) -> dict[str, Any]:
    """crew: CrewMemberDay items from am.endday.pipeline. Idempotent per close date."""
    if s.scalar(select(AmReport.id).where(AmReport.kind == "crew_day", AmReport.close_date == d)) is not None:
        return {"skipped": "already run for this date"}
    members, assessments, decisions = [], {}, 0
    for m in crew:
        a = s.get(Astronaut, m.astronaut_id)
        facts = astronaut_facts(s, a, d, m.mission_day)
        y0, y1 = _bounds(d - timedelta(days=1))
        checked_yesterday = bool(_scores(s, a.id, y0, y1)) or m.mission_day <= 1
        assessment = assess(facts, checked_yesterday)
        s.add(AmReport(kind="astronaut_day", astronaut_id=a.id, close_date=d, mission_day=m.mission_day, facts=facts,
                       assessment=assessment, narrative=template_narrative("astronaut_day", facts, assessment),
                       narrative_status="pending", is_simulated=a.is_simulated))
        for dec in decide(s, d, a.id, facts, assessment):
            s.add(dec)
            decisions += 1
        members.append(facts)
        assessments[a.id] = assessment
    cfacts = crew_report_facts(members, assessments)
    crew_assessment = {"label": "AM's crew-level observations. Not a clinical or psychological assessment."}
    raised_by = sum(any(t in COHESION for t in m["behaviour"]["behavioural_topics"]) for m in members)
    cfacts["cohesion_raised_by"] = raised_by
    if raised_by >= 2:
        s.add(AmDecision(close_date=d, astronaut_id=None, category="crew", level="watch",
                         decision="Recommend a crew behavioural-health check-in with the medical officer.",
                         rationale="conflict or isolation raised by two or more crew members", rule_id="A-CREW-COHESION",
                         visible_to_doctor=True, executed=False))
        decisions += 1
    s.add(AmReport(kind="crew_day", astronaut_id=None, close_date=d, facts=cfacts, assessment=crew_assessment,
                   narrative=template_narrative("crew_day", cfacts, crew_assessment), narrative_status="pending"))
    s.flush()
    return {"astronaut_reports": len(members), "decisions": decisions}
