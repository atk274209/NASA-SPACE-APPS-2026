"""Deterministic day marking for the mission journal (DEMO rules).

Every journal day gets a level: red, yellow, green or none. The rules live in
config/day_levels.demo.yaml and are DEMONSTRATION placeholders, not clinical criteria. A level
only summarises which stored records exist for the day; it never diagnoses or judges how
serious anything is, and no language model is involved.

Why it is built this way:
  * The rules are data, so mission medical operations can replace them without code changes.
    The code only implements a small, fixed set of matchers (the rule `kind`).
  * Every reason carries refs to the records that produced it, so a marker can always be
    traced back to stored data.
  * The caller passes facts "as of" the day (records up to the end of that day only), so a
    historical day is never re-coloured by later events.
  * `assess_day` is a pure function of those facts, which keeps it easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from am.ai.checkin import CHECKIN_ITEMS
from am.config import REPO_ROOT
from am.db.enums import Severity
from am.symptoms import catalog

DAY_LEVELS_PATH = REPO_ROOT / "config" / "day_levels.demo.yaml"

LEVELS = ("none", "green", "yellow", "red")
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}

RuleKind = Literal["symptom_reported", "symptom_ongoing", "alert_as_of_day", "journal_flag", "self_report_extreme"]
SYMPTOM_KINDS = ("symptom_reported", "symptom_ongoing")


class DayLevelRule(BaseModel):
    id: str
    level: Literal["red", "yellow"]
    kind: RuleKind
    text: str
    severities: list[str] | None = None  # symptom severities, or alert severities for alert_as_of_day
    categories: list[str] | None = None  # symptom catalog categories
    report_promptly: bool | None = None  # match the DEMO report-promptly list
    flag_prefix: str | None = None
    extremes: dict[str, float] | None = None  # check-in item -> rating at the scale extreme

    @model_validator(mode="after")
    def _check_parameters(self) -> "DayLevelRule":
        """Reject rules whose parameters could never match, so a typo cannot silently disable a rule."""
        if self.kind in SYMPTOM_KINDS:
            if self.severities is not None and not set(self.severities) <= set(catalog.SEVERITIES):
                raise ValueError(f"{self.id}: symptom severities must be among {catalog.SEVERITIES}")
            if self.categories is not None and not set(self.categories) <= set(catalog.CATEGORIES):
                raise ValueError(f"{self.id}: unknown symptom category in {self.categories}")
        elif self.kind == "alert_as_of_day":
            if not self.severities or not set(self.severities) <= {s.value for s in Severity}:
                raise ValueError(f"{self.id}: alert rules need severities among {[s.value for s in Severity]}")
        elif self.kind == "journal_flag":
            if not self.flag_prefix:
                raise ValueError(f"{self.id}: journal_flag rules need flag_prefix")
        elif self.kind == "self_report_extreme":
            if not self.extremes or not set(self.extremes) <= set(CHECKIN_ITEMS):
                raise ValueError(f"{self.id}: extremes must name check-in items {sorted(CHECKIN_ITEMS)}")
        return self

    def detail(self) -> str:
        """The rule's parameters in plain words, for the UI legend."""
        if self.kind == "self_report_extreme" and self.extremes:
            return ", ".join(f"{CHECKIN_ITEMS[k][0].lower()} {v:g}" for k, v in self.extremes.items())
        if self.kind == "journal_flag":
            return f'Flag text starts with "{self.flag_prefix}"'
        parts: list[str] = []
        if self.severities:
            parts.append("severity " + " or ".join(self.severities))
        if self.categories:
            parts.append("category " + " or ".join(self.categories))
        if self.report_promptly is not None:
            parts.append("on the DEMO report-promptly list" if self.report_promptly
                         else "not on the DEMO report-promptly list")
        return "; ".join(parts)


class LevelLegend(BaseModel):
    label: str
    description: str


class DayLevelConfig(BaseModel):
    version: str
    label: str
    note: str
    levels: dict[Literal["red", "yellow", "green", "none"], LevelLegend]
    rules: list[DayLevelRule]

    @model_validator(mode="after")
    def _check(self) -> "DayLevelConfig":
        if set(self.levels) != set(LEVELS):
            raise ValueError(f"levels must describe exactly {LEVELS}")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        return self


@lru_cache
def load_day_level_rules(path: Path = DAY_LEVELS_PATH) -> DayLevelConfig:
    return DayLevelConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------- facts
