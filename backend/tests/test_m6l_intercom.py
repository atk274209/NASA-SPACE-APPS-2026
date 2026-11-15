"""M6l: intercom loops between the crew, the flight surgeon and AM."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from am.crew_seed import seed_crew
from tests.conftest import sign_in


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    a1, a2 = TestClient(client.app), TestClient(client.app)
    id1 = sign_in(a1, role="astronaut", username="ast001").astronaut_id
    id2 = sign_in(a2, role="astronaut", username="ast002").astronaut_id
    return a1, id1, a2, id2


def test_each_user_sees_only_their_loops(client, crew):
    a1, id1, a2, id2 = crew
    mine = [c["id"] for c in a1.get("/api/v1/intercom/channels").json()]
    assert mine == ["crew", f"ast-{id1}"]
    doctor = [c["id"] for c in client.get("/api/v1/intercom/channels").json()]
    assert "crew" in doctor and f"ast-{id1}" in doctor and f"ast-{id2}" in doctor
    assert a1.get(f"/api/v1/intercom/channels/ast-{id2}/messages").status_code == 404
    assert a1.post(f"/api/v1/intercom/channels/ast-{id2}/messages", json={"body": "hi"}).status_code == 404


def test_private_loop_reaches_only_the_doctor_and_counts_unread(client, crew):
    a1, id1, a2, _ = crew
    ch = f"ast-{id1}"
    r = client.post(f"/api/v1/intercom/channels/{ch}/messages", json={"body": "Call me on the private loop.", "urgent": True})
    assert r.status_code == 201 and r.json()["sender_role"] == "doctor"
    unread = {c["id"]: c["unread"] for c in a1.get("/api/v1/intercom/channels").json()}
    assert unread[ch] == 1
    msgs = a1.get(f"/api/v1/intercom/channels/{ch}/messages").json()
    assert [m["body"] for m in msgs] == ["Call me on the private loop."] and msgs[0]["urgent"]
    assert {c["id"]: c["unread"] for c in a1.get("/api/v1/intercom/channels").json()}[ch] == 0
    a1.post(f"/api/v1/intercom/channels/{ch}/messages", json={"body": "On it."})
    after = client.get(f"/api/v1/intercom/channels/{ch}/messages", params={"after_id": msgs[0]["id"]}).json()
    assert [m["body"] for m in after] == ["On it."]


def test_crew_loop_is_shared_and_am_announces_crew_wide_events_only_there(client, crew):
    a1, id1, a2, _ = crew
    a2.post("/api/v1/intercom/channels/crew/messages", json={"body": "Dinner at 19:00."})
    assert [m["body"] for m in a1.get("/api/v1/intercom/channels/crew/messages").json()] == ["Dinner at 19:00."]
    client.post("/api/v1/demo/events", json={"kind": "solar_particle_event"})
    crew_msgs = a1.get("/api/v1/intercom/channels/crew/messages").json()
    assert crew_msgs[-1]["sender_role"] == "am" and "Solar particle event" in crew_msgs[-1]["body"]
    assert "[SIMULATED]" in crew_msgs[-1]["body"]


def test_personal_events_go_to_the_private_loop_not_the_crew(client, crew):
    a1, id1, a2, id2 = crew
    for _ in range(8):
        client.post("/api/v1/demo/events", json={"kind": "symptom_report"})
    crew_bodies = [m["body"] for m in client.get("/api/v1/intercom/channels/crew/messages").json()]
    assert not any("Symptom reported" in b for b in crew_bodies)
    private = client.get(f"/api/v1/intercom/channels/ast-{id1}/messages").json() + \
        client.get(f"/api/v1/intercom/channels/ast-{id2}/messages").json()
    assert all(m["sender_role"] == "am" for m in private)


def test_empty_message_is_refused(client, crew):
    a1, *_ = crew
    assert a1.post("/api/v1/intercom/channels/crew/messages", json={"body": "   "}).status_code == 422
