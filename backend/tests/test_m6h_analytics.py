"""M6h: crew analytics for the doctor's bento board (every number from stored records)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from am.crew_seed import seed_crew
from tests.conftest import sign_in


def test_crew_analytics_reflects_stored_records(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    a = sign_in(me, role="astronaut", username="ast001").astronaut_id
    me.post("/api/v1/nutrition/water", json={"astronaut_id": a, "amount_ml": 750})
    me.post("/api/test/symptoms", json={"astronaut_id": a, "symptom_code": "headache", "severity": "mild"})
    me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 5, "stress": 1}, "note": "Biraz başım ağrıyor"})
    data = client.get("/api/v1/doctor/analytics").json()
    assert data["water"]["value"] == 750 and len(data["water"]["series"]) == 14
    assert data["water"]["series"][-1]["value"] == 750
    act = data["activity"]
    assert act["value"] == 2 and sum(act["written"][0]) == 2 and len(act["written"]) == 3 and len(act["written"][0]) == 24
    assert sum(act["flagged"][0]) == 2  # symptom report and check-in note both yellow
    prob = data["problems"]
    assert [p["total"] for p in prob["periods"]] == [1, 1, 1]
    assert prob["items"] == [{"name": "Headache", "group": "neuro_behavioral", "values": [1, 1, 1]}]
    assert next(r for r in prob["table"]["rows"] if "AST-001" in r["label"])["values"] == ["1", "1", "1"]
    wb = {y["label"]: y for y in data["wellbeing"]["years"]}
    mine = wb["AST-001"]
    assert len(data["wellbeing"]["axis"]) == 8 and mine["values"][0] == 100 and mine["values"][1] == 100  # mood 5, stress 1
    assert mine["values"][5] == 30  # 750 of 2500 mL
    assert me.get("/api/v1/doctor/analytics").status_code == 403