@dataclass(frozen=True)
class SymptomFact:
    id: int
    code: str
    label: str
    category: str
    severity: str
    report_promptly: bool
    reported_at: datetime
    reported_day: int
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class AlertFact:
    id: int
    metric: str
    context: str
    direction: str
    severity: str  # peak reached by the evidence recorded up to the end of the day


@dataclass(frozen=True)
class RatingFact:
    id: int
    kind: str
    score: float
    recorded_at: datetime


@dataclass
class DayFacts:
    """Records for one mission day, already limited to what was known by the end of that day."""

    day_start: datetime
    day_end: datetime
    has_records: bool
    symptoms_reported: list[SymptomFact] = field(default_factory=list)
    symptoms_ongoing: list[SymptomFact] = field(default_factory=list)
    alerts: list[AlertFact] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    ratings: list[RatingFact] = field(default_factory=list)


@dataclass
class DayAssessment:
    level: str
    reasons: list[dict[str, Any]]


# ---------------------------------------------------------------------------- assessment
def _hhmm(t: datetime) -> str:
    return f"{t:%H:%M} UTC"


def _symptom_matches(rule: DayLevelRule, s: SymptomFact) -> bool:
    if rule.severities is not None and s.severity not in rule.severities:
        return False
    if rule.categories is not None and s.category not in rule.categories:
        return False
    return rule.report_promptly is None or s.report_promptly == rule.report_promptly


def _reported_text(s: SymptomFact) -> str:
    text = f"{s.label} reported as {s.severity} at {_hhmm(s.reported_at)}."
    return text + (" It is on the DEMO report-promptly list." if s.report_promptly else "")


def _ongoing_text(s: SymptomFact, facts: DayFacts) -> str:
    when = "before the mission start" if s.reported_day < 1 else f"on mission day {s.reported_day}"
    if s.resolved_at is not None and s.resolved_at < facts.day_end:
        tail = f"resolved at {_hhmm(s.resolved_at)} this day"
    else:
        tail = "still active at the end of the day"
    return f"Ongoing symptom: {s.label} ({s.severity}), reported {when}; {tail}."


def _alert_text(a: AlertFact) -> str:
    metric = a.metric.replace("_", " ").capitalize()
    return f"{metric} ({a.context}) alert, {a.direction} deviation: {a.severity} by the end of the day."


def _rating_text(r: RatingFact) -> str:
    label = CHECKIN_ITEMS[r.kind][0].lower() if r.kind in CHECKIN_ITEMS else r.kind.replace("_", " ")
    return f"Self-reported {label} {r.score:g}/5 at {_hhmm(r.recorded_at)}."


def assess_day(facts: DayFacts, config: DayLevelConfig) -> DayAssessment:
    """Apply the DEMO rules to one day's facts. Each record gives at most one reason (first match)."""
    reasons: list[dict[str, Any]] = []

    def first(kind: str, match) -> DayLevelRule | None:
        return next((r for r in config.rules if r.kind == kind and match(r)), None)

    def add(rule: DayLevelRule | None, text: str, refs: list[dict[str, Any]]) -> None:
        if rule is not None:
            reasons.append({"rule": rule.id, "level": rule.level, "text": text, "rule_text": rule.text,
                            "refs": refs})

    for s in facts.symptoms_reported:
        add(first("symptom_reported", lambda r, s=s: _symptom_matches(r, s)), _reported_text(s),
            [{"type": "symptom_report", "id": s.id}])
    for s in facts.symptoms_ongoing:
        add(first("symptom_ongoing", lambda r, s=s: _symptom_matches(r, s)), _ongoing_text(s, facts),
            [{"type": "symptom_report", "id": s.id}])
    for a in facts.alerts:
        add(first("alert_as_of_day", lambda r, a=a: a.severity in (r.severities or [])), _alert_text(a),
            [{"type": "alert", "id": a.id}])
    for f in facts.flags:
        text = str(f.get("text", ""))
        add(first("journal_flag", lambda r, t=text: bool(r.flag_prefix) and t.startswith(r.flag_prefix)), text,
            [{"type": "daily_series", "key": f["key"]}] if f.get("key") else [])
    for x in facts.ratings:
        add(first("self_report_extreme", lambda r, x=x: (r.extremes or {}).get(x.kind) == x.score),
            _rating_text(x), [{"type": "self_report", "id": x.id}])

    reasons.sort(key=lambda r: -LEVEL_RANK[r["level"]])  # stable: red reasons first, record order kept
    if reasons:
        level = max((r["level"] for r in reasons), key=LEVEL_RANK.__getitem__)
    else:
        level = "green" if facts.has_records else "none"
    return DayAssessment(level, reasons)
