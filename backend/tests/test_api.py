"""API integration tests (FastAPI TestClient, migrations on a temp DB, no LLM needed)."""

from datetime import UTC, datetime, timedelta


def _sim(client, scenario, code):
    r = client.post("/api/v1/simulation/run", json={"scenario": scenario, "astronaut_code": code, "seed": 42})
    assert r.status_code == 201, r.text
    return r.json()


def test_system_status_works_without_ollama(client):
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok"
    assert body["ollama"]["reachable"] is False
    assert body["threshold_label"].startswith("DEMO")


def test_metric_registry_exposes_sanity_note(client):
    metrics = client.get("/api/v1/metrics").json()
    hr = next(m for m in metrics if m["code"] == "heart_rate")
    assert hr["unit"] == "bpm"
    assert "NOT a clinical reference" in hr["plausibility_note"]


def test_manual_measurement_flow_and_rejection(client):
    a = client.post("/api/v1/astronauts", json={"code": "A-1", "display_name": "Crew A"}).json()
    assert a["is_simulated"] is False
    assert client.post("/api/v1/astronauts", json={"code": "A-1", "display_name": "dup"}).status_code == 409

    now = datetime.now(UTC)
    ok = client.post("/api/v1/measurements", json={
        "astronaut_id": a["id"], "metric_code": "heart_rate", "value": 66, "unit": "BPM",
        "context": "rest", "recorded_at": now.isoformat(), "source_name": "chest-strap-1", "source_kind": "sensor",
    })
    assert ok.status_code == 201
    body = ok.json()
    assert body["measurement"]["unit"] == "bpm"
    assert body["measurement"]["is_simulated"] is False
    assert body["assessment"]["status"] == "no_baseline"

    bad = client.post("/api/v1/measurements", json={
        "astronaut_id": a["id"], "metric_code": "heart_rate", "value": 999, "unit": "bpm",
        "context": "rest", "recorded_at": now.isoformat(), "source_name": "chest-strap-1", "source_kind": "sensor",
    }).json()
    assert bad["measurement"]["quality"] == "rejected"

    assert client.post("/api/v1/measurements", json={
        "astronaut_id": a["id"], "metric_code": "nope", "value": 1, "unit": "x",
        "context": "rest", "recorded_at": now.isoformat(), "source_name": "s",
    }).status_code == 422
    assert client.post("/api/v1/measurements", json={
        "astronaut_id": 9999, "metric_code": "heart_rate", "value": 60, "unit": "bpm",
        "context": "rest", "recorded_at": now.isoformat(), "source_name": "s",
    }).status_code == 404

    listed = client.get("/api/v1/measurements", params={"astronaut_id": a["id"]}).json()
    assert len(listed) == 1  # rejected hidden by default
    assert len(client.get("/api/v1/measurements",
                          params={"astronaut_id": a["id"], "include_rejected": True}).json()) == 2
    audit = client.get("/api/v1/audit", params={"action_prefix": "measurement."}).json()
    assert audit[0]["action"] == "measurement.rejected"


def test_nominal_simulation_has_no_warning_or_critical_alerts(client):
    run = _sim(client, "nominal", "SIM-NOM")
    assert run["label"] == "SIMULATED DATA"
    assert run["astronaut"]["is_simulated"] is True
    assert run["rejected"] == 0
    alerts = client.get("/api/v1/alerts", params={"astronaut_id": run["astronaut"]["id"]}).json()
    assert all(a["peak_severity"] in ("WATCH",) for a in alerts), [a["peak_severity"] for a in alerts]
    baselines = client.get("/api/v1/baselines", params={"astronaut_id": run["astronaut"]["id"]}).json()
    keys = {(b["metric_code"], b["context"]) for b in baselines}
    assert ("heart_rate", "rest") in keys and ("heart_rate", "exercise") in keys


