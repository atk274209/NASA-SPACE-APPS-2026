"""MVP plumbing: demo seed (simulated crew) and serving the built console from the API."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from am.config import Settings
from am.db.models import Astronaut
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.db.models_research import JournalEntry
from am.main import create_app


def test_seed_demo_creates_labelled_simulated_crew(settings, monkeypatch):
    from am import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["seed-demo"]) == 0
    assert cli.main(["seed-demo"]) == 0  # idempotent: no duplicate crew
    ctx = cli._ctx()
    with ctx.session_factory() as s:
        crew = s.scalars(select(Astronaut)).all()
        assert sorted(a.code for a in crew) == ["SIM-ALPHA", "SIM-BRAVO", "SIM-CHARLIE"]
        assert all(a.is_simulated for a in crew)
        assert all(r.is_simulated for r in s.scalars(select(SelfReport)))
        reports = s.scalars(select(SymptomReport)).all()
        assert reports and all(r.is_simulated and r.source == "simulated" for r in reports)
        alpha = next(a for a in crew if a.code == "SIM-ALPHA")
        levels = {e.mission_day: e.day_level for e in s.scalars(select(JournalEntry).where(
            JournalEntry.astronaut_id == alpha.id))}
        assert levels[27] == "red"  # severe back pain reported that day
        assert levels[12] in ("yellow", "red")  # constipation reported that day
    ctx.engine.dispose()
    ctx.llm.close()


def test_built_console_is_served_with_spa_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>AMC</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    s = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{(tmp_path / 'db.sqlite3').as_posix()}",
                 ollama_url="http://127.0.0.1:9", allowed_hosts=["testserver"], frontend_dist=dist)
    with TestClient(create_app(s)) as c:
        assert c.get("/").text.startswith("<!doctype html>")
        assert c.get("/journal").text.startswith("<!doctype html>")  # client-side route
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/favicon.svg").text == "<svg/>"
        assert "nope" not in c.get("/../secret.txt").text
        assert c.get("/api/v1/does-not-exist").status_code == 404
        assert c.get("/api/v1/system/status").status_code == 401  # the API itself needs a sign-in
        state = c.get("/api/v1/auth/state").json()
        assert state["signed_in"] is False and state["accounts_exist"] is False and state["setup_allowed"] is True


def test_resolving_an_alert_closes_its_escalations(client):
    run = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "ESC-1"}).json()
    aid = run["astronaut"]["id"]
    events = client.get("/api/v1/escalations", params={"astronaut_id": aid}).json()
    assert events and any(e["status"] == "open" for e in events)
    alert_id = events[0]["alert_id"]
    r = client.post(f"/api/v1/alerts/{alert_id}/resolve", json={"actor": "cmo", "note": "reviewed in simulation"})
    assert r.status_code == 200
    after = [e for e in client.get("/api/v1/escalations", params={"astronaut_id": aid}).json()
             if e["alert_id"] == alert_id]
    assert after and all(e["status"] == "closed" for e in after)


def test_status_and_overview_expose_new_fields(client):
    run = client.post("/api/v1/simulation/run", json={"scenario": "nominal", "astronaut_code": "OV-1"}).json()
    ov = client.get(f"/api/v1/astronauts/{run['astronaut']['id']}/overview").json()
    assert ov["mission"]["environment"] == "unspecified"
    rad = [r for r in ov["latest"] if r["metric_code"] == "radiation_dose"]
    assert rad and rad[0]["baseline_enabled"] is False
    assert all("original_value" in r["measurement"] for r in ov["latest"])
    st = client.get("/api/v1/system/status").json()
    assert st["ollama"]["configured_chat_model"] == "llama3.1:8b"


def test_slow_model_is_reported_as_timeout_not_absent():
    import httpx

    from am.ai.ollama import LLMUnavailable, OllamaClient

    def slow(_request):
        raise httpx.ReadTimeout("slow")

    client = OllamaClient("http://ollama.test", transport=httpx.MockTransport(slow))
    try:
        client.chat("m", [{"role": "user", "content": "hi"}])
    except LLMUnavailable as exc:
        assert exc.reason == "model_timeout"
    else:
        raise AssertionError("expected LLMUnavailable")
