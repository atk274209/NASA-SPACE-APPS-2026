"""F2: symptom reports, chat-detected proposals and personalised missions.

No model is needed: conftest points Ollama at a closed port, and the LLM paths use the
scripted FakeOllama from test_companion.
"""

from __future__ import annotations

import json

import pytest

from am.symptoms import catalog
from am.symptoms.detect import asks_for_advice, detect_keywords, parse_llm_reply
from tests.test_companion import FakeOllama, answer, tool_call

# Words that would turn a monitoring prompt into treatment advice.
TREATMENT_WORDS = ("medication", "medicine", "laxative", "dose", "pill", "tablet", "drug", "supplement", "enema",
                   "ibuprofen", "paracetamol")


@pytest.fixture
def sim(client) -> int:
    r = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "F2-1"})
    assert r.status_code == 201
    return r.json()["astronaut"]["id"]


def _conv(client, astronaut_id: int) -> int:
    r = client.post("/api/v1/companion/conversations", json={"astronaut_id": astronaut_id})
    assert r.status_code == 201
    return r.json()["id"]


def _say(client, conversation_id: int, text: str) -> dict:
    r = client.post(f"/api/v1/companion/conversations/{conversation_id}/messages", json={"content": text})
    assert r.status_code == 201, r.text
    return r.json()


def _today(client, astronaut_id: int) -> dict[str, dict]:
    r = client.get("/api/v1/missions/today", params={"astronaut_id": astronaut_id})
    assert r.status_code == 200, r.text
    return {t["template_code"]: t for t in r.json()["tasks"]}


