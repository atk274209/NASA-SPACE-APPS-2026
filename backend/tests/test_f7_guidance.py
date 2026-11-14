"""F7: 'Is this serious?' guidance, escalation events and the body map.

No model is needed anywhere: guidance, escalation events and body-map status are fully
deterministic (levels from the anomaly engine and the astronaut's own symptom ratings).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from am.db.base import utcnow
from am.db.enums import Severity
from am.db.models import Alert, AuditLog
from am.db.models_m5 import EscalationEvent, SymptomReport
from am.guidance import escalation
from am.guidance.bodymap import SYSTEM_METRICS, SYSTEM_TOPICS
from am.guidance.escalation import (
    FALLBACK_PROCEDURE_ID,
    EscalationConfigError,
    alert_level,
    load_matrix,
    record_escalation,
)
from am.symptoms import catalog
from tests.conftest import NOW, feed_baseline, hr
from tests.test_companion import FakeOllama

START = NOW - timedelta(days=60)
API = "/api/v1"


# ---------------------------------------------------------------- helpers
def _sim(client, code: str, scenario: str = "acute_rest_hr") -> int:
    r = client.post(f"{API}/simulation/run", json={"scenario": scenario, "astronaut_code": code, "seed": 42})
    assert r.status_code == 201, r.text
    return r.json()["astronaut"]["id"]


def _astronaut(client, code: str) -> int:
    r = client.post(f"{API}/astronauts", json={"code": code, "display_name": f"Crew {code}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _symptom(client, astronaut_id: int, code: str, severity: str, status: str = "active") -> int:
    """Insert a symptom report directly: report creation belongs to feature F2."""
    with client.app.state.ctx.session_factory() as s:
        r = SymptomReport(astronaut_id=astronaut_id, symptom_code=code, severity=severity, source="manual",
                          status=status, resolved_at=utcnow() if status == "resolved" else None)
        s.add(r)
        s.commit()
        return r.id


def _alert(client, astronaut_id: int, metric: str, *, severity: str, peak: str, status: str = "active") -> int:
    """Insert an alert row directly, to pin down one body-map rule at a time."""
    now = utcnow()
    with client.app.state.ctx.session_factory() as s:
        a = Alert(astronaut_id=astronaut_id, metric_code=metric, context="rest", severity=severity,
                  peak_severity=peak, status=status, direction="high", rule_id="test_rule",
                  threshold_version="demo-0.1", threshold_label="DEMO — NOT CLINICALLY VALIDATED",
                  last_deviation=3.0, peak_deviation=5.0, persistence_count=3, explanation="test",
                  first_seen_at=now, last_seen_at=now)
        s.add(a)
        s.commit()
        return a.id


def _ingest(ingest, *args, **kw):
    r = ingest.ingest(hr(*args, **kw), now=NOW + timedelta(days=365))
    ingest.s.commit()
    return r


def _events(db, alert_id: int) -> list[EscalationEvent]:
    return list(db.scalars(select(EscalationEvent).where(EscalationEvent.alert_id == alert_id)
                           .order_by(EscalationEvent.id)))


def _quiet_gap(ingest) -> timedelta:
    """Move past the persistence window so earlier baseline noise cannot count."""
    return timedelta(hours=ingest.anomaly.thresholds.rule_for("heart_rate").persistence.max_age_hours + 1)


# ---------------------------------------------------------------- matrix
def test_matrix_is_demo_labelled_and_matches_the_spec_table(client):
    m = load_matrix()
    assert "DEMO" in m.label
    assert all(e.procedure_id.startswith("AM-DEMO-") for e in m.levels.values())
    expected = {
        Severity.WATCH: ("Keep an eye on it", "Yourself",
                         "Re-measure per protocol and mention it in your check-in"),
        Severity.WARNING: ("Tell the crew medical officer today", "Crew medical officer",
                           "Discuss at the next private medical conference"),
        Severity.CRITICAL: ("Contact now", "Crew medical officer or flight surgeon",
                            "Use the mission's medical contact procedure now"),
    }
    for level, (meaning, who, guidance) in expected.items():
        e = m.levels[level]
        assert (e.plain_meaning, e.who_to_involve, e.guidance) == (meaning, who, guidance)
    # nobody else is involved at WATCH, so only WARNING and CRITICAL raise escalation events
    assert [lvl for lvl, e in m.levels.items() if e.raises_event] == [Severity.WARNING, Severity.CRITICAL]
    assert m.symptoms.report_promptly == Severity.CRITICAL

    body = client.get(f"{API}/guidance/levels").json()
    assert [lv["level"] for lv in body["levels"]] == ["WATCH", "WARNING", "CRITICAL"]
    assert body["label"].startswith("DEMO") and "not a medical device" in body["disclaimer"]
    assert {s["code"] for s in body["report_promptly_symptoms"]} == {
        s.code for s in catalog.SYMPTOMS if s.report_promptly}
    assert body["symptom_rules"][0] == {"when": "On the DEMO report-promptly list, whatever the rating",
                                        "level": "CRITICAL"}


def test_matrix_validation_keeps_the_demo_label_and_all_levels(tmp_path):
    good = load_matrix()
    raw = good.model_dump(mode="json")
    no_label = {**raw, "label": "Validated procedure"}
    missing_level = {**raw, "levels": {k: v for k, v in raw["levels"].items() if k != "CRITICAL"}}
    import yaml

    for i, bad in enumerate((no_label, missing_level)):
        path = tmp_path / f"bad{i}.yaml"
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(EscalationConfigError):
            load_matrix(path)
    with pytest.raises(EscalationConfigError):
        load_matrix(tmp_path / "missing.yaml")


# ---------------------------------------------------------------- escalation events
def test_events_are_raised_once_per_alert_per_level_reached(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5) + _quiet_gap(ingest)
    r1 = _ingest(ingest, astronaut.id, 115, last + timedelta(minutes=10))  # opens at WATCH
    assert r1.assessment.severity == Severity.WATCH
    assert _events(db, r1.alert_id) == []  # WATCH involves only the astronaut: no event

    r2 = _ingest(ingest, astronaut.id, 116, last + timedelta(minutes=20))  # escalates to WARNING
    r3 = _ingest(ingest, astronaut.id, 114, last + timedelta(minutes=30))  # escalates to CRITICAL
    r4 = _ingest(ingest, astronaut.id, 117, last + timedelta(minutes=40))  # stays CRITICAL
    assert r1.alert_id == r2.alert_id == r3.alert_id == r4.alert_id
    events = _events(db, r1.alert_id)
    assert [(e.level, e.procedure_id, e.status) for e in events] == [
        ("WARNING", "AM-DEMO-ESC-02", "open"), ("CRITICAL", "AM-DEMO-ESC-03", "open")]
    raised = [e.actions[0] for e in events]
    assert [a["measurement_id"] for a in raised] == [r2.measurement.id, r3.measurement.id]
    assert raised[1]["kind"] == "raised" and raised[1]["plain_meaning"] == "Contact now"
    assert raised[1]["data_time"] == r3.measurement.recorded_at.isoformat()

    # idempotent: reaching a level again never duplicates its event
    alert = db.get(Alert, r1.alert_id)
    assert record_escalation(db, alert, Severity.CRITICAL, measurement_id=None, data_time=None) is None
    audits = db.scalars(select(AuditLog).where(AuditLog.action == "escalation.raised")).all()
    assert sorted(int(a.entity_id) for a in audits) == [e.id for e in events]


def test_alert_opening_directly_at_warning_raises_an_event(ingest, astronaut, db):
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5) + _quiet_gap(ingest)
    p = ingest.baselines.active_profile(astronaut.id, "heart_rate", "rest")
    scale = ingest.baselines.scale_of(p)
    first = _ingest(ingest, astronaut.id, round(p.median + 2.5 * scale, 1), last + timedelta(minutes=10))
    assert first.alert_id is None  # a single watch-band reading is not persistent
    second = _ingest(ingest, astronaut.id, round(p.median + 3.6 * scale, 1), last + timedelta(minutes=20))
    assert second.assessment.severity == Severity.WARNING and second.alert_id is not None
    assert [e.level for e in _events(db, second.alert_id)] == ["WARNING"]


def test_broken_matrix_never_blocks_ingest_or_hides_an_escalation(ingest, astronaut, db, client, monkeypatch,
                                                                   tmp_path):
    monkeypatch.setattr(escalation, "ESCALATION_CONFIG_PATH", tmp_path / "missing.yaml")
    last = feed_baseline(ingest, astronaut.id, start=START, n=36, every_min=5) + _quiet_gap(ingest)
    rs = [_ingest(ingest, astronaut.id, v, last + timedelta(minutes=10 * (i + 1)))
          for i, v in enumerate((115, 116, 114))]
    events = _events(db, rs[0].alert_id)
    assert [(e.level, e.procedure_id) for e in events] == [("WARNING", FALLBACK_PROCEDURE_ID),
                                                           ("CRITICAL", FALLBACK_PROCEDURE_ID)]
    assert client.get(f"{API}/guidance/levels").status_code == 503
    listed = client.get(f"{API}/escalations", params={"astronaut_id": astronaut.id}).json()
    assert listed[0]["level"] == "CRITICAL" and "medical contact procedure" in listed[0]["plain_meaning"]


def test_escalation_list_and_acknowledge_flow(client):
    aid = _sim(client, "F7-ACK")
    events = client.get(f"{API}/escalations", params={"astronaut_id": aid}).json()
    assert events, "the acute scenario reaches CRITICAL, which must raise an escalation event"
    crit = next(e for e in events if e["level"] == "CRITICAL")
    assert crit["status"] == "open" and crit["label"].startswith("DEMO")
    assert crit["plain_meaning"] == "Contact now"
    assert crit["who_to_involve"] == "Crew medical officer or flight surgeon"
    assert crit["alert"]["metric_code"] == "heart_rate" and crit["alert"]["is_simulated"] is True
    assert crit["data_time"] is not None
    assert all(e["level"] in ("WARNING", "CRITICAL") for e in events)

    url = f"{API}/escalations/{crit['id']}/acknowledge"
    assert client.post(url, json={"actor": "   ", "note": ""}).status_code == 422
    ack = client.post(url, json={"actor": "flight-surgeon", "note": "Called the crew medical officer."})
    assert ack.status_code == 200, ack.text
    body = ack.json()
    assert body["status"] == "acknowledged" and body["acknowledged_by"] == "flight-surgeon"
    assert body["acknowledgement_note"] == "Called the crew medical officer."
    assert body["acknowledged_at"] is not None
    assert client.post(url, json={"actor": "someone"}).status_code == 409
    assert client.post(f"{API}/escalations/999999/acknowledge", json={"actor": "x"}).status_code == 404

    still_open = client.get(f"{API}/escalations", params={"astronaut_id": aid, "status": "open"}).json()
    assert crit["id"] not in [e["id"] for e in still_open]
    assert client.get(f"{API}/escalations", params={"astronaut_id": aid, "status": "nope"}).status_code == 422
    assert client.get(f"{API}/escalations").status_code == 422  # astronaut_id is required
    audit = client.get(f"{API}/audit", params={"action_prefix": "escalation.acknowledged"}).json()
    assert audit[0]["actor"] == "flight-surgeon" and audit[0]["entity_id"] == str(crit["id"])


# ---------------------------------------------------------------- guidance
def test_alert_level_counts_at_peak_until_acknowledged():
    a = Alert(severity="WATCH", peak_severity="CRITICAL", status="active")
    assert alert_level(a) == Severity.CRITICAL
    a.status = "acknowledged"
    assert alert_level(a) == Severity.WATCH
    a.status = "resolved"
    assert alert_level(a) == Severity.CRITICAL  # reported for reference only


def test_guidance_for_alert_follows_review_state(client):
    aid = _sim(client, "F7-GA")
    alerts = client.get(f"{API}/alerts", params={"astronaut_id": aid, "open_only": True}).json()
    hr_alert = next(a for a in alerts if a["metric_code"] == "heart_rate" and a["context"] == "rest"
                    and a["direction"] == "high")
    g = client.get(f"{API}/guidance/alerts/{hr_alert['id']}").json()
    assert g["level"] == "CRITICAL" and g["plain_meaning"] == "Contact now"
    assert g["applies_now"] is True and g["label"].startswith("DEMO")
    assert g["subject"] == {"type": "alert", "id": hr_alert["id"], "astronaut_id": aid,
                            "title": "Heart rate (rest), high deviation", "status": "active", "is_simulated": True}
    assert any("not clinical cut-offs" in r for r in g["reasons"])
    assert "CRITICAL" in [e["level"] for e in g["escalations"]]

    client.post(f"{API}/alerts/{hr_alert['id']}/acknowledge", json={"actor": "fs", "note": "seen"})
    acked = client.get(f"{API}/guidance/alerts/{hr_alert['id']}").json()
    assert acked["level"] == hr_alert["severity"]  # acknowledged: current severity
    assert acked["reasons"][0].startswith("Acknowledged")

    client.post(f"{API}/alerts/{hr_alert['id']}/resolve", json={"actor": "fs", "note": "simulated episode reviewed"})
    resolved = client.get(f"{API}/guidance/alerts/{hr_alert['id']}").json()
    assert resolved["applies_now"] is False and resolved["reasons"][0].startswith("Resolved")
    assert client.get(f"{API}/guidance/alerts/999999").status_code == 404


def test_symptom_guidance_maps_report_promptly_to_critical(client):
    aid = _astronaut(client, "F7-SYM")
    chest = _symptom(client, aid, "chest_pain", "mild")
    moderate = _symptom(client, aid, "headache", "moderate")
    severe = _symptom(client, aid, "headache", "severe")
    old = _symptom(client, aid, "nausea", "mild", status="resolved")

    g = client.get(f"{API}/guidance/symptoms/{chest}").json()
    assert g["level"] == "CRITICAL" and g["who_to_involve"] == "Crew medical officer or flight surgeon"
    assert any("report-promptly" in r for r in g["reasons"])
    assert g["subject"]["title"] == "Chest pain or tightness, rated mild" and g["escalations"] == []
    assert client.get(f"{API}/guidance/symptoms/{moderate}").json()["level"] == "WARNING"
    assert client.get(f"{API}/guidance/symptoms/{severe}").json()["level"] == "CRITICAL"
    resolved = client.get(f"{API}/guidance/symptoms/{old}").json()
    assert resolved["applies_now"] is False and resolved["level"] == "WARNING"
    assert client.get(f"{API}/guidance/symptoms/999999").status_code == 404


def test_current_guidance_takes_the_most_serious_open_item(client):
    empty = _astronaut(client, "F7-CUR")
    g = client.get(f"{API}/guidance/current", params={"astronaut_id": empty}).json()
    assert g["level"] is None and g["guidance"] is None and g["items"] == []
    assert "recorded data only" in g["note"]

    _symptom(client, empty, "headache", "mild")
    _symptom(client, empty, "shortness_of_breath", "mild")
    g = client.get(f"{API}/guidance/current", params={"astronaut_id": empty}).json()
    assert g["level"] == "CRITICAL" and g["guidance"]["plain_meaning"] == "Contact now"
    assert [i["level"] for i in g["items"]] == ["CRITICAL", "WARNING"]
    assert g["items"][0]["type"] == "symptom_report" and "report-promptly" in g["items"][0]["reason"]

    sim = _sim(client, "F7-CUR2")
    g = client.get(f"{API}/guidance/current", params={"astronaut_id": sim}).json()
    assert g["level"] == "CRITICAL" and g["items"][0]["type"] == "alert" and g["open_escalations"] >= 1
    assert client.get(f"{API}/guidance/current", params={"astronaut_id": 999999}).status_code == 404


# ---------------------------------------------------------------- body map
def _systems(client, astronaut_id: int) -> dict[str, dict]:
    r = client.get(f"{API}/bodymap", params={"astronaut_id": astronaut_id})
    assert r.status_code == 200, r.text
    return {s["code"]: s for s in r.json()["systems"]}


def test_body_map_without_data_shows_no_data_everywhere(client):
    aid = _astronaut(client, "F7-EMPTY")
    body = client.get(f"{API}/bodymap", params={"astronaut_id": aid}).json()
    assert [s["code"] for s in body["systems"]] == list(catalog.BODY_SYSTEMS)
    assert {s["status"] for s in body["systems"]} == {"no_data"}
    assert all(s["reasons"][0]["text"] == "Nothing recorded for this system yet." for s in body["systems"])
    assert [r["status"] for r in body["rules"]] == ["red", "yellow", "watch", "green", "no_data"]
    assert "report-promptly" in body["rules"][0]["meaning"] and body["label"].startswith("DEMO")
    assert "not that it is healthy" in body["rules_note"]
    eyes = next(s for s in body["systems"] if s["code"] == "eyes")
    assert eyes["coverage"].startswith("Symptom reports only")
    assert client.get(f"{API}/bodymap", params={"astronaut_id": 999999}).status_code == 404


def test_body_map_status_rules(client):
    aid = _astronaut(client, "F7-MAP")
    _alert(client, aid, "heart_rate", severity="WARNING", peak="CRITICAL")  # unacknowledged: counts at peak
    _alert(client, aid, "spo2", severity="WATCH", peak="WATCH")
    _alert(client, aid, "body_temperature", severity="WARNING", peak="CRITICAL", status="acknowledged")
    resolved = _alert(client, aid, "step_count", severity="CRITICAL", peak="CRITICAL", status="resolved")
    _symptom(client, aid, "vision_change", "mild")  # report-promptly list
    nausea = _symptom(client, aid, "nausea", "moderate")
    _symptom(client, aid, "back_pain", "mild", status="resolved")
    now = utcnow().isoformat()
    r = client.post(f"{API}/measurements", json={
        "astronaut_id": aid, "metric_code": "water_intake", "value": 2100, "unit": "mL", "context": "daily",
        "recorded_at": now, "source_name": "galley-log", "source_kind": "manual"})
    assert r.status_code == 201, r.text

    s = _systems(client, aid)
    assert s["cardiovascular"]["status"] == "red" and s["cardiovascular"]["level"] == "CRITICAL"
    assert s["respiratory"]["status"] == "watch"
    assert s["immune_skin"]["status"] == "yellow"  # acknowledged: counts at its current severity
    assert s["eyes"]["status"] == "red"
    assert s["eyes"]["active_symptoms"][0]["report_promptly"] is True
    assert s["gastrointestinal"]["status"] == "yellow"
    assert s["gastrointestinal"]["reasons"][0] == {
        "text": "Active symptom: Nausea, rated moderate.", "level": "WARNING", "ref_type": "symptom_report",
        "ref_id": nausea}
    assert s["metabolic"]["status"] == "green"
    assert s["metabolic"]["metrics"][0]["code"] == "water_intake"
    # a resolved alert and a resolved symptom are records, not open items
    assert s["musculoskeletal"]["status"] == "green" and s["musculoskeletal"]["open_alerts"] == []
    assert resolved not in [a["id"] for a in s["musculoskeletal"]["open_alerts"]]
    assert s["neuro_behavioral"]["status"] == "no_data"

    assert client.post(f"{API}/checkins", json={"astronaut_id": aid, "ratings": {"mood": 4, "stress": 2}}
                       ).status_code in (200, 201)
    neuro = _systems(client, aid)["neuro_behavioral"]
    assert neuro["status"] == "green"
    assert [m["code"] for m in neuro["metrics"]] == ["self_report:mood", "self_report:stress"]
    assert neuro["metrics"][0]["source"] == "self_report" and neuro["metrics"][0]["severity"] is None


def test_body_map_from_simulation_and_related_reading(client):
    client.app.state.ctx.llm = FakeOllama([]).client()  # offline: lexical knowledge base only
    assert client.post(f"{API}/knowledge/ingest").status_code == 200
    aid = _sim(client, "F7-SIMMAP")
    body = client.get(f"{API}/bodymap", params={"astronaut_id": aid}).json()
    assert body["simulated"] is True and body["knowledge_indexed"] is True
    s = {x["code"]: x for x in body["systems"]}
    cv = s["cardiovascular"]
    assert cv["status"] == "red"
    assert any(a["metric_code"] == "heart_rate" and a["level"] == "CRITICAL" for a in cv["open_alerts"])
    rest = next(m for m in cv["metrics"] if m["code"] == "heart_rate" and m["context"] == "rest")
    assert rest["is_simulated"] is True and rest["source"] == "measurement"
    assert cv["reasons"][0]["ref_type"] == "alert" and cv["reasons"][0]["level"] == "CRITICAL"
    # systems without any record stay no_data instead of being inferred
    assert s["eyes"]["status"] == "no_data" and s["gastrointestinal"]["status"] == "no_data"
    assert s["musculoskeletal"]["status"] in ("green", "watch") and s["musculoskeletal"]["metrics"]
    # every metric shown belongs to its system's mapping
    for code, system in s.items():
        allowed = set(SYSTEM_METRICS[code])
        assert all(m["code"] in allowed or m["code"].startswith("self_report:") for m in system["metrics"])
        assert all(set(d["matched_topics"]) <= set(SYSTEM_TOPICS[code]) for d in system["documents"])
    assert any("bone" in d["slug"] for d in s["musculoskeletal"]["documents"])
    assert any(d["slug"] == "hrr-risk-sans" for d in s["eyes"]["documents"])
    assert s["respiratory"]["documents"] == []  # no curated source covers breathing yet
