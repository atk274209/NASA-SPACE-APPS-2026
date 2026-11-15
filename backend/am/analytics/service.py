"""Crew analytics for the doctor's bento board (four live cards).

Every number comes from stored records; nothing is estimated. Self-ratings are 1-5 self-reports,
not clinical scales; the water target is the DEMO target from config/nutrition.demo.yaml.

  A. water:      crew water per mission date (hydration log), last 14 dates
  B. activity:   per hour, what the crew wrote to AM (up) and what was flagged yellow/red (down),
                 today and the two days before
  C. problems:   symptom reports by body system and symptom: today / 7 days / mission, per astronaut
  D. wellbeing:  per astronaut, 8 axes on 0-100 (5 self-ratings, water, missions, check-ins), last 7
                 days, with the week before for comparison
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.db.base import utcnow
from am.db.models import Astronaut
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_m6b import HydrationLog, ReportFlag
from am.db.models_research import DailyTask
from am.nutrition.service import load_config as nutrition_config
from am.research.series import current_mission_day, mission_start
from am.symptoms import catalog

WATER_DAYS = 14
WAVE_DAYS = 3
# Distinct, readable on the dark console; one colour per body system (no meaning implied).
SYSTEM_COLORS = {
    "neuro_behavioral": ("#9d8cf0", "dark"), "eyes": ("#5fb3f2", "dark"), "cardiovascular": ("#ef6a75", "dark"),
    "respiratory": ("#3fc1c9", "dark"), "gastrointestinal": ("#e8a33a", "dark"), "musculoskeletal": ("#7ccf8e", "dark"),
    "immune_skin": ("#d98cc4", "dark"), "metabolic": ("#c9c26a", "dark"),
}


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _pct_delta(now: float, before: float) -> str:
    if before == 0:
        return "new" if now else "±0"
    pct = round(100 * (now - before) / before)
    return f"{pct:+d}%"


# ------------------------------------------------------------------ A. water
def water(s: Session) -> dict[str, Any]:
    today = utcnow().date()
    days = [today - timedelta(days=k) for k in range(WATER_DAYS - 1, -1, -1)]
    totals: dict[date, float] = defaultdict(float)
    for r in s.scalars(select(HydrationLog).where(HydrationLog.logged_at >= _day_start(days[0]))):
        totals[r.logged_at.date()] += r.amount_ml
    crew = len(s.scalars(select(Astronaut.id)).all())
    target = nutrition_config()["hydration"]["daily_target_ml"]
    now, before = totals[today], totals[today - timedelta(days=1)]
    return {
        "label": "Crew water today", "unit": "mL", "value": round(now), "delta": _pct_delta(now, before),
        "note": f"vs yesterday · DEMO target {target / 1000:g} L per person × {crew}",
        "caption": f"Crew water, last {WATER_DAYS} days (mL)",
        "series": [{"label": d.strftime("%b %d"), "value": round(totals[d])} for d in days],
    }


# ------------------------------------------------------------------ B. activity waves
def activity(s: Session) -> dict[str, Any]:
    today = utcnow().date()
    written = [[0] * 24 for _ in range(WAVE_DAYS)]
    flagged = [[0] * 24 for _ in range(WAVE_DAYS)]
    for f in s.scalars(select(ReportFlag).where(ReportFlag.created_at >= _day_start(today - timedelta(days=WAVE_DAYS - 1)))):
        k = (today - f.created_at.date()).days
        if 0 <= k < WAVE_DAYS:
            written[k][f.created_at.hour] += 1
            if f.level in ("yellow", "red"):
                flagged[k][f.created_at.hour] += 1
    now, before = sum(written[0]), sum(written[1])
    return {
        "label": "Written to AM today", "value": now, "delta": _pct_delta(now, before),
        "note": f"{sum(flagged[0])} flagged yellow or red today",
        "written": written, "flagged": flagged,
        "labels": {"written": "Written to AM", "flagged": "Flagged yellow/red", "prior": "Prior days", "current": "today"},
    }


# ------------------------------------------------------------------ C. problems treemap
def problems(s: Session) -> dict[str, Any]:
    now = utcnow()
    today0 = _day_start(now.date())
    windows = [("Today", today0, now), ("Last 7 days", today0 - timedelta(days=6), now), ("Mission", datetime.min, now)]
    previous = [(today0 - timedelta(days=1), today0), (today0 - timedelta(days=13), today0 - timedelta(days=6)), None]
    reports = s.scalars(select(SymptomReport).where(SymptomReport.status != "retracted")).all()
    crew = {a.id: a for a in s.scalars(select(Astronaut).order_by(Astronaut.code))}

    def count(r_list, lo, hi) -> Counter:
        return Counter(r.symptom_code for r in r_list if lo <= r.reported_at <= hi)

    per_period = [count(reports, lo, hi) for _, lo, hi in windows]
    codes = sorted({c for p in per_period for c in p})
    periods = []
    for i, (label, lo, hi) in enumerate(windows):
        total = sum(per_period[i].values())
        prev = previous[i]
        if prev is not None:
            before = sum(count(reports, prev[0], prev[1]).values())
            delta, compare = _pct_delta(total, before), f"vs {'yesterday' if i == 0 else 'the 7 days before'} ({before})"
        else:
            delta, compare = f"{len({r.astronaut_id for r in reports})} crew", "since mission day 1"
        periods.append({"label": label, "total": total, "delta": delta, "compare": compare})
    groups = [{"id": k, "name": v, "color": SYSTEM_COLORS[k][0], "ink": SYSTEM_COLORS[k][1]}
              for k, v in catalog.BODY_SYSTEMS.items()]
    items = []
    for c in codes:
        sym = catalog.get(c)
        items.append({"name": sym.label if sym else c, "group": sym.body_system if sym else "neuro_behavioral",
                      "values": [p[c] for p in per_period]})
    rows = []
    for a in crew.values():
        mine = [r for r in reports if r.astronaut_id == a.id]
        rows.append({"label": f"{a.display_name} ({a.code})",
                     "values": [str(sum(count(mine, lo, hi).values())) for _, lo, hi in windows]})
    return {"title": "Reported problems by body system", "groups": groups, "periods": periods, "items": items,
            "table": {"columns": ["Crew member", "Today", "7 days", "Mission"], "rows": rows}}


# ------------------------------------------------------------------ D. wellbeing radar
AXES = [("mood", "Mood", False), ("stress", "Calm", True), ("fatigue", "Energy", True),
        ("sleep_quality", "Sleep", False), ("motivation", "Motivation", False)]


def _week_values(s: Session, a: Astronaut, end: date) -> tuple[list[int], dict[str, str]]:
    start = end - timedelta(days=6)
    lo, hi = _day_start(start), _day_start(end + timedelta(days=1))
    scores: dict[str, list[float]] = defaultdict(list)
    checkin_days: set[date] = set()
    for r in s.scalars(select(SelfReport).where(SelfReport.astronaut_id == a.id, SelfReport.recorded_at >= lo,
                                                SelfReport.recorded_at < hi)):
        scores[r.kind].append(r.score)
        checkin_days.add(r.recorded_at.date())
    values, rows = [], {}
    for kind, label, inverted in AXES:
        v = scores.get(kind)
        if v:
            m = sum(v) / len(v)
            values.append(round(100 * ((5 - m) if inverted else (m - 1)) / 4))
            rows[label] = f"{m:.1f} / 5{' (lower is better)' if inverted else ''}"
        else:
            values.append(0)
            rows[label] = "not recorded"
    target = nutrition_config()["hydration"]["daily_target_ml"]
    ms = mission_start(s, a.id)
    today_md = current_mission_day(s, a.id) or 0
    md_end = today_md - (utcnow().date() - end).days
    days = [d for d in range(md_end - 6, md_end + 1) if d >= 1]
    w = []
    for d in days:
        tot = sum(x.amount_ml for x in s.scalars(select(HydrationLog).where(HydrationLog.astronaut_id == a.id,
                                                                          HydrationLog.mission_day == d)))
        w.append(min(tot / target, 1.0))
    water_pct = round(100 * sum(w) / len(w)) if w else 0
    tasks = s.scalars(select(DailyTask).where(DailyTask.astronaut_id == a.id, DailyTask.mission_day.in_(days))).all() \
        if days else []
    missions_pct = round(100 * sum(t.status == "done" for t in tasks) / len(tasks)) if tasks else 0
    span = len(days) if ms else 7
    checkin_pct = round(100 * len(checkin_days) / max(span, 1))
    values += [water_pct, missions_pct, checkin_pct]
    rows |= {"Water": f"{water_pct}% of DEMO target (capped)", "Missions": f"{missions_pct}% of tasks done",
             "Check-ins": f"{len(checkin_days)} of {span} day(s)"}
    return values, rows


def wellbeing(s: Session) -> dict[str, Any]:
    today = utcnow().date()
    out = []
    for a in s.scalars(select(Astronaut).order_by(Astronaut.code)):
        now, rows = _week_values(s, a, today)
        before, _ = _week_values(s, a, today - timedelta(days=7))
        well_now, well_before = round(sum(now[:5]) / 5), round(sum(before[:5]) / 5)
        adh_now, adh_before = round(sum(now[5:]) / 3), round(sum(before[5:]) / 3)
        out.append({
            "label": a.code, "name": a.display_name, "values": now,
            "stats": [
                {"label": "Wellbeing index", "value": well_now, "delta": f"{well_now - well_before:+d}",
                 "note": "pts vs prior week"},
                {"label": "Adherence index", "value": adh_now, "delta": f"{adh_now - adh_before:+d}",
                 "note": "pts vs prior week"},
            ],
            "rows": [{"label": k, "value": v} for k, v in rows.items()],
        })
    return {"title": "Wellbeing and adherence, last 7 days",
            "axis": [label for _, label, _ in AXES] + ["Water", "Missions", "Check-ins"],
            "note": "0-100: self-ratings (1-5, not clinical scales; stress and fatigue inverted) and adherence.",
            "years": out}


def crew_analytics(s: Session) -> dict[str, Any]:
    return {"water": water(s), "activity": activity(s), "problems": problems(s), "wellbeing": wellbeing(s),
            "generated_at": utcnow()}
