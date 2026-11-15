"""Hydration and supplement logging, and the crew hydration leaderboard (DEMO target).

The leaderboard ranks ADHERENCE to the daily target, capped at 100 %: drinking more than the target
earns nothing extra, so the game never rewards over-drinking. It shows no amounts and only astronauts
who opted in (they can leave it any time from their profile).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.config import REPO_ROOT
from am.db.base import utcnow
from am.db.enums import SourceKind
from am.db.models import Astronaut, DataSource, Measurement
from am.db.models_m6a import AstronautProfile, DataOrigin
from am.db.models_m6b import HydrationLog, SupplementLog
from am.research.series import current_mission_day, day_bounds, mission_start

CONFIG_PATH = REPO_ROOT / "config" / "nutrition.demo.yaml"
SOURCE_NAME = "AM hydration log (daily total)"
LEADERBOARD_DAYS = 7


@lru_cache
def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


class NutritionError(Exception):
    """Refused; safe to show."""


def _today(s: Session, astronaut_id: int) -> int:
    day = current_mission_day(s, astronaut_id)
    if day is None:
        raise NutritionError("no mission: mission days are undefined")
    return day


def log_water(s: Session, astronaut_id: int, amount_ml: float, *, source: str, actor: str,
              origin: str = DataOrigin.MANUAL_TEST_ENTRY.value) -> HydrationLog:
    cap = load_config()["hydration"]["max_single_entry_ml"]
    if not 0 < amount_ml <= cap:
        raise NutritionError(f"one entry must be between 1 and {cap} mL (check for a typing error)")
    row = HydrationLog(astronaut_id=astronaut_id, mission_day=_today(s, astronaut_id), amount_ml=float(amount_ml),
                       source=source, data_origin=origin)
    s.add(row)
    s.flush()
    audit(s, actor=actor, action="hydration.logged", entity="hydration_log", entity_id=row.id,
          payload={"amount_ml": amount_ml, "source": source})
    return row


def undo_water(s: Session, water_id: int, *, actor: str) -> None:
    row = s.get(HydrationLog, water_id)
    if row is None:
        raise LookupError("entry not found")
    if row.mission_day != current_mission_day(s, row.astronaut_id):
        raise NutritionError("only today's entries can be removed; closed days are locked")
    s.delete(row)
    audit(s, actor=actor, action="hydration.removed", entity="hydration_log", entity_id=water_id)
    s.flush()


def log_supplement(s: Session, astronaut_id: int, code: str, taken: bool, *, actor: str) -> None:
    if code not in {x["code"] for x in load_config()["supplements"]}:
        raise NutritionError(f"unknown supplement '{code}'")
    day = _today(s, astronaut_id)
    row = s.scalars(select(SupplementLog).where(SupplementLog.astronaut_id == astronaut_id,
                                                SupplementLog.mission_day == day,
                                                SupplementLog.supplement_code == code)).first()
    if taken and row is None:
        s.add(SupplementLog(astronaut_id=astronaut_id, mission_day=day, supplement_code=code))
    elif not taken and row is not None:
        s.delete(row)
    audit(s, actor=actor, action="supplement.logged" if taken else "supplement.unlogged", entity="supplement_log",
          entity_id=None, payload={"astronaut_id": astronaut_id, "code": code, "mission_day": day})
    s.flush()


def day_total(s: Session, astronaut_id: int, day: int) -> float:
    return float(s.scalar(select(func.coalesce(func.sum(HydrationLog.amount_ml), 0)).where(
        HydrationLog.astronaut_id == astronaut_id, HydrationLog.mission_day == day)) or 0)


def today_status(s: Session, astronaut_id: int) -> dict[str, Any]:
    cfg = load_config()
    day = _today(s, astronaut_id)
    target = cfg["hydration"]["daily_target_ml"]
    total = day_total(s, astronaut_id, day)
    entries = s.scalars(select(HydrationLog).where(HydrationLog.astronaut_id == astronaut_id,
                                                   HydrationLog.mission_day == day).order_by(HydrationLog.id)).all()
    taken = set(s.scalars(select(SupplementLog.supplement_code).where(SupplementLog.astronaut_id == astronaut_id,
                                                                      SupplementLog.mission_day == day)))
    return {"mission_day": day, "label": cfg["label"], "target_ml": target, "total_ml": total,
            "progress": min(total / target, 1.0) if target else 0.0, "target_met": total >= target,
            "quick_add_ml": cfg["hydration"]["quick_add_ml"],
            "entries": [{"id": e.id, "amount_ml": e.amount_ml, "source": e.source, "logged_at": e.logged_at}
                        for e in entries],
            "supplements": [{"code": x["code"], "label": x["label"], "taken": x["code"] in taken}
                            for x in cfg["supplements"]]}


def close_day(s: Session, ctx: Any, astronaut_id: int, day: int) -> dict[str, Any]:
    """End Day: store the day's total as one water_intake measurement, and adherence points."""
    from am.exams.service import award
    from am.health.ingest import IngestService, MeasurementInput

    cfg = load_config()
    total = day_total(s, astronaut_id, day)
    start = mission_start(s, astronaut_id)
    out: dict[str, Any] = {"total_ml": total, "measurement_id": None, "points": 0}
    if total > 0 and start is not None:
        d0, d1 = day_bounds(start, day)
        exists = s.scalar(select(Measurement.id).join(DataSource, Measurement.source_id == DataSource.id).where(
            Measurement.astronaut_id == astronaut_id, Measurement.metric_code == "water_intake",
            Measurement.recorded_at >= d0, Measurement.recorded_at < d1, DataSource.name == SOURCE_NAME))
        if exists is None:
            at = min(d1.replace(microsecond=0) - (d1 - d0) / 48, utcnow())  # 23:30 of the closed day
            res = IngestService(s, ctx.thresholds, ctx.policy).ingest(MeasurementInput(
                astronaut_id=astronaut_id, metric_code="water_intake", value=total, unit="mL", context="daily",
                recorded_at=at, source_name=SOURCE_NAME, source_kind=SourceKind.MANUAL))
            out["measurement_id"] = res.measurement.id
        if total >= cfg["hydration"]["daily_target_ml"]:
            out["points"] += cfg["points"]["hydration_target_met"] * award(
                s, astronaut_id, "hydration_target_met", f"water:{day}", cfg["points"]["hydration_target_met"], day)
    if s.scalar(select(func.count()).select_from(SupplementLog).where(SupplementLog.astronaut_id == astronaut_id,
                                                                     SupplementLog.mission_day == day)):
        out["points"] += cfg["points"]["supplements_logged"] * award(
            s, astronaut_id, "supplements_logged", f"supp:{day}", cfg["points"]["supplements_logged"], day)
    s.flush()
    return out


