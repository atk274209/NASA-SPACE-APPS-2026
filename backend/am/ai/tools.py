"""Read-only tools that give the language model access to stored data.

The model never sees the database directly and has no write tools. Every tool returns
(data, refs): `data` is compact JSON for the prompt, `refs` lists the stored records the
data came from so the final answer can be traced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.ai.ollama import OllamaClient
from am.baseline.service import BaselineService
from am.db.base import utcnow
from am.db.enums import AlertStatus, DataQuality
from am.db.models import Alert, Astronaut, Measurement, MetricDefinition, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.policy import BaselinePolicy, ThresholdConfig
from am.rag.knowledge import search as knowledge_search
from am.symptoms import catalog as symptom_catalog

Ref = dict[str, Any]
ToolResult = tuple[dict[str, Any], list[Ref]]

KNOWLEDGE_HITS = 4
KNOWLEDGE_TEXT_CHARS = 700  # keeps the excerpts within the model's context window


@dataclass
class ToolEnv:
    session: Session
    astronaut_id: int
    thresholds: ThresholdConfig
    policy: BaselinePolicy
    # Knowledge search uses embeddings when both are set; otherwise its offline keyword fallback.
    llm: OllamaClient | None = None
    embed_model: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolEnv, dict[str, Any]], ToolResult]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _r(v: float | None, d: int = 1) -> float | None:
    return None if v is None else round(float(v), d)


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def compare_to_baseline(value: float, median: float | None, z: float | None, severity: str | None) -> str | None:
    """Pre-computed comparison so the language model never has to do arithmetic."""
    if median is None:
        return None
    diff = value - median
    if abs(diff) < 1e-9:
        where = "equal to"
    else:
        where = "above" if diff > 0 else "below"
    pct = f", {abs(diff) / abs(median) * 100:.0f}%" if median else ""
    zs = f", robust z {z:+.2f}" if z is not None else ""
    sev = f", classified {severity}" if severity else ", not classified (no alert rule for this indicator)"
    return f"{abs(diff):.2f} {where} personal median {median:.2f}{pct}{zs}{sev}"


def alert_summary(a: Alert) -> str:
    """Deterministic plain-language reading of an alert's state."""
    review = {"active": "not yet reviewed by the crew/medical team",
              "acknowledged": "acknowledged, still open",
              "resolved": "resolved"}.get(a.status, a.status)
    return (f"Highest level reached: {a.peak_severity}. Level of the most recent deviating reading: {a.severity}. "
            f"Review status: {review}.")


def _metric_ok(env: ToolEnv, code: str) -> MetricDefinition | None:
    return env.session.get(MetricDefinition, code)


# ------------------------------------------------------------------ handlers
def health_overview(env: ToolEnv, _args: dict[str, Any]) -> ToolResult:
    s = env.session
    a = s.get(Astronaut, env.astronaut_id)
    mission = s.scalars(select(Mission).where(Mission.astronaut_id == env.astronaut_id)
                        .order_by(Mission.id.desc()).limit(1)).first()
    alerts = s.scalars(select(Alert).where(Alert.astronaut_id == env.astronaut_id,
                                           Alert.status != AlertStatus.RESOLVED.value)).all()
    latest_ids = select(func.max(Measurement.id)).where(
        Measurement.astronaut_id == env.astronaut_id, Measurement.quality != DataQuality.REJECTED.value
    ).group_by(Measurement.metric_code, Measurement.context)
    rows = s.scalars(select(Measurement).where(Measurement.id.in_(latest_ids))).all()
    bsvc = BaselineService(s, env.policy, env.thresholds)
    readings = []
    for m in rows:
        prof = bsvc.active_profile(env.astronaut_id, m.metric_code, m.context)
        readings.append({
            "metric": m.metric_code, "context": m.context, "value": _r(m.value, 2), "unit": m.unit,
            "recorded_at": _ts(m.recorded_at), "robust_z": _r(m.deviation_score, 2), "severity": m.severity,
            "baseline_median": _r(prof.median, 2) if prof else None,
            "baseline_status": (prof.status + (", frozen" if prof.is_frozen else "")) if prof else "none",
            "comparison": compare_to_baseline(m.value, prof.median if prof else None, m.deviation_score,
                                              m.severity),
        })
    data = {
        "astronaut": a.display_name if a else None,
        "simulated_data": bool(a and a.is_simulated),
        "mission": mission.name if mission else None,
        "mission_day": (utcnow().date() - mission.start_date).days + 1 if mission else None,
        "threshold_label": env.thresholds.label,
        "open_alerts": [{"alert_id": x.id, "metric": x.metric_code, "context": x.context, "direction": x.direction,
                         "current_severity": x.severity, "peak_severity": x.peak_severity, "status": x.status,
                         "last_robust_z": _r(x.last_deviation, 2), "summary": alert_summary(x)} for x in alerts],
        "latest_readings": readings,
    }
    refs = [{"type": "alert", "id": x.id} for x in alerts] + [{"type": "measurement", "id": m.id} for m in rows]
    return data, refs


