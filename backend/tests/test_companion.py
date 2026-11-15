"""Companion tests with a scripted fake Ollama (no model needed)."""

from __future__ import annotations

import json

import httpx
import pytest

from am.ai.companion import grounding_check
from am.ai.ollama import OllamaClient


class FakeOllama:
    """Scripted /api/chat replies; records every request payload."""

    def __init__(self, replies: list[dict], models: list[str] | None = None, chat_status: int = 200):
        self.replies = list(replies)
        self.models = models if models is not None else ["llama3.1:8b"]
        self.chat_status = chat_status
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            self.requests.append(body)
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": "model not found"})
            msg = self.replies.pop(0)
            return httpx.Response(200, json={"model": body["model"], "message": msg, "total_duration": 5_000_000})
        return httpx.Response(404)

    def client(self) -> OllamaClient:
        return OllamaClient("http://ollama.test", transport=httpx.MockTransport(self.handler))


def tool_call(name: str, **args) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


@pytest.fixture
def sim(client):
    r = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "C-1"})
    assert r.status_code == 201
    return r.json()


def _conv(client, astronaut_id: int) -> int:
    r = client.post("/api/v1/companion/conversations", json={"astronaut_id": astronaut_id})
    assert r.status_code == 201
    return r.json()["id"]


def test_grounding_check_flags_only_unknown_numbers():
    src = ['{"median": 67.0, "value": 112.0, "robust_z": 12.93}']
    assert grounding_check("Median 67 bpm, latest 112 bpm, z 12.9.", src) == []
    assert grounding_check("Your heart rate was 131 bpm.", src) == [131.0]
    assert grounding_check("Rate it from 1 to 5.", src) == []  # small integers ignored


def test_tool_loop_answer_is_traceable_and_grounding_checked(client, sim):
    fake = FakeOllama([
        tool_call("get_health_overview"),
        tool_call("get_alerts", open_only=True),
        answer("Your resting heart rate alert is open. One reading reached 999 bpm."),  # 999 is invented
    ])
    client.app.state.ctx.llm = fake.client()
    cid = _conv(client, sim["astronaut"]["id"])
    r = client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "How am I doing?"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["ai_available"] is True
    assert [x["name"] for x in t["trace"]] == ["get_health_overview", "get_alerts"]
    msg = t["assistant_message"]
    assert msg["generated_by"] == "llm"
    ref_types = {ref["type"] for ref in msg["refs"]}
    assert {"alert", "measurement"} <= ref_types
    grounding = [ref for ref in msg["refs"] if ref["type"] == "grounding_check"]
    assert grounding and grounding[0]["unverified_numbers"] == [999.0]

    # the model was given tools and told the data is simulated
    first = fake.requests[0]
    assert {t["function"]["name"] for t in first["tools"]} >= {"get_health_overview", "get_alert_detail"}
    assert "SIMULATED" in first["messages"][0]["content"]
    # tool results were fed back to the model
    assert any(m["role"] == "tool" for m in fake.requests[1]["messages"])

    # history endpoint shows only user/assistant turns; conversation got a title
    hist = client.get(f"/api/v1/companion/conversations/{cid}/messages").json()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    convs = client.get("/api/v1/companion/conversations", params={"astronaut_id": sim["astronaut"]["id"]}).json()
    assert convs[0]["title"] == "How am I doing?"


def test_unknown_tool_and_bad_args_do_not_crash(client, sim):
    fake = FakeOllama([tool_call("drop_tables"), tool_call("get_alert_detail", alert_id="abc"),
                       answer("I could not find that.")])
    client.app.state.ctx.llm = fake.client()
    cid = _conv(client, sim["astronaut"]["id"])
    t = client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "x"}).json()
    assert [x["ok"] for x in t["trace"]] == [False, False]
    assert t["assistant_message"]["content"] == "I could not find that."


