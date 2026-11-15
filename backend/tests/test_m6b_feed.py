"""M6b: the doctor's live feed of what astronauts write, rule flags and AI triage suggestions."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am.ai.ollama import ChatReply, LLMUnavailable
from am.crew_seed import seed_crew
from am.db.models import Astronaut
from am.db.models_m6b import ReportFlag
from am.triage import ai as triage_ai
from am.triage.rules import symptom_rules, text_rules
from tests.conftest import sign_in


@pytest.fixture
def crew(client, tmp_path):
    with client.app.state.ctx.session_factory() as s:
        seed_crew(s, credentials_file=tmp_path / "c.txt", simple_passwords=True, profile="fast")
        s.commit()
        return {a.code: a.id for a in s.scalars(select(Astronaut).where(Astronaut.code.like("AST-%")))}


def astro(client, crew, code) -> TestClient:
    c = TestClient(client.app)
    sign_in(c, role="astronaut", astronaut_id=crew[code], username=code.lower().replace("-", ""))
    return c


def chat(c: TestClient, astronaut_id: int, text: str) -> dict:
    conv = c.post("/api/v1/companion/conversations", json={"astronaut_id": astronaut_id}).json()
    r = c.post(f"/api/v1/companion/conversations/{conv['id']}/messages", json={"content": text})
    assert r.status_code == 201, r.text
    return conv


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, model, messages, **kw):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatReply(content=json.dumps(r) if isinstance(r, dict) else r, model="fake")


# ------------------------------------------------------------------ deterministic rules
@pytest.mark.parametrize("text, level, rule", [
    ("Göğsümde sıkışma var", "red", "R-TEXT-PROMPTLY"),
    ("I have chest pain since lunch", "red", "R-TEXT-PROMPTLY"),
    ("Biraz başım ağrıyor", "yellow", "R-TEXT-SYMPTOM"),
    ("I want to kill myself", "red", "R-TEXT-SELFHARM"),
    ("I do not want to hurt myself", "red", "R-TEXT-SELFHARM"),  # negated, still reviewed by a person
    ("Bugün harika bir gündü, deney bitti", "none", None),
    ("I don't have a headache today", "none", None),
])
def test_text_rules(text, level, rule):
    res = text_rules(text)
    assert res.level == level
    if rule:
        assert rule in res.rule_ids and res.reasons


def test_symptom_rules_follow_the_demo_escalation_matrix():
    assert symptom_rules("headache", "mild").level == "yellow"
    assert symptom_rules("headache", "severe").level == "red"
    assert symptom_rules("chest_pain", "mild").level == "red"  # report-promptly list


# ------------------------------------------------------------------ feed entries appear at once
def test_astronaut_writing_reaches_the_doctor_feed_at_once(client, crew):
    a, b = crew["AST-001"], crew["AST-002"]
    me = astro(client, crew, "AST-001")
    conv = chat(me, a, "Göğsümde sıkışma var, nefes almak zor")
    me.post("/api/test/symptoms", json={"astronaut_id": a, "symptom_code": "headache", "severity": "mild",
                                        "note": "after the long EVA prep"})
    me.post("/api/test/checkins", json={"astronaut_id": a, "ratings": {"mood": 3}, "note": "Uyuyamadım, çok yorgunum"})
    feed = client.get("/api/v1/doctor/feed").json()
    by_type = {f["source_type"]: f for f in feed}
    assert set(by_type) == {"message", "symptom_report", "checkin_note"}
    msg = by_type["message"]
    assert msg["level"] == "red" and msg["rule_level"] == "red" and msg["astronaut_code"] == "AST-001"
    assert msg["conversation_id"] == conv["id"] and msg["ai_status"] == "pending"
    assert "Göğsümde" in msg["text"]
    assert by_type["symptom_report"]["level"] == "yellow"
    assert by_type["checkin_note"]["level"] == "yellow"
    # only the astronaut's own words: AM's replies never appear in the feed
    assert all(f["source_type"] != "message" or f["source_id"] != msg["source_id"] + 1 for f in feed)
    # polling for new entries only
    top = max(f["id"] for f in feed)
    assert client.get("/api/v1/doctor/feed", params={"after_id": top}).json() == []
    chat(astro(client, crew, "AST-002"), b, "Sırtım ağrıyor")
    new = client.get("/api/v1/doctor/feed", params={"after_id": top}).json()
    assert len(new) == 1 and new[0]["astronaut_code"] == "AST-002" and new[0]["level"] == "yellow"
    # the feed is doctor-only
    assert me.get("/api/v1/doctor/feed").status_code == 403
    assert me.get("/api/v1/doctor/crew").status_code == 403


def test_crew_summary_and_seen(client, crew):
    a = crew["AST-001"]
    chat(astro(client, crew, "AST-001"), a, "I have chest pain")
    rows = {c["code"]: c for c in client.get("/api/v1/doctor/crew").json()}
    assert rows["AST-001"]["unseen_red"] == 1 and rows["AST-001"]["level"] == "red"
    assert rows["AST-002"]["level"] == "none" and rows["AST-001"]["chronic_conditions"] == []
    assert rows["AST-002"]["chronic_conditions"] == ["Migraine"]
    item = client.get("/api/v1/doctor/feed").json()[0]
    seen = client.post(f"/api/v1/doctor/feed/{item['id']}/seen").json()
    assert seen["seen_by"] == "testdoctor"
    assert {c["code"]: c for c in client.get("/api/v1/doctor/crew").json()}["AST-001"]["unseen_red"] == 0


# ------------------------------------------------------------------ AI suggestion: raise only, quote required
def _flag(client, crew, text):
    chat(astro(client, crew, "AST-001"), crew["AST-001"], text)
    with client.app.state.ctx.session_factory() as s:
        return s.scalars(select(ReportFlag).order_by(ReportFlag.id.desc())).first().id


def _run(client, monkeypatch, replies):
    fake = FakeLLM(replies)
    monkeypatch.setattr(client.app.state.ctx, "llm", fake)
    triage_ai.process_pending(client.app.state.ctx)
    return fake


def _get(client, flag_id):
    return next(f for f in client.get("/api/v1/doctor/feed").json() if f["id"] == flag_id)


def test_ai_can_raise_the_level_with_a_valid_quote(client, crew, monkeypatch):
    fid = _flag(client, crew, "Bugün kendimi çok tuhaf hissediyorum, sanki her şey uzaklaşıyor")
    _run(client, monkeypatch, [{"level": "red", "reason": "Describes feeling detached from surroundings.",
                                "quote": "sanki her şey uzaklaşıyor"}])
    f = _get(client, fid)
    assert f["ai_status"] == "done" and f["ai_level"] == "red" and f["level"] == "red"
    assert f["rule_level"] == "none" and f["ai_quote"] == "sanki her şey uzaklaşıyor"


def test_ai_can_never_lower_a_rule_level(client, crew, monkeypatch):
    fid = _flag(client, crew, "I have chest pain")
    _run(client, monkeypatch, [{"level": "none", "reason": "Routine.", "quote": ""}])
    f = _get(client, fid)
    assert f["ai_level"] == "none" and f["rule_level"] == "red" and f["level"] == "red"


def test_ai_suggestion_without_a_real_quote_is_rejected(client, crew, monkeypatch):
    fid = _flag(client, crew, "Everything went fine today")
    _run(client, monkeypatch, [{"level": "red", "reason": "Chest pain.", "quote": "my chest hurts"}])
    f = _get(client, fid)
    assert f["ai_status"] == "rejected" and f["ai_level"] is None and f["level"] == "none"


def test_ai_unavailable_is_retried_later(client, crew, monkeypatch):
    fid = _flag(client, crew, "Bir şey yok, sadece yorgunum")
    fake = _run(client, monkeypatch, [LLMUnavailable("not_running")])
    assert fake.calls == 1 and _get(client, fid)["ai_status"] == "unavailable"
    fake = _run(client, monkeypatch, [])  # too early: not retried
    assert fake.calls == 0
    with client.app.state.ctx.session_factory() as s:
        row = s.get(ReportFlag, fid)
        row.ai_at -= timedelta(minutes=5)
        s.commit()
    _run(client, monkeypatch, [{"level": "yellow", "reason": "Reports tiredness.", "quote": "yorgunum"}])
    f = _get(client, fid)
    assert f["ai_status"] == "done" and f["level"] == "yellow"


# ------------------------------------------------------------------ R01: no write lock while the model answers
def test_message_reaches_the_doctor_while_the_model_is_still_answering(client, crew):
    """The doctor sees the message and others can write while the local model runs (finding R01)."""
    import httpx

    from am.ai.ollama import OllamaClient
    from am.symptoms.service import SymptomService

    a, b = crew["AST-001"], crew["AST-002"]
    ctx = client.app.state.ctx
    seen_during_call: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        body = json.loads(request.content)
        if body.get("format"):  # triage call, not used here
            return httpx.Response(500)
        # While "the model is thinking": another writer and the doctor's feed must not wait.
        with ctx.session_factory() as s:
            SymptomService(s).report(b, "headache", "mild")
            s.commit()
            seen_during_call["feed"] = [f.text for f in s.scalars(select(ReportFlag))]
        return httpx.Response(200, json={"model": body["model"], "total_duration": 1,
                                          "message": {"role": "assistant", "content": "Thanks, noted."}})

    ctx.llm = OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler))
    me = astro(client, crew, "AST-001")
    conv = me.post("/api/v1/companion/conversations", json={"astronaut_id": a}).json()
    r = me.post(f"/api/v1/companion/conversations/{conv['id']}/messages",
                json={"content": "Bugün sırtım çok ağrıyor"})
    assert r.status_code == 201, r.text
    assert "Bugün sırtım çok ağrıyor" in seen_during_call["feed"]
    assert any(t.startswith("Symptom report: Headache") for t in seen_during_call["feed"])


# ------------------------------------------------------------------ doctor approval
def test_rule_red_waits_for_doctor_approval(client, crew, monkeypatch):
    a = crew["AST-001"]
    me = astro(client, crew, "AST-001")
    chat(me, a, "Göğsümde sıkışma var")             # rule red  -> awaiting
    chat(me, a, "Biraz başım ağrıyor")              # rule yellow -> no approval needed
    fid = _flag(client, crew, "Sanki her şey uzaklaşıyor")  # rule none; AI will say red
    _run(client, monkeypatch, [{"level": "red", "reason": "x", "quote": "her şey uzaklaşıyor"}] * 3)
    care = me.get("/api/v1/care/status", params={"astronaut_id": a}).json()
    assert [i["text"] for i in care["awaiting"]] == ["Göğsümde sıkışma var"]  # the model never starts it
    assert care["protocol"]["procedure_id"] and "DEMO" in care["protocol_label"]
    assert astro(client, crew, "AST-002").get("/api/v1/care/status", params={"astronaut_id": a}).status_code == 403
    queue = client.get("/api/v1/doctor/approvals").json()
    assert len(queue["awaiting"]) == 1
    item = queue["awaiting"][0]
    assert me.post(f"/api/v1/doctor/feed/{item['id']}/approve", json={"note": "x"}).status_code == 403
    ok = client.post(f"/api/v1/doctor/feed/{item['id']}/approve", json={"note": "Call me on the private loop now."})
    assert ok.status_code == 200 and ok.json()["approval_status"] == "approved" and ok.json()["seen_by"]
    assert client.post(f"/api/v1/doctor/feed/{item['id']}/approve", json={}).status_code == 409
    care = me.get("/api/v1/care/status", params={"astronaut_id": a}).json()
    assert care["awaiting"] == [] and care["recently_approved"][0]["approval_note"].startswith("Call me")
    assert _get(client, fid)["approval_status"] is None


# ------------------------------------------------------------------ READ audit (Module J)
def test_doctor_reads_are_audited_once_per_window(client, crew):
    a = crew["AST-001"]
    from am.auth import guard
    from am.db.models import AuditLog

    guard._read_seen.clear()
    for _ in range(3):
        assert client.get("/api/v1/measurements", params={"astronaut_id": a}).status_code == 200
    client.get("/api/v1/doctor/feed")
    astro(client, crew, "AST-001").get("/api/v1/measurements", params={"astronaut_id": a})  # not a doctor read
    with client.app.state.ctx.session_factory() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "doctor.read")).all()
    per_astronaut = [r for r in rows if r.entity == "astronaut" and r.entity_id == str(a)]
    assert len(per_astronaut) == 1 and per_astronaut[0].payload["route"].endswith("/measurements")
    assert any(r.entity == "crew" and r.payload["route"].endswith("/doctor/feed") for r in rows)
    assert all(r.actor == "testdoctor" for r in rows)
