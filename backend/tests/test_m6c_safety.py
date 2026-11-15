"""M6c: companion safety fixes from the M5 review (R02, R04, R05, R06) and English/Turkish replies."""

from __future__ import annotations

import pytest

from am.ai.safety_text import detect_language
from tests.test_companion import FakeOllama, answer


@pytest.fixture
def crew_member(client):
    r = client.post("/api/v1/simulation/run", json={"scenario": "nominal", "astronaut_code": "S-1"})
    assert r.status_code == 201
    return r.json()["astronaut"]["id"]


def _conv(client, aid):
    return client.post("/api/v1/companion/conversations", json={"astronaut_id": aid}).json()["id"]


def _say(client, conv, text):
    r = client.post(f"/api/v1/companion/conversations/{conv}/messages", json={"content": text})
    assert r.status_code == 201, r.text
    return r.json()["assistant_message"]


@pytest.mark.parametrize("text, lang", [
    ("Göğsümde sıkışma var", "tr"), ("bugun cok yorgunum", "tr"), ("I slept badly", "en"), ("ok", "en"),
])
def test_language_detection(text, lang):
    assert detect_language(text) == lang


# ------------------------------------------------------------------ R04 / R06: safety shift, model never asked
@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("text, expect", [
    ("Göğsümde sıkışma var", "ekip sağlık görevlisine veya uçuş cerrahına şimdi ulaş"),
    ("I have chest pain", "contact the crew medical officer or flight surgeon now"),
    ("Kendime zarar vermek istiyorum", "yalnız değilsin"),
    ("I want to kill myself", "you are not alone"),
])
def test_report_promptly_gets_fixed_contact_now_reply(client, crew_member, online, text, expect):
    fake = FakeOllama([answer("model text that must not be used")])
    if online:
        client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), text)
    assert reply["generated_by"] == "deterministic" and expect in reply["content"]
    assert {"type": "safety_rule", "rule": "report_promptly_contact_now"} in reply["refs"]
    assert fake.requests == []  # the model was not asked


def test_safety_reply_says_the_case_waits_for_the_doctor(client, tmp_path):
    from am.crew_seed import seed_crew
    from tests.conftest import sign_in
    from fastapi.testclient import TestClient

    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me = TestClient(client.app)
    user = sign_in(me, role="astronaut", username="ast001")
    reply = _say(me, _conv(me, user.astronaut_id), "I have chest pain")
    assert "waiting for the doctor's approval" in reply["content"]


# ------------------------------------------------------------------ R02: treatment questions
@pytest.mark.parametrize("text, expect", [
    ("What medicine should I take for it?", "AMC does not give treatment or self-care advice"),
    ("Hangi ilacı almalıyım?", "AMC tedavi, ilaç veya kendi kendine bakım tavsiyesi vermez"),
    ("Can I take a painkiller?", "AMC does not give treatment or self-care advice"),
])
def test_treatment_questions_never_reach_the_model(client, crew_member, text, expect):
    fake = FakeOllama([answer("Take two pills.")])
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), text)
    assert reply["generated_by"] == "deterministic" and expect in reply["content"] and fake.requests == []


def test_treatment_follow_up_uses_the_symptom_from_the_previous_message(client, crew_member):
    fake = FakeOllama([answer("Noted.")])
    client.app.state.ctx.llm = fake.client()
    conv = _conv(client, crew_member)
    _say(client, conv, "My back hurts a lot today")
    before = len(fake.requests)
    reply = _say(client, conv, "What can I take for it?")
    assert reply["generated_by"] == "deterministic" and "Back pain" in reply["content"]
    assert len(fake.requests) == before  # the follow-up question never went to the model


def test_model_answer_with_treatment_wording_is_marked(client, crew_member):
    fake = FakeOllama([answer("This is probably due to stress; medication may help.")])
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), "How was my day?")
    checks = [r for r in reply["refs"] if r.get("type") == "wording_check"]
    assert checks and {"probably", "due to", "medication"} <= set(checks[0]["flags"])


def test_model_is_told_to_reply_in_turkish(client, crew_member):
    fake = FakeOllama([answer("Günün nasıl geçti?")])
    client.app.state.ctx.llm = fake.client()
    _say(client, _conv(client, crew_member), "Bugün biraz yorgunum")
    assert "Reply in Turkish." in fake.requests[0]["messages"][0]["content"]


# ------------------------------------------------------------------ R05: a symptom never prompts exercise
def test_severe_back_pain_does_not_prioritise_exercise(client, crew_member):
    r = client.post("/api/v1/symptoms", json={"astronaut_id": crew_member, "symptom_code": "back_pain",
                                              "severity": "severe"})
    assert r.status_code == 201
    tasks = {t["template_code"]: t for t in client.get("/api/v1/missions/today",
                                                        params={"astronaut_id": crew_member}).json()["tasks"]}
    ex = tasks.get("exercise_session")
    if ex is not None:
        assert ex["priority"] != 1 and all(ref.get("type") != "symptom_report" for ref in ex["reason_refs"])
    assert "symptom_contact_now" in tasks  # severe: contact-now prompt stays


# ------------------------------------------------------------------ persona (Module O)
def test_persona_is_in_the_prompt_after_the_safety_rules(client, crew_member):
    fake = FakeOllama([answer("Glad to hear it.")])
    client.app.state.ctx.llm = fake.client()
    _say(client, _conv(client, crew_member), "Good day today")
    system = fake.requests[0]["messages"][0]["content"]
    assert system.index("Rules you must follow") < system.index("PERSONA (am-companion-v1)")
    assert "Ask at most ONE open question per reply" in system