def test_tool_rounds_are_capped(client, sim):
    fake = FakeOllama([tool_call("get_health_overview")] * 4 + [answer("Done.")])
    client.app.state.ctx.llm = fake.client()
    cid = _conv(client, sim["astronaut"]["id"])
    t = client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "loop"}).json()
    assert len(t["trace"]) == 4
    assert "tools" not in fake.requests[-1]  # final round offers no tools


def test_unreachable_model_falls_back_to_deterministic_summary(client, sim):
    # conftest points ollama_url at a closed port
    cid = _conv(client, sim["astronaut"]["id"])
    t = client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "status?"}).json()
    assert t["ai_available"] is False
    assert t["fallback_reason"] == "service_unreachable"
    msg = t["assistant_message"]
    assert msg["generated_by"] == "deterministic"
    assert "open alert" in msg["content"] and "heart rate" in msg["content"]
    # the astronaut gets the gist: no timestamps, scores or threshold labels
    assert "UTC" not in msg["content"] and "robust z" not in msg["content"] and "Thresholds" not in msg["content"]
    assert any(r["type"] == "alert" for r in msg["refs"])


def test_missing_model_falls_back_and_status_reports_it(client, sim):
    fake = FakeOllama([], models=["other:latest"], chat_status=404)
    client.app.state.ctx.llm = fake.client()
    st = client.get("/api/v1/ai/status").json()
    assert st == {"reachable": True, "model": "llama3.1:8b", "model_installed": False,
                  "installed_models": ["other:latest"], "reason": "model_not_installed"}
    cid = _conv(client, sim["astronaut"]["id"])
    t = client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "hi"}).json()
    assert t["fallback_reason"] == "model_not_installed"


def test_mission_memory_is_written_deterministically_from_alerts(client, sim):
    mem = client.get("/api/v1/memory", params={"astronaut_id": sim["astronaut"]["id"], "layer": "mission"}).json()
    alerts = [m for m in mem if m["kind"] == "alert"]
    assert alerts, "WARNING/CRITICAL alert events must be recorded in mission memory"
    assert all(m["source"] == "deterministic" and m["is_simulated"] for m in alerts)
    assert any("CRITICAL" in m["content"] for m in alerts)
    assert all(any(r["type"] == "alert" for r in m["source_refs"]) for m in alerts)


def test_checkin_flow_feeds_short_memory_and_prompt(client, sim):
    aid = sim["astronaut"]["id"]
    items = client.get("/api/v1/checkins/items").json()
    assert {i["key"] for i in items} == {"mood", "stress", "fatigue", "sleep_quality", "motivation"}
    bad = client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"mood": 6}})
    assert bad.status_code == 422
    assert client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"hunger": 3}}).status_code == 422
    assert client.post("/api/v1/checkins", json={"astronaut_id": aid}).status_code == 422
    ok = client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"sleep_quality": 2, "stress": 4},
                                               "note": "Woke up several times."})
    assert ok.status_code == 201 and len(ok.json()) == 3
    short = client.get("/api/v1/memory", params={"astronaut_id": aid, "layer": "short"}).json()
    assert short[0]["source"] == "astronaut" and "sleep quality 2/5" in short[0]["content"]

    fake = FakeOllama([answer("Noted.")])
    client.app.state.ctx.llm = fake.client()
    cid = _conv(client, aid)
    client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": "hello"})
    system = fake.requests[0]["messages"][0]["content"]
    assert "sleep quality 2/5" in system and "Mission memory" in system


def test_suggestions_are_context_aware(client, sim):
    s = client.get("/api/v1/companion/suggestions", params={"astronaut_id": sim["astronaut"]["id"]}).json()
    assert any("heart rate alert" in x["text"] for x in s)
    assert 1 <= len(s) <= 4


