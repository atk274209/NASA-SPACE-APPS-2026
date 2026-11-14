"""M4 integration: knowledge base, correlation, journal, missions, research observations."""

from __future__ import annotations

import json

import pytest

from am.rag.knowledge import ingest_directory
from tests.test_companion import FakeOllama


@pytest.fixture(scope="module")
def _unused():  # pragma: no cover
    return None


@pytest.fixture
def decline(client):
    r = client.post("/api/v1/simulation/run", json={"scenario": "gradual_decline", "astronaut_code": "M4-1"})
    assert r.status_code == 201
    return r.json()["astronaut"]["id"]


def test_knowledge_ingest_offline_uses_lexical_search(client):
    client.app.state.ctx.llm = FakeOllama([]).client()  # no /api/embed -> embeddings unavailable
    rep = client.post("/api/v1/knowledge/ingest").json()
    assert rep["skipped"] == [] and rep["documents_total"] >= 10
    assert rep["embedded"] == 0 and rep["embed_error"] == "model_not_installed"
    hits = client.get("/api/v1/knowledge/search", params={"q": "bone fracture resistive exercise"}).json()
    assert hits and hits[0]["method"] == "lexical"
    assert "bone" in hits[0]["doc_slug"]
    assert all(h["url"].startswith("https://") for h in hits)
    again = client.post("/api/v1/knowledge/ingest").json()
    assert again["unchanged"] == rep["documents_total"] and again["loaded"] == 0
    docs = client.get("/api/v1/knowledge/documents").json()
    assert all(d["license_basis"] for d in docs)


def test_ingest_rejects_invalid_or_copyright_passages(tmp_path, ctx):
    good = {"id": "ok", "title": "T", "url": "https://x.gov/a", "source_type": "nasa_web_page",
            "license_basis": "NASA web page, US government work", "passages": [{"text": "p"}]}
    bad_url = {**good, "id": "bad-url", "url": "ftp://x"}
    copyrighted = {**good, "id": "cr", "license_basis": "copyrighted journal article", "passages": [{"text": "q"}]}
    missing = {"id": "missing", "title": "T"}
    for d in (good, bad_url, copyrighted, missing):
        (tmp_path / f"{d['id']}.json").write_text(json.dumps(d), encoding="utf-8")
    with ctx.session_factory() as s:
        rep = ingest_directory(s, tmp_path, None, "none")
    assert rep.loaded == 1
    assert {x.split(":")[0] for x in rep.skipped} == {"bad-url.json", "cr.json", "missing.json"}


def test_correlation_finds_injected_trends_and_flags_shared_trends(client, decline):
    run = client.post("/api/v1/research/correlation/run", json={"astronaut_id": decline}).json()
    trends = {f["y_key"]: f for f in run["findings"] if f["finding_type"] == "trend"}
    assert trends["heart_rate:rest"]["rho"] > 0.7 and trends["heart_rate:rest"]["slope_per_day"] > 0
    assert trends["hrv_rmssd:rest"]["rho"] < -0.7
    pairs = [f for f in run["findings"] if f["finding_type"] != "trend"]
    # simulated streams have independent noise: no association should survive detrending
    assert pairs and all(f["detrended_supported"] is False for f in pairs)
    assert all("does not establish causation" in f["caveat"] for f in run["findings"])
    assert run["is_simulated"] is True
    latest = client.get("/api/v1/research/correlation/latest", params={"astronaut_id": decline}).json()
    assert latest["id"] == run["id"]


def test_journal_reports_against_mission_start_reference(client, decline):
    entries = client.post("/api/v1/journal/generate", json={"astronaut_id": decline}).json()
    by_day = {e["mission_day"]: e for e in entries}
    day = max(d for d, e in by_day.items() if e["complete_day"])
    physical = {i["key"]: i for i in by_day[day]["sections"]["physical"]}
    assert physical["heart_rate:rest"]["status"] == "above reference"
    assert physical["exercise_duration:daily"]["status"] == "below reference"
    assert physical["heart_rate:rest"]["reference_days"] == [1, 7]
    assert by_day[3]["sections"]["physical"][0]["status"] == "reference period"
    assert any("Persistent change" in f["text"] for f in by_day[day]["flags"])
    assert any(f.get("severity") in ("WARNING", "CRITICAL") for f in by_day[day]["flags"])
    long_mem = client.get("/api/v1/memory", params={"astronaut_id": decline, "layer": "long"}).json()
    assert long_mem and all(m["source"] == "journal" and m["content"].startswith("Pattern:") for m in long_mem)
    # regenerating is idempotent: no duplicate entries or patterns
    client.post("/api/v1/journal/generate", json={"astronaut_id": decline})
    assert len(client.get("/api/v1/journal", params={"astronaut_id": decline}).json()) == len(entries)
    assert len(client.get("/api/v1/memory", params={"astronaut_id": decline, "layer": "long"}).json()) == len(long_mem)
    assert client.post("/api/v1/journal/generate", json={"astronaut_id": decline, "first_day": 0,
                                                         "last_day": 0}).status_code == 422


