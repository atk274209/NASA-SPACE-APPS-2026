"""Deterministic extraction of health values the astronaut STATES in conversation (Module L).

English and Turkish. Only numbers the astronaut wrote are taken; nothing is estimated. A missing
activity context stays empty (the astronaut picks it when confirming). Every hit carries the exact
sentence it came from, so the record links back to the source message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from am.symptoms.detect import _ascii, sentence_at

NUM = r"(\d{1,4}(?:[.,]\d{1,2})?)"


@dataclass(frozen=True)
class ValueHit:
    metric_code: str
    value: float
    unit: str
    context: str | None  # None: ask the astronaut
    quote: str


_REST = re.compile(r"\b(rest|resting|at rest|woke|wake|morning|dinlen\w*|istirahat\w*|uyan\w*|sabah)\b")
_EXERCISE = re.compile(r"\b(exercise|workout|training|run|running|treadmill|cycle|egzersiz\w*|spor\w*|kosu\w*|antrenman\w*)\b")
_SLEEPING = re.compile(r"\b(asleep|during sleep|uykuda|uyurken)\b")

_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("heart_rate", "bpm", re.compile(rf"\b(?:heart ?rate|pulse|hr|nabz\w*|kalp (?:hiz\w*|atis\w*))\D{{0,20}}?{NUM}")),
    ("spo2", "%", re.compile(rf"\b(?:spo2|sp02|oxygen(?: saturation)?|oksijen\w*|satur\w*)\D{{0,15}}?%?\s?{NUM}")),
    ("body_temperature", "degC", re.compile(rf"{NUM}\s?(?:°\s?c|degrees?(?: c(?:elsius)?)?|derece)\b")),
    ("sleep_duration", "h", re.compile(rf"\b(?:slept|sleep|uyudum|uyku\w*)\D{{0,12}}?{NUM}\s?(?:h|hrs?|hours?|saat)\b")),
    ("sleep_duration", "h", re.compile(rf"{NUM}\s?(?:h|hrs?|hours?|saat)\s(?:of sleep|sleep|uyudum|uyku\w*)")),
    ("water_intake", "L", re.compile(rf"{NUM}\s?(?:l|lt|liters?|litres?|litre)\b(?:\D{{0,15}}?(?:water|su\b|suyu))")),
    ("water_intake", "mL", re.compile(rf"{NUM}\s?ml\b(?:\D{{0,15}}?(?:water|su\b|suyu))")),
]
_BP = re.compile(r"\b(?:blood pressure|bp|tansiyon\w*)\D{0,15}?(\d{2,3})\s?/\s?(\d{2,3})")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _context_for(metric: str, folded_sentence: str) -> str | None:
    if metric in ("sleep_duration", "water_intake"):
        return "daily"
    if metric in ("body_temperature", "spo2", "systolic_bp", "diastolic_bp"):
        return "rest" if not _EXERCISE.search(folded_sentence) else None
    if _SLEEPING.search(folded_sentence):
        return "sleep"
    if _EXERCISE.search(folded_sentence):
        return "exercise"
    if _REST.search(folded_sentence):
        return "rest"
    return None  # heart rate without a stated activity: the astronaut chooses


def extract_values(text: str) -> list[ValueHit]:
    folded = _ascii(text).lower()  # same length as `text`, so positions map back to the original
    out: list[ValueHit] = []
    seen: set[tuple[str, float]] = set()

    def add(metric: str, value: float, unit: str, start: int, end: int) -> None:
        if (metric, value) in seen:
            return
        seen.add((metric, value))
        quote = sentence_at(text, start, end)
        out.append(ValueHit(metric, value, unit, _context_for(metric, _ascii(quote).lower()), quote))

    for m in _BP.finditer(folded):
        add("systolic_bp", _num(m.group(1)), "mmHg", m.start(), m.end())
        add("diastolic_bp", _num(m.group(2)), "mmHg", m.start(), m.end())
    for metric, unit, pat in _PATTERNS:
        for m in pat.finditer(folded):
            value = _num(m.group(1))
            if metric == "water_intake" and unit == "L":
                metric_value, metric_unit = value * 1000, "mL"
            else:
                metric_value, metric_unit = value, unit
            add(metric, metric_value, metric_unit, m.start(), m.end())
    return out