CONTEXTS = ("rest", "exercise", "sleep", "other", "daily")


def _stream_args(env: ToolEnv, args: dict[str, Any]) -> tuple[str, str] | dict[str, Any]:
    code, context = str(args.get("metric_code", "")), str(args.get("context", ""))
    if not _metric_ok(env, code):
        return {"error": f"metric_code is missing or unknown ('{code}'). Call again with metric_code (e.g. "
                         "'heart_rate') and context (e.g. 'sleep' for night-time, 'rest' for daytime resting)."}
    if context not in CONTEXTS:
        return {"error": f"context is required, one of {list(CONTEXTS)}. Night-time heart rate is 'sleep'; "
                         "daily totals (sleep_duration, exercise_duration, water_intake) are 'daily'."}
    return code, context


def recent_measurements(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    parsed = _stream_args(env, args)
    if isinstance(parsed, dict):
        return parsed, []
    code, context = parsed
    base = select(Measurement).where(
        Measurement.astronaut_id == env.astronaut_id, Measurement.metric_code == code,
        Measurement.context == context, Measurement.quality != DataQuality.REJECTED.value)
    last = env.session.scalars(base.order_by(Measurement.recorded_at.desc()).limit(1)).first()
    if last is None:
        return {"metric": code, "context": context, "n": 0, "note": "no readings stored"}, []

    mission_day = args.get("mission_day")
    if mission_day not in (None, ""):
        mission = env.session.scalars(select(Mission).where(Mission.astronaut_id == env.astronaut_id)
                                      .order_by(Mission.id.desc()).limit(1)).first()
        if mission is None:
            return {"error": "no mission record, so mission days cannot be resolved"}, []
        try:
            day = int(mission_day)
        except (TypeError, ValueError):
            return {"error": "mission_day must be an integer"}, []
        if day < 1:
            return {"error": "mission_day counts from 1 (first mission day). For the most recent N days, "
                             "use the 'days' parameter instead."}, []
        start = datetime.combine(mission.start_date, datetime.min.time()) + timedelta(days=day - 1)
        end = start + timedelta(days=1)
        window = f"mission day {day} only ({_ts(start)} to {_ts(end)})"
        rows = env.session.scalars(base.where(Measurement.recorded_at >= start, Measurement.recorded_at < end)
                                   .order_by(Measurement.recorded_at)).all()
        listing = rows[-48:]
    else:
        if args.get("days") not in (None, ""):
            hours = float(args["days"]) * 24
        else:
            hours = float(args.get("hours", 24) or 24)
        hours = max(1.0, min(hours, 24 * 60))
        since = last.recorded_at - timedelta(hours=hours)
        window = f"{hours:g} h ending at the most recent reading ({_ts(last.recorded_at)})"
        rows = env.session.scalars(base.where(Measurement.recorded_at >= since)
                                   .order_by(Measurement.recorded_at)).all()
        listing = rows[-10:]
    if not rows:
        return {"metric": code, "context": context, "window": window, "n": 0,
                "note": "no readings in this window"}, []
    vals = sorted(r.value for r in rows)
    sev_counts: dict[str, int] = {}
    for r in rows:
        sev_counts[r.severity or "unassessed"] = sev_counts.get(r.severity or "unassessed", 0) + 1
    data = {
        "metric": code, "context": context, "unit": last.unit,
        "window": window,
        "coverage_note": (f"Retrieved readings span {_ts(rows[0].recorded_at)} to {_ts(rows[-1].recorded_at)}. "
                          "Only the times listed in 'readings' were retrieved; there is NO value for any other "
                          "time. If asked about a time not listed, say it was not retrieved."),
        "n": len(rows), "min": _r(vals[0], 2), "max": _r(vals[-1], 2), "median": _r(vals[len(vals) // 2], 2),
        "first": {"value": _r(rows[0].value, 2), "at": _ts(rows[0].recorded_at)},
        "last": {"value": _r(rows[-1].value, 2), "at": _ts(rows[-1].recorded_at),
                 "robust_z": _r(rows[-1].deviation_score, 2), "severity": rows[-1].severity},
        "change_first_to_last": _r(rows[-1].value - rows[0].value, 2),
        "severity_counts": sev_counts,
        "simulated_data": any(r.is_simulated for r in rows),
        "readings": [{"at": _ts(r.recorded_at), "value": _r(r.value, 2), "robust_z": _r(r.deviation_score, 2),
                      "severity": r.severity} for r in listing],
    }
    refs = [{"type": "measurement", "id": r.id} for r in listing]

    # Deterministic point lookup: the model must not pick a time out of a long list itself.
    tod = args.get("time_of_day")
    if tod not in (None, "") and mission_day not in (None, ""):
        try:
            hh, mm = (int(x) for x in str(tod).strip().split(":")[:2])
            target = start + timedelta(hours=hh, minutes=mm)
        except (ValueError, TypeError):
            data["nearest_to_requested_time"] = {"error": "time_of_day must be HH:MM (24 h, UTC)"}
        else:
            ranked = sorted(rows, key=lambda r: abs((r.recorded_at - target).total_seconds()))[:2]
            data["nearest_to_requested_time"] = {
                "requested": _ts(target),
                "instruction": "Answer with these readings and their exact times; say how far they are from "
                               "the requested time.",
                "readings": [{"at": _ts(r.recorded_at), "value": _r(r.value, 2), "unit": r.unit,
                              "minutes_from_requested": round((r.recorded_at - target).total_seconds() / 60),
                              "severity": r.severity} for r in sorted(ranked, key=lambda r: r.recorded_at)],
            }
            data.pop("readings")  # avoid offering a long list to choose from
            refs = [{"type": "measurement", "id": r.id} for r in ranked]
    elif tod not in (None, ""):
        data["nearest_to_requested_time"] = {"error": "time_of_day needs mission_day as well"}
    return data, refs


def baseline_info(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    parsed = _stream_args(env, args)
    if isinstance(parsed, dict):
        return parsed, []
    code, context = parsed
    bsvc = BaselineService(env.session, env.policy, env.thresholds)
    p = bsvc.active_profile(env.astronaut_id, code, context)
    if p is None:
        return {"metric": code, "context": context, "baseline": None,
                "note": "not enough good-quality readings in this context yet"}, []
    anchor = bsvc.anchor_profile(env.astronaut_id, code, context)
    data = {
        "metric": code, "context": context, "version": p.version, "status": p.status,
        "frozen": p.is_frozen, "frozen_reason": p.frozen_reason,
        "median": _r(p.median, 2), "deviation_scale": _r(bsvc.scale_of(p), 3),
        "p05": _r(p.p05, 2), "p95": _r(p.p95, 2), "samples": p.n,
        "window": f"{_ts(p.window_start)} to {_ts(p.window_end)}",
        "anchor_median": _r(anchor.median, 2) if anchor else None,
        "note": "Personal statistical reference, not a clinical normal range.",
    }
    return data, [{"type": "baseline", "id": p.id}]


def alerts_list(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    stmt = select(Alert).where(Alert.astronaut_id == env.astronaut_id)
    if args.get("open_only", True):
        stmt = stmt.where(Alert.status != AlertStatus.RESOLVED.value)
    rows = env.session.scalars(stmt.order_by(Alert.last_seen_at.desc()).limit(15)).all()
    data = {"threshold_label": env.thresholds.label, "alerts": [{
        "alert_id": a.id, "metric": a.metric_code, "context": a.context, "direction": a.direction,
        "current_severity": a.severity, "peak_severity": a.peak_severity, "status": a.status,
        "first_seen": _ts(a.first_seen_at), "last_seen": _ts(a.last_seen_at),
        "peak_robust_z": _r(a.peak_deviation, 2), "summary": alert_summary(a)} for a in rows]}
    return data, [{"type": "alert", "id": a.id} for a in rows]


def alert_detail(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    try:
        aid = int(args.get("alert_id"))
    except (TypeError, ValueError):
        return {"error": "alert_id must be an integer"}, []
    a = env.session.get(Alert, aid)
    if a is None or a.astronaut_id != env.astronaut_id:
        return {"error": f"alert {aid} not found for this astronaut"}, []
    ev = a.evidence[-10:]
    data = {
        "alert_id": a.id, "metric": a.metric_code, "context": a.context, "direction": a.direction,
        "current_severity": a.severity, "peak_severity": a.peak_severity, "status": a.status,
        "rule": a.rule_id, "threshold_version": a.threshold_version, "threshold_label": a.threshold_label,
        "summary": alert_summary(a), "explanation": a.explanation, "persistence_count": a.persistence_count,
        "evidence_last_10": [{"measurement_id": e.measurement_id, "value": _r(e.measurement.value, 2),
                              "unit": e.measurement.unit, "at": _ts(e.measurement.recorded_at),
                              "robust_z": _r(e.deviation, 2), "severity": e.severity_at_time} for e in ev],
        "reviews": [{"action": k.action, "by": k.actor, "note": k.note} for k in a.acks],
    }
    return data, [{"type": "alert", "id": a.id}] + [{"type": "measurement", "id": e.measurement_id} for e in ev]


def self_reports(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    days = max(1, min(int(args.get("days", 7) or 7), 90))
    since = utcnow() - timedelta(days=days)
    rows = env.session.scalars(select(SelfReport).where(
        SelfReport.astronaut_id == env.astronaut_id, SelfReport.recorded_at >= since)
        .order_by(SelfReport.recorded_at)).all()
    data = {"days": days, "scale": "1 to 5, self-reported, not a clinical instrument",
            "reports": [{"at": _ts(r.recorded_at), "kind": r.kind, "score": r.score, "note": r.note or None}
                        for r in rows[-40:]]}
    return data, [{"type": "self_report", "id": r.id} for r in rows[-40:]]


def symptom_reports(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    status = str(args.get("status") or "active")
    if status not in ("active", "all"):
        return {"error": "status must be 'active' (default) or 'all'"}, []
    stmt = select(SymptomReport).where(SymptomReport.astronaut_id == env.astronaut_id)
    if status == "active":
        stmt = stmt.where(SymptomReport.status == "active")
    rows = env.session.scalars(stmt.order_by(SymptomReport.reported_at.desc()).limit(30)).all()
    items = []
    for r in rows:
        sym = symptom_catalog.get(r.symptom_code)
        items.append({
            "report_id": r.id, "symptom": sym.label if sym else r.symptom_code, "code": r.symptom_code,
            "self_rated_severity": r.severity, "status": r.status, "reported_at": _ts(r.reported_at),
            "onset_at": _ts(r.onset_at) if r.onset_at else None,
            "resolved_at": _ts(r.resolved_at) if r.resolved_at else None,
            "source": r.source, "note": r.note or None,
            "on_demo_report_promptly_list": bool(sym and sym.report_promptly),
        })
    data = {"status_filter": status,
            "note": "Symptoms the astronaut reported, with their own severity rating. They are not diagnoses.",
            "report_promptly_list": symptom_catalog.REPORT_PROMPTLY_LABEL,
            "symptoms": items}
    return data, [{"type": "symptom_report", "id": r.id} for r in rows]


def search_knowledge(env: ToolEnv, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required: a few keywords, for example 'bone loss resistive exercise'"}, []
    use_embeddings = env.llm is not None and bool(env.embed_model)
    hits = knowledge_search(env.session, query[:300], llm=env.llm if use_embeddings else None,
                            embed_model=env.embed_model or "", k=KNOWLEDGE_HITS)
    results, refs = [], []
    for h in hits:
        doc = h.chunk.document
        url = h.chunk.source_url or doc.url
        results.append({"chunk_id": h.chunk.id, "title": doc.title, "organization": doc.organization,
                        "published_date": doc.published_date, "url": url, "kind": h.chunk.kind,
                        "text": h.chunk.text[:KNOWLEDGE_TEXT_CHARS]})
        refs.append({"type": "knowledge", "id": h.chunk.id, "doc_slug": doc.slug, "title": doc.title, "url": url})
    data = {"query": query,
            "note": ("Excerpts from AM's curated public sources about spaceflight health in general, not about "
                     "this astronaut. kind 'passage' is a verbatim quote; 'finding' and 'limitation' are AM's own "
                     "summaries. Name the source title when you use one."),
            "results": results}
    if not results:
        data["note"] = "No matching excerpts in the knowledge base (it may not be loaded yet)."
    return data, refs


_STREAM = {
    "type": "object",
    "properties": {
        "metric_code": {"type": "string", "description": "one of: heart_rate, hrv_rmssd, spo2, body_temperature, "
                                                         "respiratory_rate, systolic_bp, diastolic_bp (contexts rest/"
                                                         "exercise/sleep); sleep_duration, exercise_duration, "
                                                         "water_intake, energy_intake, step_count (context daily)"},
        "context": {"type": "string", "enum": ["rest", "exercise", "sleep", "other", "daily"]},
    },
    "required": ["metric_code", "context"],
}

TOOLS: list[Tool] = [
    Tool("get_health_overview", "Current status: mission day, open alerts and the latest reading of every "
         "indicator with its deviation from the personal baseline.", {"type": "object", "properties": {}},
         health_overview),
    Tool("get_recent_measurements",
         "Measured values of one indicator in one context: summary statistics plus individual timed readings. "
         "Use mission_day to look at one specific mission day; otherwise it covers the last `hours` "
         "(default 24) ending at the latest reading. Daily totals such as sleep_duration, exercise_duration "
         "and water_intake use context 'daily'.",
         {**_STREAM, "properties": {**_STREAM["properties"],
                                    "hours": {"type": "number", "description": "window length in hours (default 24)"},
                                    "days": {"type": "number", "description": "most recent N days (alternative to hours)"},
                                    "mission_day": {"type": "integer", "description": "a specific mission day (1 = first)"},
                                    "time_of_day": {"type": "string", "description": "HH:MM (24 h, UTC) with mission_day: "
                                                    "returns the readings nearest to that time"}}},
         recent_measurements),
    Tool("get_baseline", "The astronaut's personal baseline for one indicator and context.", _STREAM, baseline_info),
    Tool("get_alerts", "Alerts for this astronaut.", {"type": "object", "properties": {
        "open_only": {"type": "boolean", "description": "only unresolved alerts (default true)"}}}, alerts_list),
    Tool("get_alert_detail", "Full explanation and supporting measurements of one alert.", {
        "type": "object", "properties": {"alert_id": {"type": "integer"}}, "required": ["alert_id"]}, alert_detail),
    Tool("get_self_reports", "The astronaut's own subjective check-in ratings (mood, stress, fatigue, sleep "
         "quality, motivation). Measured sleep duration and exercise duration are NOT here; use "
         "get_recent_measurements with context 'daily' for those.", {"type": "object", "properties": {
             "days": {"type": "integer", "description": "how many days back (default 7)"}}}, self_reports),
    Tool("get_symptom_reports", "Symptoms the astronaut reported themselves, with their own severity rating "
         "(mild, moderate, severe). These are not diagnoses. Active symptoms only unless status is 'all'.",
         {"type": "object", "properties": {"status": {"type": "string", "enum": ["active", "all"]}}},
         symptom_reports),
    Tool("search_knowledge", "Search AM's curated knowledge base of public spaceflight-health sources (mostly NASA "
         "Human Research Program pages). Returns short excerpts with source titles. Use it for general background "
         "questions; it says nothing about this astronaut's own data.",
         {"type": "object", "properties": {"query": {"type": "string", "description": "a few keywords"}},
          "required": ["query"]}, search_knowledge),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(env: ToolEnv, name: str, args: dict[str, Any]) -> ToolResult:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"error": f"unknown tool '{name}'. Available: {sorted(TOOLS_BY_NAME)}"}, []
    try:
        return tool.handler(env, args if isinstance(args, dict) else {})
    except Exception as exc:  # a tool failure must not crash the conversation
        return {"error": f"tool failed: {type(exc).__name__}"}, []