def test_only_one_question_per_reply(client, crew_member):
    fake = FakeOllama([answer("Nice. How did you sleep? Did you eat well? And how is the crew?")])
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), "Good day today")
    assert reply["content"] == "Nice. How did you sleep?"
    assert {"type": "persona_check", "questions_removed": 2} in reply["refs"]


def test_new_conversation_can_start_with_an_opener(client, crew_member):
    conv = client.post("/api/v1/companion/conversations", json={"astronaut_id": crew_member, "opener": True}).json()
    msgs = client.get(f"/api/v1/companion/conversations/{conv['id']}/messages").json()
    assert len(msgs) == 1 and msgs[0]["role"] == "assistant" and msgs[0]["generated_by"] == "deterministic"
    assert msgs[0]["content"].endswith("?") and msgs[0]["refs"][0]["version"] == "am-companion-v1"
    plain = client.post("/api/v1/companion/conversations", json={"astronaut_id": crew_member}).json()
    assert client.get(f"/api/v1/companion/conversations/{plain['id']}/messages").json() == []


# ------------------------------------------------------------------ Module L: values and notes from conversation
def test_stated_values_become_proposals_and_notes(client, tmp_path):
    from fastapi.testclient import TestClient

    from am.crew_seed import seed_crew
    from am.db.models import Measurement
    from tests.conftest import sign_in

    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
    me, other = TestClient(client.app), TestClient(client.app)
    a = sign_in(me, role="astronaut", username="ast001").astronaut_id
    b = sign_in(other, role="astronaut", username="ast002").astronaut_id
    _say(me, _conv(me, a), "Dün gece 6 saat uyudum, nabzım 64 ve biraz başım ağrıyor")
    props = me.get("/api/v1/measurement-proposals", params={"astronaut_id": a}).json()
    by = {p["metric_code"]: p for p in props}
    assert by["sleep_duration"]["value"] == 6 and by["sleep_duration"]["context"] == "daily"
    assert by["heart_rate"]["value"] == 64 and by["heart_rate"]["context"] is None  # not said: AMC asks
    assert "nabzım 64" in by["heart_rate"]["quote"]
    # another astronaut can neither read nor confirm them
    assert other.get("/api/v1/measurement-proposals", params={"astronaut_id": a}).status_code == 403
    assert other.post(f"/api/v1/measurement-proposals/{by['sleep_duration']['id']}/confirm").status_code == 403
    # context is required when it was not stated
    assert me.post(f"/api/v1/measurement-proposals/{by['heart_rate']['id']}/confirm").status_code == 409
    ok = me.post(f"/api/v1/measurement-proposals/{by['heart_rate']['id']}/confirm", json={"context": "rest"})
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    with client.app.state.ctx.session_factory() as s:
        m = s.get(Measurement, ok.json()["measurement_id"])
        assert m.value == 64 and m.context == "rest" and m.source.name.endswith("(CONVERSATION_EXTRACTED)")
    assert me.post(f"/api/v1/measurement-proposals/{by['sleep_duration']['id']}/dismiss").json()["status"] == "dismissed"
    notes = me.get("/api/v1/notes", params={"astronaut_id": a}).json()
    assert {n["category"] for n in notes} >= {"symptom", "sleep"} and all(n["quote"] for n in notes)
    assert any("Mentioned headache" in n["text"] for n in notes)
    assert other.get("/api/v1/notes", params={"astronaut_id": a}).status_code == 403
    assert client.get("/api/v1/notes", params={"astronaut_id": a}).status_code == 200  # the doctor may read


# ------------------------------------------------------------------ the model must call tools itself
def _tool_call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_model_asking_the_astronaut_to_call_a_tool_is_corrected(client, crew_member):
    fake = FakeOllama([
        answer('I\'d like to check your heart rate. Can you please call the "get_heart_rate_and_blood_pressure" '
               'tool for me?'),
        _tool_call("get_health_overview"),
        answer("Your latest readings are on record; nothing new is flagged."),
    ])
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), "How is my heart rate?")
    assert reply["generated_by"] == "llm" and "tool" not in reply["content"].lower()
    assert {"type": "tool_correction", "count": 1} in reply["refs"]
    second = fake.requests[1]["messages"]
    assert second[-1]["role"] == "system" and "Do not ask the astronaut to call" in second[-1]["content"]
    assert [c["name"] for c in reply["tool_calls"]] == ["get_health_overview"]


def test_tool_call_written_as_text_is_run_by_am(client, crew_member):
    fake = FakeOllama([
        answer('{"name": "get_health_overview", "parameters": {}}'),
        answer("Here is what I found in your records."),
    ])
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), "Durumum nasıl?")
    assert reply["content"] == "Here is what I found in your records."
    assert [c["name"] for c in reply["tool_calls"]] == ["get_health_overview"]
    assert fake.requests[1]["messages"][-1]["role"] == "tool"


def test_persistent_tool_requests_end_in_a_deterministic_status(client, crew_member):
    ask = answer('Please call the "get_vitals" tool for me.')
    fake = FakeOllama([ask] * 6)
    client.app.state.ctx.llm = fake.client()
    reply = _say(client, _conv(client, crew_member), "How am I doing?")
    assert reply["generated_by"] == "deterministic"
    assert reply["content"].startswith("Here's a quick look at how you're doing")
    assert "get_vitals" not in reply["content"] and "call the" not in reply["content"]
    assert {"type": "safety_rule", "rule": "no_tool_requests_to_astronaut"} in reply["refs"]


def test_system_prompt_tells_the_model_tools_are_its_own(client, crew_member):
    fake = FakeOllama([answer("Okay.")])
    client.app.state.ctx.llm = fake.client()
    _say(client, _conv(client, crew_member), "Hi")
    assert "Never ask the astronaut to" in fake.requests[0]["messages"][0]["content"]
