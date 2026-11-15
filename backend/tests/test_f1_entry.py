"""F1: unit conversion, manual entry, blood glucose, radiation log, crew and missions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from am.db.enums import AssessmentStatus, DataQuality, Severity, SourceKind
from am.db.models import AuditLog, BaselineProfile, Measurement
from am.health.ingest import MeasurementInput
from am.health.metrics import get_spec
from am.health.units import (
    GLUCOSE_MOLAR_MASS_G_PER_MOL,
    MMOL_PER_L_TO_MG_PER_DL,
    POUND_TO_KILOGRAM,
    to_canonical,
)
from am.health.validation import validate_measurement
from am.simulation.generator import SCENARIOS, generate
from tests.conftest import NOW, feed_baseline

LATER = NOW + timedelta(days=365)  # "now" for ingest calls on fixture timestamps


def _crew(client, code="F1-A"):
    r = client.post("/api/v1/astronauts", json={"code": code, "display_name": f"Crew {code}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _entry(client, astronaut_id, metric, value, unit, context, **kw):
    body = {"astronaut_id": astronaut_id, "metric_code": metric, "value": value, "unit": unit,
            "context": context, **kw}
    return client.post("/api/v1/manual-entries", json=body)


# ---------------------------------------------------------------- unit conversion
@pytest.mark.parametrize(
    "metric, value, unit, expected, canonical_unit",
    [
        ("body_temperature", 98.6, "degF", 37.0, "degC"),
        ("body_temperature", 32, "°F", 0.0, "degC"),
        ("body_temperature", 212, "F", 100.0, "degC"),
        ("body_mass", 1, "lb", 0.45359237, "kg"),
        ("body_mass", 154, "lbs", 154 * 0.45359237, "kg"),
        ("blood_glucose", 1, "mmol/L", 18.016, "mg/dL"),
        ("blood_glucose", 5.5, "mmol/l", 99.088, "mg/dL"),
        ("radiation_dose", 450, "µSv", 0.45, "mSv"),  # micro sign
        ("radiation_dose", 450, "μSv", 0.45, "mSv"),  # Greek mu
    ],
)
def test_conversions_use_exact_or_defined_constants(metric, value, unit, expected, canonical_unit):
    c = to_canonical(get_spec(metric), value, unit)
    assert c is not None and c.unit == canonical_unit
    assert c.value == pytest.approx(expected, abs=1e-9)
    assert c.original_value == value and c.original_unit is not None


def test_conversion_constants_match_their_sources():
    assert POUND_TO_KILOGRAM == 0.45359237
    assert GLUCOSE_MOLAR_MASS_G_PER_MOL == 180.16
    assert MMOL_PER_L_TO_MG_PER_DL == pytest.approx(18.016)


def test_canonical_unit_and_aliases_are_not_conversions_and_unknown_units_are_refused():
    same = to_canonical(get_spec("blood_glucose"), 92, "mg/dl")
    assert same is not None and same.value == 92 and same.unit == "mg/dL"
    assert same.conversion is None and same.original_value is None and same.original_unit is None
    assert to_canonical(get_spec("blood_glucose"), 92, "mmHg") is None
    assert to_canonical(get_spec("heart_rate"), 60, "degF") is None  # conversions are per metric


def test_validation_runs_on_the_canonical_value():
    temp = get_spec("body_temperature")
    # 98.6 would fail the 30-43 °C bound as a raw number; converted to 37 °C it passes.
    ok = validate_measurement(temp, value=98.6, unit="degF", context="rest", recorded_at=NOW, now=NOW)
    assert ok.accepted and ok.value == pytest.approx(37.0) and ok.unit == "degC"
    assert ok.original_value == 98.6 and ok.original_unit == "degF"
    # 35 would pass as a raw number; converted it is 1.7 °C and is rejected.
    cold = validate_measurement(temp, value=35, unit="degF", context="rest", recorded_at=NOW, now=NOW)
    assert not cold.accepted and any(n.startswith("below_sanity_bound") for n in cold.notes)

    glucose = get_spec("blood_glucose")
    # typical entry error: a mmol/L number typed while mg/dL is selected
    slip = validate_measurement(glucose, value=5.5, unit="mg/dL", context="fasting", recorded_at=NOW, now=NOW)
    assert not slip.accepted and any(n.startswith("below_sanity_bound") for n in slip.notes)
    right = validate_measurement(glucose, value=5.5, unit="mmol/L", context="fasting", recorded_at=NOW, now=NOW)
    assert right.accepted and right.value == pytest.approx(99.088)
    wrong_unit = validate_measurement(glucose, value=90, unit="mmHg", context="fasting", recorded_at=NOW, now=NOW)
    assert not wrong_unit.accepted and wrong_unit.notes[0].startswith("unit_mismatch")
    assert "mmol/L" in wrong_unit.notes[0]  # the note lists what would have been accepted
    rest = validate_measurement(glucose, value=90, unit="mg/dL", context="rest", recorded_at=NOW, now=NOW)
    assert any(n.startswith("context_not_allowed") for n in rest.notes)


def test_ingest_stores_canonical_value_and_original_only_when_converted(db, ingest, astronaut):
    t = NOW - timedelta(hours=1)
    conv = ingest.ingest(MeasurementInput(astronaut.id, "body_mass", 165.0, "lb", "daily", t, "scale-1",
                                          SourceKind.SENSOR), now=NOW)
    plain = ingest.ingest(MeasurementInput(astronaut.id, "body_mass", 74.8, "kg", "daily", t, "scale-1",
                                           SourceKind.SENSOR), now=NOW)
    db.commit()
    m1, m2 = db.get(Measurement, conv.measurement.id), db.get(Measurement, plain.measurement.id)
    assert m1.unit == "kg" and m1.value == pytest.approx(165 * 0.45359237)
    assert (m1.original_value, m1.original_unit) == (165.0, "lb")
    assert m2.value == 74.8 and m2.original_value is None and m2.original_unit is None
    # rejected conversions keep the entered value too, for traceability
    bad = ingest.ingest(MeasurementInput(astronaut.id, "body_mass", 9000.0, "lb", "daily", t, "scale-1",
                                         SourceKind.SENSOR), now=NOW)
    assert bad.measurement.quality == DataQuality.REJECTED.value
    assert bad.measurement.original_value == 9000.0 and bad.measurement.unit == "kg"
    with pytest.raises(ValueError):
        ingest.ingest(MeasurementInput(astronaut.id, "body_mass", float("nan"), "kg", "daily", t, "scale-1",
                                       SourceKind.SENSOR), now=NOW)


def test_generic_measurement_endpoint_also_converts(client, ctx):
    aid = _crew(client, "F1-GEN")
    r = client.post("/api/v1/measurements", json={
        "astronaut_id": aid, "metric_code": "body_temperature", "value": 98.6, "unit": "°F", "context": "rest",
        "recorded_at": datetime.now(UTC).isoformat(), "source_name": "thermometer-1", "source_kind": "sensor"})
    assert r.status_code == 201, r.text
    assert r.json()["measurement"]["unit"] == "degC" and r.json()["measurement"]["value"] == pytest.approx(37.0)
    with ctx.session_factory() as s:
        m = s.get(Measurement, r.json()["measurement"]["id"])
        assert (m.original_value, m.original_unit) == (98.6, "degF")


# ---------------------------------------------------------------- entry spec
def test_entry_spec_describes_units_contexts_and_bounds(client):
    spec = client.get("/api/v1/entry-spec").json()
    by_code = {m["code"]: m for m in spec["metrics"]}

    glucose = by_code["blood_glucose"]
    assert glucose["category"] == "metabolic" and glucose["unit"] == "mg/dL"
    assert glucose["contexts"] == ["fasting", "postprandial", "other"]
    mmol = next(u for u in glucose["accepted_units"] if u["unit"] == "mmol/L")
    assert "18.016" in mmol["note"] and "180.16" in mmol["source"] and mmol["factor"] == pytest.approx(18.016)
    assert glucose["accepted_units"][0]["canonical"] is True
    assert "NOT a clinical reference range" in glucose["sanity_note"]
    assert "mmol/L value entered as mg/dL" in glucose["bound_note"]
    assert glucose["alert_rule_id"] == "glucose_personal_deviation" and glucose["baseline_enabled"] is True

    rad = by_code["radiation_dose"]
    assert rad["category"] == "radiation" and rad["unit"] == "mSv" and rad["contexts"] == ["daily"]
    assert rad["baseline_enabled"] is False and rad["alert_rule_id"] is None
    assert "AMC does not evaluate exposure limits" in rad["sanity_note"]

    temp_units = {u["unit"]: u for u in by_code["body_temperature"]["accepted_units"]}
    assert temp_units["degF"]["offset"] == -32 and temp_units["degF"]["factor"] == pytest.approx(5 / 9)
    assert "lb" in {u["unit"] for u in by_code["body_mass"]["accepted_units"]}
    assert by_code["heart_rate"]["accepted_units"][0]["unit"] == "bpm"

    contexts = {c["code"]: c for c in spec["contexts"]}
    assert contexts["fasting"]["label"] == "Fasting" and contexts["postprandial"]["label"] == "After a meal"
    assert {c["code"] for c in spec["categories"]} >= {"metabolic", "radiation", "cardiovascular"}
    assert spec["default_source_name"] == "manual entry"
    assert spec["threshold_label"].startswith("DEMO")


# ---------------------------------------------------------------- manual entry
def test_manual_entry_reports_conversion_and_is_listed(client):
    aid = _crew(client)
    r = _entry(client, aid, "blood_glucose", 5.5, "mmol/L", "fasting")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["accepted"] is True
    e = out["entry"]
    assert e["value"] == pytest.approx(99.088) and e["unit"] == "mg/dL" and e["source_name"] == "manual entry"
    conv = e["conversion"]
    assert conv["entered_value"] == 5.5 and conv["entered_unit"] == "mmol/L"
    assert conv["canonical_value"] == pytest.approx(99.088) and "18.016" in conv["formula"]
    assert out["assessment"]["status"] == AssessmentStatus.NO_BASELINE.value  # nothing to compare with yet
    assert out["alert"] is None and out["threshold_label"].startswith("DEMO")

    plain = _entry(client, aid, "heart_rate", 64, "bpm", "rest").json()
    assert plain["entry"]["conversion"] is None
    assert client.get("/api/v1/manual-entries", params={"astronaut_id": aid}).json()[0]["id"] == plain["entry"]["id"]


def test_rejected_entry_is_explained_stored_and_audited(client):
    aid = _crew(client)
    slip = _entry(client, aid, "blood_glucose", 5.5, "mg/dL", "fasting")
    assert slip.status_code == 201
    body = slip.json()
    assert body["accepted"] is False and body["entry"]["quality"] == "rejected"
    assert any(n.startswith("below_sanity_bound") for n in body["entry"]["validation_notes"])
    assert body["assessment"]["status"] == AssessmentStatus.SKIPPED_REJECTED.value

    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    late = _entry(client, aid, "heart_rate", 60, "bpm", "rest", recorded_at=future).json()
    assert late["accepted"] is False and "timestamp_in_future" in late["entry"]["validation_notes"]

    listed = client.get("/api/v1/manual-entries", params={"astronaut_id": aid}).json()
    assert [x["accepted"] for x in listed] == [False, False]  # rejected entries stay visible
    audit = client.get("/api/v1/audit", params={"action_prefix": "measurement.rejected"}).json()
    assert {a["entity_id"] for a in audit} >= {str(body["entry"]["id"])}


def test_entry_quality_flags_and_request_errors(client):
    aid = _crew(client)
    low = _entry(client, aid, "spo2", 97, "%", "rest", confidence=0.3).json()
    assert low["accepted"] is True and low["entry"]["quality"] == DataQuality.DEGRADED.value
    poor = _entry(client, aid, "spo2", 97, "%", "rest", signal_quality="poor").json()
    assert poor["entry"]["quality"] == DataQuality.DEGRADED.value

    assert _entry(client, 9999, "heart_rate", 60, "bpm", "rest").status_code == 404
    assert _entry(client, aid, "nope", 1, "x", "rest").status_code == 422
    nan = client.post("/api/v1/manual-entries", content=(
        '{"astronaut_id": %d, "metric_code": "heart_rate", "value": NaN, "unit": "bpm", "context": "rest"}' % aid),
        headers={"content-type": "application/json"})
    assert nan.status_code == 422 and "finite" in nan.json()["detail"]
    # a manual entry cannot be filed under a source registered with another kind
    client.post("/api/v1/measurements", json={
        "astronaut_id": aid, "metric_code": "heart_rate", "value": 60, "unit": "bpm", "context": "rest",
        "recorded_at": datetime.now(UTC).isoformat(), "source_name": "chest-strap-9", "source_kind": "sensor"})
    clash = _entry(client, aid, "heart_rate", 61, "bpm", "rest", source_name="chest-strap-9")
    assert clash.status_code == 422 and "sensor" in clash.json()["detail"]
    # blank source name falls back to the default
    blank = _entry(client, aid, "heart_rate", 62, "bpm", "rest", source_name="  ").json()
    assert blank["entry"]["source_name"] == "manual entry"


def test_recent_entries_are_manual_only_newest_first_and_limited(client):
    run = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "F1-SIM"}).json()
    aid = run["astronaut"]["id"]
    ids = [_entry(client, aid, "water_intake", 200 + i, "mL", "daily").json()["entry"]["id"] for i in range(12)]
    listed = client.get("/api/v1/manual-entries", params={"astronaut_id": aid}).json()
    assert [x["id"] for x in listed] == list(reversed(ids))[:10]  # default limit 10
    assert all(x["source_name"] == "manual entry" and x["is_simulated"] is False for x in listed)
    assert len(client.get("/api/v1/manual-entries", params={"astronaut_id": aid, "limit": 3}).json()) == 3
    assert client.get("/api/v1/manual-entries", params={"astronaut_id": 9999}).status_code == 404

    # A high resting heart rate against the simulated baseline links to the open alert.
    hit = _entry(client, aid, "heart_rate", 150, "bpm", "rest").json()
    assert hit["accepted"] is True and hit["assessment"]["status"] == "assessed"
    assert hit["assessment"]["z"] > 0 and hit["assessment"]["severity"] in ("WATCH", "WARNING", "CRITICAL")
    assert hit["alert"] is not None and hit["entry"]["alert_id"] == hit["alert"]["id"]
    assert hit["alert"]["threshold_label"].startswith("DEMO")
    assert client.get("/api/v1/manual-entries", params={"astronaut_id": aid}).json()[0]["alert_id"] == hit["alert"]["id"]


# ---------------------------------------------------------------- blood glucose rule
def test_glucose_rule_is_personal_deviation_only(ctx):
    rule = ctx.thresholds.rule_for("blood_glucose")
    assert rule is not None and rule.rule_id == "glucose_personal_deviation"
    default = ctx.thresholds.rule_for("heart_rate")
    assert rule.bands == default.bands  # robust-z bands, no absolute mg/dL cut-offs
    assert rule.persistence.max_age_hours == 72  # DEMO: daily readings can confirm each other
    assert ctx.thresholds.rule_for("radiation_dose") is None


def test_glucose_compared_only_with_own_baseline_in_same_context(db, ingest, astronaut):
    start = NOW - timedelta(days=40)
    last = feed_baseline(ingest, astronaut.id, start=start, n=25, every_min=24 * 60, mean=90, sd=4,
                         context="fasting", metric="blood_glucose", unit="mg/dL")
    prof = db.scalars(select(BaselineProfile).where(BaselineProfile.metric_code == "blood_glucose")).one()
    assert prof.context == "fasting" and prof.status == "provisional"

    def reading(value, t, context="fasting"):
        r = ingest.ingest(MeasurementInput(astronaut.id, "blood_glucose", value, "mg/dL", context, t,
                                           "test-fixture", SourceKind.SIMULATED), now=LATER)
        db.commit()
        return r

    # A post-meal reading is never compared with the fasting baseline.
    after_meal = reading(160, last + timedelta(hours=3), context="postprandial")
    assert after_meal.assessment.status == AssessmentStatus.NO_BASELINE

    # Repeated daily deviations escalate step by step thanks to the 72 h persistence window.
    first = reading(150, last + timedelta(days=1))
    assert first.assessment.instant_severity == Severity.CRITICAL
    assert first.assessment.severity == Severity.WATCH  # a single extreme reading is surfaced, not hidden
    second = reading(152, last + timedelta(days=2))
    third = reading(151, last + timedelta(days=3))
    assert (second.assessment.severity, third.assessment.severity) == (Severity.WARNING, Severity.CRITICAL)
    assert first.alert_id is not None and first.alert_id == second.alert_id == third.alert_id


def test_radiation_dose_has_no_baseline_and_no_alerts(db, ingest, astronaut):
    t0 = NOW - timedelta(days=30)
    for i in range(25):
        r = ingest.ingest(MeasurementInput(astronaut.id, "radiation_dose", 0.4 + (i % 3) * 0.05, "mSv", "daily",
                                           t0 + timedelta(days=i), "dosimeter-1", SourceKind.SENSOR), now=LATER)
        assert r.assessment.status == AssessmentStatus.NO_BASELINE and r.alert_id is None
        assert "no anomaly rule configured for metric" in r.assessment.reasons
    big = ingest.ingest(MeasurementInput(astronaut.id, "radiation_dose", 50.0, "mSv", "daily",
                                         t0 + timedelta(days=26), "dosimeter-1", SourceKind.SENSOR), now=LATER)
    db.commit()
    assert big.measurement.quality == DataQuality.GOOD.value and big.alert_id is None
    assert db.scalars(select(BaselineProfile).where(BaselineProfile.metric_code == "radiation_dose")).first() is None


# ---------------------------------------------------------------- radiation log
def test_radiation_cumulative_sums_days_and_states_no_limits(client):
    aid = _crew(client, "F1-RAD")
    # 01:00 UTC on the two previous calendar days: always in the past, on distinct UTC dates.
    d2 = (datetime.now(UTC) - timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
    d1 = d2 - timedelta(days=1)
    _entry(client, aid, "radiation_dose", 0.4, "mSv", "daily", recorded_at=d1.isoformat())
    _entry(client, aid, "radiation_dose", 0.1, "mSv", "daily", recorded_at=(d1 + timedelta(hours=5)).isoformat())
    conv = _entry(client, aid, "radiation_dose", 350, "µSv", "daily", recorded_at=d2.isoformat()).json()
    assert conv["entry"]["value"] == pytest.approx(0.35) and conv["entry"]["conversion"]["entered_label"] == "µSv"
    rejected = _entry(client, aid, "radiation_dose", -1, "mSv", "daily", recorded_at=d2.isoformat()).json()
    assert rejected["accepted"] is False

    out = client.get("/api/v1/radiation/cumulative", params={"astronaut_id": aid}).json()
    assert out["note"] == "AMC does not evaluate exposure limits."
    assert out["unit"] == "mSv" and out["entries"] == 3 and out["simulated"] is False
    assert [(d["utc_date"], d["entries"]) for d in out["days"]] == [(d1.date().isoformat(), 2),
                                                                     (d2.date().isoformat(), 1)]
    assert [d["dose"] for d in out["days"]] == [0.5, 0.35]
    assert [d["cumulative"] for d in out["days"]] == [0.5, 0.85]
    assert out["total"] == 0.85 and out["mission_start"] is None and out["days"][0]["mission_day"] is None
    assert client.get("/api/v1/radiation/cumulative", params={"astronaut_id": 9999}).status_code == 404


# ---------------------------------------------------------------- simulator
def test_simulator_adds_glucose_and_radiation_streams_without_touching_existing_ones():
    end = datetime(2026, 11, 14, 12, 0, 0)
    scenario = SCENARIOS["acute_rest_hr"]
    with_m5 = generate(scenario, astronaut_id=1, end=end, seed=42)
    without = generate(replace(scenario, extra_streams=[]), astronaut_id=1, end=end, seed=42)

    def key(x):
        return (x.metric_code, x.context, x.value, x.recorded_at)

    base = [x for x in with_m5 if x.metric_code not in ("blood_glucose", "radiation_dose")]
    assert [key(x) for x in base] == [key(x) for x in without]

    glucose = [x for x in with_m5 if x.metric_code == "blood_glucose"]
    rad = [x for x in with_m5 if x.metric_code == "radiation_dose"]
    assert glucose and all(x.context == "fasting" and x.unit == "mg/dL" for x in glucose)
    assert rad and all(x.context == "daily" and x.unit == "mSv" for x in rad)
    assert len({x.recorded_at.date() for x in glucose}) == len(glucose)  # once a day
    assert len({x.recorded_at.date() for x in rad}) == len(rad)
    assert all(x.source_kind == SourceKind.SIMULATED for x in glucose + rad)


def test_simulated_run_feeds_the_radiation_log(client):
    run = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "F1-SR"}).json()
    aid = run["astronaut"]["id"]
    assert run["rejected"] == 0
    out = client.get("/api/v1/radiation/cumulative", params={"astronaut_id": aid}).json()
    assert out["simulated"] is True and out["entries"] >= 3
    assert out["days"][-1]["cumulative"] == out["total"]
    assert out["days"][0]["mission_day"] == 1
    glucose = client.get("/api/v1/measurements", params={"astronaut_id": aid, "metric_code": "blood_glucose"}).json()
    assert glucose and all(m["is_simulated"] and m["context"] == "fasting" for m in glucose)


# ---------------------------------------------------------------- crew and missions
def test_mission_environments_are_plain_descriptions(client):
    envs = client.get("/api/v1/missions/environments").json()
    codes = [e["code"] for e in envs]
    assert codes[:5] == ["leo_station", "lunar_surface", "deep_space_transit", "mars_surface", "ground_analog"]
    assert "unspecified" in codes
    assert all(e["label"] and e["description"] for e in envs)
    assert not any(ch.isdigit() for e in envs for ch in e["description"])  # no physiological numbers


def test_create_mission_with_environment(client, ctx):
    aid = _crew(client, "F1-MIS")
    start = (datetime.now(UTC).date() - timedelta(days=4)).isoformat()
    r = client.post("/api/v1/missions", json={"astronaut_id": aid, "name": "Habitat run 1", "start_date": start,
                                              "planned_days": 30, "environment": "ground_analog",
                                              "actor": "ops-1"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["environment"] == "ground_analog" and m["environment_label"] == "Ground analog"
    assert m["is_current"] is True and m["is_simulated"] is False and m["planned_days"] == 30

    later = client.post("/api/v1/missions", json={"astronaut_id": aid, "name": "Transit", "start_date": start,
                                                  "environment": "deep_space_transit"}).json()
    listed = client.get("/api/v1/missions", params={"astronaut_id": aid}).json()
    assert [x["id"] for x in listed] == [later["id"], m["id"]]
    assert [x["is_current"] for x in listed] == [True, False]
    assert client.get(f"/api/v1/astronauts/{aid}/overview").json()["mission_day"] == 5

    with ctx.session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "mission.created")).all()
        assert {x.actor for x in log} == {"ops-1", "operator"}

    bad = {"astronaut_id": aid, "name": "X", "start_date": start, "environment": "jupiter"}
    assert client.post("/api/v1/missions", json=bad).status_code == 422
    assert client.post("/api/v1/missions", json={**bad, "environment": "leo_station", "name": "  "}).status_code == 422
    assert client.post("/api/v1/missions", json={**bad, "environment": "leo_station",
                                                  "start_date": (date.today() + timedelta(days=3)).isoformat()}
                       ).status_code == 422
    assert client.post("/api/v1/missions", json={**bad, "environment": "leo_station", "astronaut_id": 9999}
                       ).status_code == 404
    assert client.post("/api/v1/missions", json={**bad, "environment": "leo_station", "planned_days": 0}
                       ).status_code == 422


def test_mission_on_simulated_record_is_marked_simulated(client):
    run = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "F1-SM"}).json()
    r = client.post("/api/v1/missions", json={"astronaut_id": run["astronaut"]["id"], "name": "Extra",
                                              "start_date": run["mission"]["start_date"],
                                              "environment": "leo_station"})
    assert r.status_code == 201 and r.json()["is_simulated"] is True