def test_acute_simulation_raises_critical_alert_with_evidence_and_ack_flow(client):
    run = _sim(client, "acute_rest_hr", "SIM-ACU")
    aid = run["astronaut"]["id"]
    alerts = client.get("/api/v1/alerts", params={"astronaut_id": aid, "open_only": True}).json()
    hr = [a for a in alerts if a["metric_code"] == "heart_rate" and a["context"] == "rest" and a["direction"] == "high"]
    assert hr and hr[0]["peak_severity"] == "CRITICAL"
    assert hr[0]["is_simulated"] is True

    detail = client.get(f"/api/v1/alerts/{hr[0]['id']}").json()
    assert len(detail["evidence"]) >= 3
    assert all(e["measurement"]["is_simulated"] for e in detail["evidence"])

    # exercise HR (also high values) must not be compared to the rest baseline
    assert not [a for a in alerts if a["context"] == "exercise" and a["peak_severity"] in ("WARNING", "CRITICAL")]

    # acknowledgement & resolution
    assert client.post(f"/api/v1/alerts/{hr[0]['id']}/resolve", json={"actor": "fs", "note": ""}).status_code == 422
    ack = client.post(f"/api/v1/alerts/{hr[0]['id']}/acknowledge", json={"actor": "flight-surgeon", "note": "seen"})
    assert ack.json()["status"] == "acknowledged"
    res = client.post(f"/api/v1/alerts/{hr[0]['id']}/resolve",
                      json={"actor": "flight-surgeon", "note": "simulated scenario reviewed"})
    assert res.json()["status"] == "resolved"
    assert [x["action"] for x in res.json()["acks"]] == ["acknowledge", "resolve"]
    assert client.post(f"/api/v1/alerts/{hr[0]['id']}/acknowledge", json={"actor": "x"}).status_code == 409


def test_gradual_decline_does_not_absorb_drift(client):
    run = _sim(client, "gradual_decline", "SIM-GRD")
    aid = run["astronaut"]["id"]
    history = client.get("/api/v1/baselines", params={"astronaut_id": aid, "include_history": True,
                                                     "metric_code": "heart_rate"}).json()
    rest = [b for b in history if b["context"] == "rest"]
    anchor = next(b for b in rest if b["is_anchor"])
    current = [b for b in rest if b["status"] != "superseded"][0]
    # simulated drift reaches +14 bpm; the baseline must stay near the anchor and freeze
    assert current["median"] - anchor["median"] < 5
    assert current["is_frozen"] is True
    alerts = client.get("/api/v1/alerts", params={"astronaut_id": aid}).json()
    assert any(a["metric_code"] == "heart_rate" and a["context"] == "rest" for a in alerts)
    audit = client.get("/api/v1/baselines/audit", params={"astronaut_id": aid, "metric_code": "heart_rate",
                                                         "context": "rest"}).json()
    assert any(x["action"] == "frozen" for x in audit)


def test_simulation_rejects_duplicate_code_and_unknown_scenario(client):
    _sim(client, "nominal", "SIM-DUP")
    assert client.post("/api/v1/simulation/run", json={"scenario": "nominal", "astronaut_code": "SIM-DUP"}
                       ).status_code == 409
    assert client.post("/api/v1/simulation/run", json={"scenario": "nope"}).status_code == 404


def test_baseline_rebuild_and_unfreeze_endpoints(client):
    run = _sim(client, "gradual_decline", "SIM-RB")
    aid = run["astronaut"]["id"]
    rest = [b for b in client.get("/api/v1/baselines", params={"astronaut_id": aid}).json()
            if b["metric_code"] == "heart_rate" and b["context"] == "rest"][0]
    assert client.post(f"/api/v1/baselines/{rest['id']}/unfreeze",
                       json={"actor": "fs", "reason": "x"}).status_code == 422  # reason too short
    un = client.post(f"/api/v1/baselines/{rest['id']}/unfreeze", json={"actor": "fs", "reason": "medical review"})
    assert un.status_code == 200 and un.json()["is_frozen"] is False

    end = datetime.now(UTC)
    rb = client.post("/api/v1/baselines/rebuild", json={
        "astronaut_id": aid, "metric_code": "heart_rate", "context": "rest",
        "window_start": (end - timedelta(days=30)).isoformat(), "window_end": (end - timedelta(days=20)).isoformat(),
        "actor": "flight-surgeon", "reason": "re-anchor on pre-decline window",
    })
    assert rb.status_code == 201, rb.text
    assert rb.json()["is_anchor"] is True and rb.json()["supersedes_id"] == rest["id"]