def _report(client, astronaut_id: int, code: str, severity: str, **kw) -> dict:
    r = client.post("/api/v1/symptoms", json={"astronaut_id": astronaut_id, "symptom_code": code,
                                              "severity": severity, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _detection_reply(*items: dict) -> dict:
    return {"role": "assistant", "content": json.dumps({"symptoms": list(items)})}


# ------------------------------------------------------------------ keyword detector
@pytest.mark.parametrize("text, codes", [
    ("I've been constipated since yesterday.", ["constipation"]),
    ("Dünden beri kabızlığım var.", ["constipation"]),
    ("kabizlik var", ["constipation"]),  # Turkish typed without diacritics
    ("Tuvalete çıkamıyorum.", ["constipation"]),
    ("BAŞIM AĞRIYOR", ["headache"]),  # Turkish capitals
    ("Kafam ağrıyor", ["headache"]),
    ("I can't sleep", ["insomnia"]),  # the negation inside the stem is the symptom itself
    ("Uyuyamıyorum", ["insomnia"]),
    ("I'm not hungry", ["loss_of_appetite"]),
    ("Kustum", ["vomiting"]),
    ("No headache, but I feel nauseous", ["nausea"]),  # a contrast word ends the negation
    ("Ateşim yok ama başım dönüyor", ["dizziness"]),
    ("My back hurts after the EVA.", ["back_pain"]),
])
def test_keyword_detector_finds_symptoms_in_english_and_turkish(text, codes):
    assert [d.code for d in detect_keywords(text)] == codes


@pytest.mark.parametrize("text", [
    "I don't have a headache",
    "I don’t have a headache.",  # typographic apostrophe
    "I have never had a migraine",
    "I didn't vomit",
    "No nausea today.",
    "başım ağrımıyor",
    "Başım ağrımadı.",
    "Kusmadım",  # kus-ma-dı-m: the negative suffix overlaps the stem's -ma
    "Midem bulanmıyor",
    "Kabız değilim",
    "Kabızlık yok",
    "kabız olmadım",
    "The computer crashed",  # "rash" must start a word
    "Yalnızca su içtim",  # "yalnızca" means "only"
    "Zamanım sınırlı",  # "sınırlı" (limited) is not "sinirli" (irritable)
])
def test_keyword_detector_respects_negation_and_word_boundaries(text):
    assert detect_keywords(text) == []


def test_advice_requests_are_recognised_in_english_and_turkish():
    assert asks_for_advice("My head hurts. What should I do?")
    assert asks_for_advice("Başım ağrıyor, NE YAPMALIYIM?")
    assert asks_for_advice("Kabızlık için hangi ilacı alayım?")
    assert not asks_for_advice("I feel nauseous since lunch.")


def test_quote_is_the_sentence_and_an_exact_substring():
    msg = "Hi AM. I don't have a headache. But I've been constipated since Tuesday! Anything to log?"
    found = detect_keywords(msg)
    assert [(d.code, d.quote) for d in found] == [("constipation", "But I've been constipated since Tuesday!")]
    run_on = "word " * 200 + "constipated " + "word " * 200
    quote = detect_keywords(run_on)[0].quote
    assert quote in run_on and "constipated" in quote and len(quote) <= 300


def test_llm_reply_keeps_only_verbatim_catalog_quotes():
    msg = "Since lunch my stomach feels off. I don't have a headache."
    reply = json.dumps({"symptoms": [
        {"code": "nausea", "quote": "my stomach feels off"},
        {"code": "fever", "quote": "I feel hot"},  # not in the message
        {"code": "made_up", "quote": "Since lunch"},  # not a catalog code
        {"code": "headache", "quote": "I don't have a headache"},  # the keyword rules see the denial
        {"code": "nausea", "quote": "Since lunch"},  # duplicate code
        {"code": "fatigue", "quote": "I"},  # too short to show what was said
    ]})
    assert [(d.code, d.quote) for d in parse_llm_reply(msg, reply)] == [("nausea", "my stomach feels off")]
    assert parse_llm_reply(msg, "not json") == []
    assert parse_llm_reply(msg, json.dumps({"symptoms": "nausea"})) == []


def test_catalog_tasks_are_prompts_not_treatment():
    for s in catalog.SYMPTOMS:
        for t in s.tasks:
            text = f"{t.title} {t.detail}".lower()
            assert not any(w in text for w in TREATMENT_WORDS), (s.code, t.code)
            assert t.protocol_ref.startswith("AM-DEMO-")


# ------------------------------------------------------------------ reports
def test_catalog_endpoint(client):
    c = client.get("/api/v1/symptoms/catalog").json()
    assert len(c["symptoms"]) == len(catalog.SYMPTOMS)
    assert c["report_promptly_label"] == catalog.REPORT_PROMPTLY_LABEL and "DEMO" in c["report_promptly_label"]
    assert c["severities"] == ["mild", "moderate", "severe"]
    chest = next(s for s in c["symptoms"] if s["code"] == "chest_pain")
    assert chest == {"code": "chest_pain", "label": "Chest pain or tightness", "category": "cardiorespiratory",
                     "body_system": "cardiovascular", "body_system_label": "Heart and circulation",
                     "report_promptly": True}


def test_manual_report_is_validated_audited_remembered_and_resolvable(client, sim):
    for body, status in [
        ({"astronaut_id": sim, "symptom_code": "unicorn_flu", "severity": "mild"}, 422),
        ({"astronaut_id": sim, "symptom_code": "headache", "severity": "terrible"}, 422),
        ({"astronaut_id": 999, "symptom_code": "headache", "severity": "mild"}, 404),
        ({"astronaut_id": sim, "symptom_code": "headache", "severity": "mild", "onset_at": "2099-01-01T00:00:00Z"}, 422),
    ]:
        assert client.post("/api/v1/symptoms", json=body).status_code == status, body

    rep = _report(client, sim, "headache", "moderate", note="Since the EVA prep.",
                  onset_at="2026-01-02T08:00:00+02:00")
    assert (rep["source"], rep["status"], rep["label"]) == ("manual", "active", "Headache")
    assert rep["onset_at"].startswith("2026-01-02T06:00")  # stored as UTC
    audit = client.get("/api/v1/audit", params={"action_prefix": "symptom."}).json()
    assert [(a["action"], a["entity_id"], a["actor"]) for a in audit] == [("symptom.reported", str(rep["id"]),
                                                                          "astronaut:F2-1")]
    short = client.get("/api/v1/memory", params={"astronaut_id": sim, "layer": "short"}).json()
    assert short[0]["source"] == "astronaut" and short[0]["kind"] == "symptom"
    assert short[0]["content"].startswith("Symptom reported: headache, self-rated moderate")
    assert {"type": "symptom_report", "id": rep["id"]} in short[0]["source_refs"]

    active = client.get("/api/v1/symptoms", params={"astronaut_id": sim, "status": "active"}).json()
    assert [x["id"] for x in active] == [rep["id"]]
    assert client.get("/api/v1/symptoms", params={"astronaut_id": sim, "status": "nope"}).status_code == 422

    res = client.post(f"/api/v1/symptoms/{rep['id']}/resolve", json={"note": "Gone after rest."})
    assert res.status_code == 200 and res.json()["status"] == "resolved" and res.json()["resolved_at"]
    assert client.post(f"/api/v1/symptoms/{rep['id']}/resolve", json={}).status_code == 409
    assert client.post("/api/v1/symptoms/99999/resolve", json={}).status_code == 404
    assert client.get("/api/v1/symptoms", params={"astronaut_id": sim, "status": "active"}).json() == []
    resolved = client.get("/api/v1/audit", params={"action_prefix": "symptom.resolved"}).json()
    assert resolved[0]["payload"]["note"] == "Gone after rest."


# ------------------------------------------------------------------ chat -> proposal -> missions
@pytest.mark.parametrize("text, quote", [
    ("Hi AM. I've been constipated since yesterday and I don't have a headache.",
     "I've been constipated since yesterday and I don't have a headache."),
    ("Merhaba. Dünden beri kabızlık var, başım ağrımıyor.", "Dünden beri kabızlık var, başım ağrımıyor."),
])
def test_constipation_mentioned_in_chat_becomes_personalised_missions_once_confirmed(client, sim, text, quote):
    cid = _conv(client, sim)
    turn = _say(client, cid, text)
    assert turn["ai_available"] is False  # no model: detection is deterministic
    props = turn["proposals"]
    assert [(p["symptom_code"], p["status"], p["detector"]) for p in props] == [("constipation", "pending", "keyword")]
    assert props[0]["quote"] == quote and quote in turn["user_message"]["content"]
    assert (props[0]["message_id"], props[0]["conversation_id"]) == (turn["user_message"]["id"], cid)
    reply = turn["assistant_message"]["content"]
    assert ("possible symptom in your message: constipation" in reply
            or "olası bir belirti fark ettim: kabızlık" in reply)

    # nothing is recorded or planned before the astronaut confirms
    assert client.get("/api/v1/symptoms", params={"astronaut_id": sim}).json() == []
    assert "bowel_log" not in _today(client, sim)
    pending = client.get("/api/v1/symptoms/proposals", params={"astronaut_id": sim}).json()
    assert [p["id"] for p in pending] == [props[0]["id"]]

    conf = client.post(f"/api/v1/symptoms/proposals/{props[0]['id']}/confirm", json={"severity": "mild"})
    assert conf.status_code == 201, conf.text
    report = conf.json()["report"]
    assert (report["source"], report["proposal_id"]) == ("chat_confirmed", props[0]["id"])
    assert conf.json()["proposal"]["status"] == "confirmed"
    assert client.post(f"/api/v1/symptoms/proposals/{props[0]['id']}/dismiss").status_code == 409
    short = client.get("/api/v1/memory", params={"astronaut_id": sim, "layer": "short"}).json()
    assert short[0]["content"] == "Symptom confirmed from chat: constipation, self-rated mild."

    tasks = _today(client, sim)
    ref = {"type": "symptom_report", "id": report["id"]}
    for code in ("bowel_log", "hydration_log", "meal_log", "symptom_followup_cmo"):
        assert ref in tasks[code]["reason_refs"], code
        assert "Symptom: constipation, self-rated mild, reported on mission day" in tasks[code]["reason"], code
    # R05: a symptom never adds, links or prioritises an action task such as exercise
    if "exercise_session" in tasks:
        assert ref not in tasks["exercise_session"]["reason_refs"]
        assert "Symptom" not in tasks["exercise_session"]["reason"]
    assert "symptom_contact_now" not in tasks  # mild, and not on the report-promptly list
    assert tasks["bowel_log"]["priority"] == 2 and tasks["symptom_followup_cmo"]["category"] == "medical"
    assert "nutrition plan" in tasks["meal_log"]["detail"]
    # the generic hydration task is reused: the symptom is appended to its reason, not duplicated
    assert tasks["hydration_log"]["reason"].startswith("Daily intake record. Symptom: constipation")
    assert tasks["hydration_log"]["priority"] == 2
    for t in tasks.values():
        text_ = f"{t['title']} {t['detail']} {t['reason']}".lower()
        assert not any(w in text_ for w in TREATMENT_WORDS), t["template_code"]
    assert len(tasks) == len(_today(client, sim))  # stable on reload


@pytest.mark.parametrize("text", ["I don't have a headache.", "Başım ağrımıyor."])
def test_denied_symptom_in_chat_is_not_proposed(client, sim, text):
    turn = _say(client, _conv(client, sim), text)
    assert turn["proposals"] == []
    assert client.get("/api/v1/symptoms/proposals", params={"astronaut_id": sim, "status": "all"}).json() == []


def test_severe_or_report_promptly_symptoms_add_contact_now_and_resolution_clears_open_tasks(client, sim):
    severe = _report(client, sim, "constipation", "severe")
    chest = _report(client, sim, "chest_pain", "mild")
    tasks = _today(client, sim)
    contact = tasks["symptom_contact_now"]
    assert contact["priority"] == 1 and contact["category"] == "medical"
    assert {"type": "symptom_report", "id": severe["id"]} in contact["reason_refs"]
    assert {"type": "symptom_report", "id": chest["id"]} in contact["reason_refs"]
    assert "chest pain or tightness, self-rated mild" in contact["reason"]
    assert "DEMO report-promptly list" in contact["reason"]
    assert tasks["bowel_log"]["priority"] == 1  # severe
    assert all(t["protocol_ref"].startswith("AM-DEMO-") for t in tasks.values())

    done = client.post(f"/api/v1/missions/tasks/{tasks['meal_log']['id']}", json={"status": "done"})
    assert done.status_code == 200
    for r in (severe, chest):
        assert client.post(f"/api/v1/symptoms/{r['id']}/resolve", json={}).status_code == 200
    after = _today(client, sim)
    assert not {"symptom_contact_now", "bowel_log", "symptom_followup_cmo"} & set(after)
    assert after["meal_log"]["status"] == "done"  # completed work stays as history
    assert after["hydration_log"]["reason_refs"] == [] and "Symptom" not in after["hydration_log"]["reason"]


def test_proposals_are_deduplicated_and_decisions_audited(client, sim):
    cid = _conv(client, sim)
    first = _say(client, cid, "So constipated. Really constipated today.")
    assert [p["symptom_code"] for p in first["proposals"]] == ["constipation"]  # once per message and code
    assert _say(client, cid, "Still constipated.")["proposals"] == []  # already pending
    client.post(f"/api/v1/symptoms/proposals/{first['proposals'][0]['id']}/confirm", json={"severity": "moderate"})
    assert _say(client, cid, "The constipation is annoying.")["proposals"] == []  # already an active report

    dizzy = _say(client, cid, "I feel dizzy.")["proposals"][0]
    assert client.post(f"/api/v1/symptoms/proposals/{dizzy['id']}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/v1/symptoms/proposals/{dizzy['id']}/confirm",
                       json={"severity": "mild"}).status_code == 409
    assert [p["symptom_code"] for p in _say(client, cid, "Dizzy again.")["proposals"]] == ["dizziness"]

    allp = client.get("/api/v1/symptoms/proposals", params={"astronaut_id": sim, "status": "all"}).json()
    assert sorted(p["status"] for p in allp) == ["confirmed", "dismissed", "pending"]
    assert client.get("/api/v1/symptoms/proposals", params={"astronaut_id": sim, "status": "x"}).status_code == 422
    assert client.post("/api/v1/symptoms/proposals/99999/dismiss").status_code == 404
    actions = [a["action"] for a in client.get("/api/v1/audit", params={"action_prefix": "symptom_proposal."}).json()]
    assert sorted(actions) == ["symptom_proposal.confirmed", "symptom_proposal.dismissed"]


# ------------------------------------------------------------------ companion with a (fake) model
def test_llm_detection_adds_only_verified_proposals(client, sim):
    msg = "I'm constipated and since lunch my stomach feels off."
    fake = FakeOllama([
        answer("Thanks for telling me."),
        _detection_reply({"code": "nausea", "quote": "my stomach feels off"},
                         {"code": "constipation", "quote": "constipated"},  # keyword detector already has it
                         {"code": "fever", "quote": "burning up"}),  # not in the message: dropped
    ])
    client.app.state.ctx.llm = fake.client()
    turn = _say(client, _conv(client, sim), msg)
    assert [(p["symptom_code"], p["detector"], p["quote"]) for p in turn["proposals"]] == [
        ("constipation", "keyword", msg), ("nausea", "llm", "my stomach feels off")]
    detect_request = fake.requests[1]
    assert "format" in detect_request and "tools" not in detect_request
    assert detect_request["messages"][-1]["content"] == msg
    system = fake.requests[0]["messages"][0]["content"]  # the reply knows the proposal is unconfirmed
    assert "NOT confirmed" in system and 'Constipation, from: "' in system
    assert client.get("/api/v1/symptoms", params={"astronaut_id": sim}).json() == []


def test_llm_detection_is_optional_and_never_breaks_the_turn(client, sim):
    from am.settings_store import set_setting

    # malformed detection output, then no detection reply at all: the turn still succeeds
    fake = FakeOllama([answer("Noted."), {"role": "assistant", "content": "not json"}, answer("Noted again.")])
    client.app.state.ctx.llm = fake.client()
    cid = _conv(client, sim)
    for text, reply in (("I feel dizzy.", "Noted."), ("Also queasy.", "Noted again.")):
        turn = _say(client, cid, text)
        assert turn["ai_available"] is True and turn["assistant_message"]["content"] == reply
        assert [p["detector"] for p in turn["proposals"]] == ["keyword"]

    with client.app.state.ctx.session_factory() as s:
        set_setting(s, "symptom_llm_detection", False, actor="test")
        s.commit()
    fake = FakeOllama([answer("Noted.")])
    client.app.state.ctx.llm = fake.client()
    assert _say(client, cid, "My stomach feels off.")["proposals"] == []
    assert len(fake.requests) == 1  # no detection call when the setting is off


def test_companion_sees_active_symptoms_and_uses_read_only_symptom_and_knowledge_tools(client, sim):
    rep = _report(client, sim, "back_pain", "moderate")
    client.app.state.ctx.llm = FakeOllama([]).client()  # no embeddings: knowledge search is lexical
    assert client.post("/api/v1/knowledge/ingest").status_code == 200
    fake = FakeOllama([
        tool_call("get_symptom_reports"),
        tool_call("search_knowledge", query="bone loss resistive exercise"),
        answer("You reported moderate back pain."),
        _detection_reply(),
    ])
    client.app.state.ctx.llm = fake.client()
    turn = _say(client, _conv(client, sim), "What symptoms do I have on record?")
    assert [t["name"] for t in turn["trace"]] == ["get_symptom_reports", "search_knowledge"]
    assert all(t["ok"] for t in turn["trace"])
    refs = turn["assistant_message"]["refs"]
    assert {"type": "symptom_report", "id": rep["id"]} in refs
    knowledge = [r for r in refs if r["type"] == "knowledge"]
    assert knowledge and all(r["doc_slug"] and r["title"] and r["url"].startswith("https://") for r in knowledge)
    system = fake.requests[0]["messages"][0]["content"]
    assert "Active symptoms reported by the astronaut" in system and "Back pain: moderate" in system
    assert "Do not give self-care advice" in system and "Do not say what might cause a symptom" in system
    offered = {t["function"]["name"] for t in fake.requests[0]["tools"]}
    assert {"get_symptom_reports", "search_knowledge"} <= offered


@pytest.mark.parametrize("text, codes, intro", [
    ("I can't sleep and my head hurts. What should I do?", ["insomnia", "headache"],
     "AM does not give treatment or self-care advice"),
    # M6c: the companion answers in the astronaut's language (master prompt Module L)
    ("Başım ağrıyor, ne yapmalıyım?", ["headache"], "AM tedavi, ilaç veya kendi kendine bakım tavsiyesi vermez"),
])
def test_asking_what_to_do_about_a_symptom_gets_a_fixed_answer_not_the_model(client, sim, text, codes, intro):
    fake = FakeOllama([])
    client.app.state.ctx.llm = fake.client()
    turn = _say(client, _conv(client, sim), text)
    assert fake.requests == []  # the question never reaches the model
    msg = turn["assistant_message"]
    assert msg["generated_by"] == "deterministic"
    assert msg["content"].startswith(intro)
    assert "crew medical officer or flight surgeon" in msg["content"] or "uçuş cerrah" in msg["content"]
    assert not any(w in msg["content"].lower() for w in TREATMENT_WORDS)
    assert {"type": "safety_rule", "rule": "no_treatment_advice"} in msg["refs"]
    assert [p["symptom_code"] for p in turn["proposals"]] == codes  # still proposed for confirmation


def test_fixed_answer_names_urgent_symptoms_and_plain_questions_still_reach_the_model(client, sim):
    headache = _report(client, sim, "headache", "severe")
    cid = _conv(client, sim)
    text = _say(client, cid, "My chest hurts and my head hurts, what should I do?")["assistant_message"]
    assert "Chest pain or tightness is on the DEMO report-promptly list: contact the crew medical officer" in \
        text["content"]
    assert "Headache is recorded (self-rated severe)" in text["content"]
    assert "You rated headache severe: contact the crew medical officer" in text["content"]
    assert {"type": "symptom_report", "id": headache["id"]} in text["refs"]

    fake = FakeOllama([answer("Your missions are listed on the missions page."), _detection_reply()])
    client.app.state.ctx.llm = fake.client()
    plain = _say(client, cid, "What should I do today?")  # advice wording but no symptom: the model answers
    assert plain["assistant_message"]["generated_by"] == "llm" and fake.requests


def test_tools_reject_bad_arguments(client, sim):
    from am.ai.tools import ToolEnv, run_tool

    ctx = client.app.state.ctx
    with ctx.session_factory() as s:
        env = ToolEnv(s, sim, ctx.thresholds, ctx.policy)
        assert "error" in run_tool(env, "get_symptom_reports", {"status": "everything"})[0]
        assert "error" in run_tool(env, "search_knowledge", {})[0]
        empty, refs = run_tool(env, "search_knowledge", {"query": "bone loss"})  # knowledge base not loaded
        assert empty["results"] == [] and refs == []
