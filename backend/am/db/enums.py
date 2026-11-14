"""Shared enumerations. Stored as plain strings for migration friendliness."""

from enum import StrEnum


class MeasurementContext(StrEnum):
    REST = "rest"
    EXERCISE = "exercise"
    SLEEP = "sleep"
    OTHER = "other"
    DAILY = "daily"  # daily aggregates (sleep duration, intake totals...)


class SourceKind(StrEnum):
    SENSOR = "sensor"
    MANUAL = "manual"
    LAB = "lab"
    SIMULATED = "simulated"


class DataQuality(StrEnum):
    GOOD = "good"
    DEGRADED = "degraded"
    REJECTED = "rejected"


class Severity(StrEnum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @classmethod
    def from_rank(cls, rank: int) -> "Severity":
        rank = max(0, min(3, rank))
        return _RANK_SEVERITY[rank]


_SEVERITY_RANK = {Severity.NORMAL: 0, Severity.WATCH: 1, Severity.WARNING: 2, Severity.CRITICAL: 3}
_RANK_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}


class AssessmentStatus(StrEnum):
    ASSESSED = "assessed"
    NO_BASELINE = "no_baseline"
    SKIPPED_REJECTED = "skipped_rejected"


class BaselineStatus(StrEnum):
    PROVISIONAL = "provisional"
    ESTABLISHED = "established"
    SUPERSEDED = "superseded"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
