"""Deterministic triage rules for the doctor's live feed (yellow / red).

These rules, not the language model, set the floor of every flag (master prompt Engineering
Rule 8). The levels reuse the DEMO escalation matrix (config/escalation.demo.yaml):
CRITICAL -> red, WARNING -> yellow. Like that matrix, they are demonstration rules pending
validation by mission medical operations.

Rule ids:
  R-SYM-LEVEL        a symptom report, levelled by the escalation matrix (severity, report-promptly list)
  R-TEXT-PROMPTLY    free text mentions a symptom on the DEMO report-promptly list
  R-TEXT-SYMPTOM     free text mentions another catalog symptom
  R-TEXT-SELFHARM    free text mentions self-harm, even in a negated sentence: a person reviews it
"""

from __future__ import annotations

from dataclasses import dataclass, field

from am.symptoms import catalog
from am.symptoms.detect import find_stems

SEVERITY_TO_LEVEL = {"CRITICAL": "red", "WARNING": "yellow"}
SELF_HARM = "self_harm_thoughts"


@dataclass
class RuleResult:
    level: str = "none"
    rule_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def raise_to(self, level: str, rule_id: str, reason: str) -> None:
        order = ("none", "yellow", "red")
        if order.index(level) > order.index(self.level):
            self.level = level
        if rule_id not in self.rule_ids:
            self.rule_ids.append(rule_id)
        self.reasons.append(reason)

    def merge(self, other: RuleResult) -> None:
        order = ("none", "yellow", "red")
        if order.index(other.level) > order.index(self.level):
            self.level = other.level
        self.rule_ids += [r for r in other.rule_ids if r not in self.rule_ids]
        self.reasons += other.reasons


def text_rules(text: str) -> RuleResult:
    """Rules for free text the astronaut wrote (chat message, check-in note)."""
    res = RuleResult()
    seen: set[tuple[str, bool]] = set()
    for hit in find_stems(text):
        key = (hit.code, hit.negated)
        if key in seen:
            continue
        seen.add(key)
        sym = catalog.get(hit.code)
        label = sym.label if sym else hit.code
        if hit.code == SELF_HARM:
            res.raise_to("red", "R-TEXT-SELFHARM", f"Mentions {label.lower()}"
                         + (" (in a negated sentence; a person should still review it)." if hit.negated else "."))
        elif hit.negated:
            continue
        elif sym is not None and sym.report_promptly:
            res.raise_to("red", "R-TEXT-PROMPTLY", f"Mentions {label.lower()}, which is on the DEMO report-promptly "
                                                   "list.")
        else:
            res.raise_to("yellow", "R-TEXT-SYMPTOM", f"Mentions {label.lower()}.")
    return res


def symptom_rules(symptom_code: str, severity: str) -> RuleResult:
    """Rules for a symptom report, using the DEMO escalation matrix."""
    from am.guidance.escalation import load_matrix

    res = RuleResult()
    matrix = load_matrix()
    sym = catalog.get(symptom_code)
    label = sym.label if sym else symptom_code
    levels = []
    rated = matrix.symptoms.by_severity.get(severity)
    if rated is not None:
        levels.append((rated.value, f"{label} rated {severity}."))
    if sym is not None and sym.report_promptly:
        levels.append((matrix.symptoms.report_promptly.value, f"{label} is on the DEMO report-promptly list."))
    if not levels:  # unrated report: still reviewed by a person
        levels.append(("WARNING", f"{label} reported without a rating."))
    for sev, reason in levels:
        res.raise_to(SEVERITY_TO_LEVEL.get(sev, "none"), "R-SYM-LEVEL", reason)
    return res