@dataclass
class BoardRow:
    astronaut_id: int
    name: str
    code: str
    days_met: int
    days_counted: int
    adherence_pct: int
    streak: int


def leaderboard(s: Session) -> dict[str, Any]:
    target = load_config()["hydration"]["daily_target_ml"]
    rows: list[BoardRow] = []
    for a in s.scalars(select(Astronaut).order_by(Astronaut.code)):
        prof = s.get(AstronautProfile, a.id)
        if prof is not None and not prof.leaderboard_opt_in:
            continue
        today = current_mission_day(s, a.id)
        if today is None:
            continue
        days = [d for d in range(today - LEADERBOARD_DAYS + 1, today + 1) if d >= 1]
        totals = {d: day_total(s, a.id, d) for d in days}
        ratios = [min(t / target, 1.0) for t in totals.values()]  # capped: no reward for over-drinking
        streak = 0
        for d in sorted(days, reverse=True):
            if totals[d] >= target:
                streak += 1
            elif d != today:  # today is still open: it does not break the streak
                break
        rows.append(BoardRow(a.id, a.display_name, a.code, sum(t >= target for t in totals.values()), len(days),
                             round(100 * sum(ratios) / len(ratios)) if ratios else 0, streak))
    rows.sort(key=lambda r: (-r.days_met, -r.adherence_pct, -r.streak, r.code))
    return {"window_days": LEADERBOARD_DAYS, "label": load_config()["label"],
            "rule": "Ranked by days the daily target was met and by adherence capped at 100 %: drinking more "
                    "than the target earns nothing extra. No amounts are shown. Astronauts can leave the board.",
            "rows": [r.__dict__ | {"rank": i + 1} for i, r in enumerate(rows)]}


def set_opt_in(s: Session, astronaut_id: int, opt_in: bool, *, actor: str) -> bool:
    prof = s.get(AstronautProfile, astronaut_id)
    if prof is None:
        prof = AstronautProfile(astronaut_id=astronaut_id)
        s.add(prof)
    prof.leaderboard_opt_in = opt_in
    audit(s, actor=actor, action="leaderboard.opt_in" if opt_in else "leaderboard.opt_out", entity="astronaut",
          entity_id=astronaut_id)
    s.flush()
    return opt_in