def test_missions_adapt_and_verify_completion_from_data(client, decline):
    today = client.get("/api/v1/missions/today", params={"astronaut_id": decline}).json()
    tasks = {t["template_code"]: t for t in today["tasks"]}
    assert tasks["exercise_session"]["priority"] == 1
    assert "below your mission-start reference" in tasks["exercise_session"]["reason"]
    assert tasks["alert_review"]["priority"] == 1 and tasks["alert_review"]["reason_refs"][0]["type"] == "alert"
    assert all(t["protocol_ref"].startswith("AM-DEMO-") for t in today["tasks"])
    assert "DEMO" in today["protocol_note"]
    # manual completion
    r = client.post(f"/api/v1/missions/tasks/{tasks['daily_reflection']['id']}", json={"status": "done"}).json()
    assert r["status"] == "done" and r["completed_by"] == "astronaut"
    # check-in completes the check-in task from data; data-verified tasks cannot be reopened
    client.post("/api/v1/checkins", json={"astronaut_id": decline, "ratings": {"mood": 3}})
    again = {t["template_code"]: t for t in client.get("/api/v1/missions/today",
                                                       params={"astronaut_id": decline}).json()["tasks"]}
    assert again["daily_checkin"]["status"] == "done" and again["daily_checkin"]["completed_by"] == "data"
    assert client.post(f"/api/v1/missions/tasks/{again['daily_checkin']['id']}",
                       json={"status": "pending"}).status_code == 422
    assert client.post(f"/api/v1/missions/tasks/{again['hydration_log']['id']}",
                       json={"status": "nope"}).status_code == 422
    # stable: same tasks, no duplicates
    assert len(again) == len(tasks)


def test_research_observation_with_validated_citations(client, decline):
    client.app.state.ctx.llm = FakeOllama([]).client()
    client.post("/api/v1/knowledge/ingest")
    run = client.post("/api/v1/research/correlation/run", json={"astronaut_id": decline}).json()
    trend = next(f for f in run["findings"] if f["y_key"] == "heart_rate:rest" and f["finding_type"] == "trend")

    # retrieval is lexical here; find which chunk ids the observation will see
    hits = client.get("/api/v1/knowledge/search",
                      params={"q": "spaceflight cardiovascular deconditioning heart rate orthostatic intolerance",
                              "k": 4}).json()
    good_id = hits[0]["chunk_id"]
    fake = FakeOllama([{"role": "assistant", "content": json.dumps({
        "hypothesis": "Resting heart rate may rise with cardiovascular deconditioning.",
        "rationale": "The excerpt describes deconditioning.", "cited_chunk_ids": [good_id, 999999]})}])
    client.app.state.ctx.llm = fake.client()
    obs = client.post("/api/v1/research/observations", json={"finding_id": trend["id"]}).json()
    assert obs["generated_by"] == "llm"
    assert obs["grounding"]["cited_chunk_ids"] == [good_id]
    assert obs["grounding"]["invalid_citations_removed"] == [999999]
    assert obs["evidence_level"] == "trend" and "SIMULATED" in obs["confidence_note"]
    assert obs["doc_refs"] and obs["scientific_context"]
    assert "format" in fake.requests[0]  # structured output requested

    # without the model: clearly labelled template hypothesis
    tmpl = client.post("/api/v1/research/observations", json={"finding_id": trend["id"], "use_llm": False}).json()
    assert tmpl["generated_by"] == "deterministic" and tmpl["hypothesis"].startswith("Template hypothesis")

    rev = client.post(f"/api/v1/research/observations/{obs['id']}/review",
                      json={"status": "reviewed", "actor": "pi"}).json()
    assert rev["status"] == "reviewed"
    assert client.post("/api/v1/research/observations", json={"finding_id": 999999}).status_code == 404


def test_baseline_freeze_is_attributed_to_data_time_not_window(client, decline):
    entries = {e["mission_day"]: e for e in client.post("/api/v1/journal/generate",
                                                        json={"astronaut_id": decline}).json()}
    audit = client.get("/api/v1/baselines/audit", params={"astronaut_id": decline, "metric_code": "heart_rate",
                                                         "context": "rest"}).json()
    frozen = [a for a in audit if a["action"] == "frozen"]
    assert frozen and "data_time" in frozen[0]["details"]
    freeze_flag_days = [d for d, e in entries.items() if any("frozen pending review" in f["text"] for f in e["flags"])]
    assert freeze_flag_days and min(freeze_flag_days) > 7  # not during the reference week


def test_journal_alert_flags_use_peak_as_of_that_day(client, decline):
    entries = {e["mission_day"]: e for e in client.post("/api/v1/journal/generate",
                                                        json={"astronaut_id": decline}).json()}
    crit_days = [d for d, e in entries.items() if any(f.get("severity") == "CRITICAL" for f in e["flags"])]
    alerts = client.get("/api/v1/alerts", params={"astronaut_id": decline}).json()
    hr = next(a for a in alerts if a["metric_code"] == "heart_rate" and a["context"] == "rest")
    detail = client.get(f"/api/v1/alerts/{hr['id']}").json()
    first_crit = min(e["measurement"]["recorded_at"] for e in detail["evidence"] if e["severity_at_time"] == "CRITICAL")
    start = client.get(f"/api/v1/astronauts/{decline}/overview").json()["mission"]["start_date"]
    from datetime import date, datetime

    crit_day = (datetime.fromisoformat(first_crit).date() - date.fromisoformat(start)).days + 1
    assert crit_days and min(crit_days) == crit_day


def test_verbatim_passages_need_explicit_government_work_basis():
    from am.rag.knowledge import _passages_allowed

    assert _passages_allowed("NASA web page, US government work; generally public domain in the US.")
    assert not _passages_allowed("NASA Technical Reports Server record. Public-domain status not stated.")
    assert not _passages_allowed("Peer-reviewed journal article; copyright not verified")
    assert not _passages_allowed("Contractor-authored report")