def test_conversation_validation(client, sim):
    assert client.post("/api/v1/companion/conversations", json={"astronaut_id": 999}).status_code == 404
    assert client.post("/api/v1/companion/conversations/999/messages", json={"content": "x"}).status_code == 404
    cid = _conv(client, sim["astronaut"]["id"])
    assert client.post(f"/api/v1/companion/conversations/{cid}/messages", json={"content": ""}).status_code == 422


def test_compare_to_baseline_is_deterministic():
    from am.ai.tools import compare_to_baseline

    assert compare_to_baseline(129, 128, 0.2, "NORMAL") == \
        "1.00 above personal median 128.00, 1%, robust z +0.20, classified NORMAL"
    assert compare_to_baseline(80, 68, 4.1, "WARNING").startswith("12.00 above personal median 68.00, 18%")
    assert compare_to_baseline(60, 68, -3.0, None).endswith("not classified (no alert rule for this indicator)")
    assert compare_to_baseline(60, None, None, None) is None


def test_recent_measurements_mission_day_window(client, sim):
    from am.ai.tools import ToolEnv, run_tool

    ctx = client.app.state.ctx
    with ctx.session_factory() as s:
        env = ToolEnv(s, sim["astronaut"]["id"], ctx.thresholds, ctx.policy)
        day2, refs = run_tool(env, "get_recent_measurements",
                              {"metric_code": "heart_rate", "context": "sleep", "mission_day": 2})
        assert day2["window"].startswith("mission day 2 only")
        assert day2["n"] == 8 and len(day2["readings"]) == 8 and len(refs) == 8
        far, _ = run_tool(env, "get_recent_measurements",
                          {"metric_code": "heart_rate", "context": "sleep", "mission_day": 99})
        assert far["n"] == 0 and far["note"] == "no readings in this window"
        bad, _ = run_tool(env, "get_recent_measurements",
                          {"metric_code": "heart_rate", "context": "rest", "mission_day": "x"})
        assert "error" in bad


def test_stream_tools_refuse_to_guess_context_or_negative_days(client, sim):
    from am.ai.tools import ToolEnv, run_tool

    ctx = client.app.state.ctx
    with ctx.session_factory() as s:
        env = ToolEnv(s, sim["astronaut"]["id"], ctx.thresholds, ctx.policy)
        missing, _ = run_tool(env, "get_recent_measurements", {"metric_code": "heart_rate"})
        assert "context is required" in missing["error"]
        neg, _ = run_tool(env, "get_recent_measurements",
                          {"metric_code": "sleep_duration", "context": "daily", "mission_day": -3})
        assert "use the 'days' parameter" in neg["error"]
        recent, _ = run_tool(env, "get_recent_measurements",
                             {"metric_code": "sleep_duration", "context": "daily", "days": 3})
        assert recent["n"] >= 2 and "Retrieved readings span" in recent["coverage_note"]
        assert "context is required" in run_tool(env, "get_baseline", {"metric_code": "heart_rate"})[0]["error"]


def test_time_of_day_lookup_returns_nearest_readings_only(client, sim):
    from am.ai.tools import ToolEnv, run_tool

    ctx = client.app.state.ctx
    with ctx.session_factory() as s:
        env = ToolEnv(s, sim["astronaut"]["id"], ctx.thresholds, ctx.policy)
        d, refs = run_tool(env, "get_recent_measurements", {"metric_code": "heart_rate", "context": "sleep",
                                                            "mission_day": 2, "time_of_day": "03:00"})
        near = d["nearest_to_requested_time"]
        assert near["requested"].endswith("03:00 UTC")
        assert len(near["readings"]) == 2 and len(refs) == 2
        assert all(abs(r["minutes_from_requested"]) <= 60 for r in near["readings"])
        assert "readings" not in d
        bad, _ = run_tool(env, "get_recent_measurements", {"metric_code": "heart_rate", "context": "sleep",
                                                           "mission_day": 2, "time_of_day": "3am"})
        assert "error" in bad["nearest_to_requested_time"]
