"""M6f: hydration and supplements, crew leaderboard (adherence, opt-in)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from am.crew_seed import seed_crew
from am.db.models import Measurement
from tests.conftest import sign_in


def _crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    out = {}
    for u in ("ast001", "ast002", "ast003"):
        c = TestClient(client.app)
        out[u] = (c, sign_in(c, role="astronaut", username=u).astronaut_id)
    return out


def test_water_and_supplements_are_logged_and_closed_into_one_daily_total(client, tmp_path):
    crew = _crew(client, tmp_path)
    me, a = crew["ast001"]
    for ml in (500, 500, 500, 500, 600):
        r = me.post("/api/v1/nutrition/water", json={"astronaut_id": a, "amount_ml": ml})
        assert r.status_code == 201
    st = r.json()
    assert st["total_ml"] == 2600 and st["target_met"] and st["progress"] == 1.0 and "DEMO" in st["label"]
    assert me.post("/api/v1/nutrition/water", json={"astronaut_id": a, "amount_ml": 5000}).status_code == 422
    last = st["entries"][-1]["id"]
    assert me.post(f"/api/v1/nutrition/water/{last}/undo").json()["total_ml"] == 2000
    assert me.post("/api/v1/nutrition/water", json={"astronaut_id": a, "amount_ml": 500}).status_code == 201
    st = me.post("/api/v1/nutrition/supplements", json={"astronaut_id": a, "supplement_code": "vitamin_d"}).json()
    assert {x["code"]: x["taken"] for x in st["supplements"]}["vitamin_d"] is True
    other, b = crew["ast002"]
    assert other.post("/api/v1/nutrition/water", json={"astronaut_id": a, "amount_ml": 250}).status_code == 403
    assert other.post(f"/api/v1/nutrition/water/{last - 1}/undo").status_code == 403
    run = client.post("/api/test/end-day", json={}).json()
    assert run["status"] == "completed"
    step = run["steps"]["hydration"]["detail"][str(a)]
    assert step["total_ml"] == 2500 and step["measurement_id"] and step["points"] == 15
    with client.app.state.ctx.session_factory() as s:
        totals = s.scalars(select(Measurement).where(Measurement.astronaut_id == a,
                                                     Measurement.metric_code == "water_intake")).all()
        assert [m.value for m in totals] == [2500]


def test_leaderboard_ranks_adherence_capped_and_respects_opt_out(client, tmp_path):
    crew = _crew(client, tmp_path)
    (c1, a1), (c2, a2), (c3, a3) = crew["ast001"], crew["ast002"], crew["ast003"]
    c1.post("/api/v1/nutrition/water", json={"astronaut_id": a1, "amount_ml": 1500})  # 60 %
    for _ in range(4):
        c2.post("/api/v1/nutrition/water", json={"astronaut_id": a2, "amount_ml": 1500})  # 6 L: capped at 100 %
    c3.post("/api/v1/nutrition/water", json={"astronaut_id": a3, "amount_ml": 1500})
    c3.post("/api/v1/nutrition/water", json={"astronaut_id": a3, "amount_ml": 1000})  # exactly the target
    board = c1.get("/api/v1/nutrition/leaderboard").json()
    rows = {r["code"]: r for r in board["rows"]}
    assert rows["AST-002"]["adherence_pct"] == rows["AST-003"]["adherence_pct"] == 100  # no reward above target
    assert rows["AST-001"]["adherence_pct"] == 60 and rows["AST-001"]["rank"] > rows["AST-003"]["rank"]
    assert all("total_ml" not in r and "amount_ml" not in r for r in board["rows"])
    assert "earns nothing extra" in board["rule"]
    assert c2.post("/api/v1/profile/leaderboard", json={"astronaut_id": a2, "opt_in": False}).json()["opt_in"] is False
    assert "AST-002" not in {r["code"] for r in c1.get("/api/v1/nutrition/leaderboard").json()["rows"]}
    assert c1.post("/api/v1/profile/leaderboard", json={"astronaut_id": a2, "opt_in": True}).status_code == 403


def test_water_said_in_chat_goes_to_the_hydration_log(client, tmp_path):
    crew = _crew(client, tmp_path)
    me, a = crew["ast001"]
    conv = me.post("/api/v1/companion/conversations", json={"astronaut_id": a}).json()
    me.post(f"/api/v1/companion/conversations/{conv['id']}/messages", json={"content": "Bugün 1 litre su içtim"})
    prop = me.get("/api/v1/measurement-proposals", params={"astronaut_id": a}).json()[0]
    assert prop["metric_code"] == "water_intake"
    assert me.post(f"/api/v1/measurement-proposals/{prop['id']}/confirm").json()["status"] == "confirmed"
    assert me.get("/api/v1/nutrition/today", params={"astronaut_id": a}).json()["total_ml"] == 1000
