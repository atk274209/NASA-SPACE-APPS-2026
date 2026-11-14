"""AM companion: conversation orchestration over a local LLM with read-only tools.

Safety properties:
  * The LLM reaches data only through read-only tools (am/ai/tools.py).
  * It cannot change alerts, baselines or memory; alert status is deterministic.
  * Every assistant message stores the tool calls and record refs it relied on.
  * A grounding check flags numbers in the answer that do not appear in the retrieved data.
  * If the model is unavailable, AM answers with a deterministic status summary instead.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.memory import MemoryService
from am.ai.ollama import LLMUnavailable, OllamaClient
from am.ai.tools import TOOLS, ToolEnv, run_tool
from am.db.base import utcnow
from am.db.models import Astronaut
from am.db.models_ai import Conversation, Message
from am.policy import BaselinePolicy, ThresholdConfig

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 12

SYSTEM_PROMPT = """You are AM, the onboard health companion of a long-duration space mission.
You talk with one astronaut about their own health data.

Rules you must follow:
1. Facts about the astronaut's health come ONLY from tool results or the memory notes below.
   Call tools before answering anything about measurements, baselines, alerts or check-ins.
   Never invent, estimate or round-trip numbers that are not in the tool results.
2. If the data needed is not available, say so plainly. Only state a value for a time or day
   that appears in the tool result; if the result covers a different window, say that you did
   not retrieve data for the requested time (and fetch it with mission_day if possible).
3. Alert severities come from a deterministic engine using DEMONSTRATION thresholds that are
   not clinically validated. Report them as given; never upgrade, downgrade or dismiss them.
   peak_severity is the highest level the alert has reached; current_severity is the level of
   the most recent deviating reading. Use each alert's "summary" field to describe its state.
4. Do not diagnose, do not prescribe or change medication or treatment. For anything that may
   need medical judgement, recommend contacting the flight surgeon / medical team.
5. Do not do arithmetic or compare numbers yourself. Use the pre-computed "comparison",
   "robust_z" and "severity" fields exactly as given ("above"/"below" included).
6. Do not offer physiological mechanisms, causes or links between indicators on your own
   (for example, do not say one indicator "might be contributing" to another). Only if the
   astronaut explicitly asks why, you may name POSSIBLE factors visible in the data, labelled as
   possibilities for the medical team to assess. Never present correlation as causation.
7. Never reassure or judge how serious something is ("not serious", "nothing to worry about",
   "concerning"). State the classification and, for WARNING or CRITICAL, recommend contacting
   the flight surgeon / medical team.
8. If the data is marked simulated, mention that it is simulated data.
9. Be concise and calm: short paragraphs or bullets, plain language, numbers with units.
   Answer the question asked; do not list every indicator unless asked.
10. Reply in the same language the astronaut writes in."""


@dataclass
class ToolTraceItem:
    name: str
    arguments: dict[str, Any]
    ok: bool
    ref_count: int


@dataclass
class TurnResult:
    user_message: Message
    assistant_message: Message
    trace: list[ToolTraceItem] = field(default_factory=list)
    ai_available: bool = True
    fallback_reason: str | None = None


_NUM = re.compile(r"(?<![\w.])[-+−]?\d+(?:[.,]\d+)?")


def _numbers_in(text: str) -> set[float]:
    out = set()
    for tok in _NUM.findall(text):
        try:
            out.add(float(tok.replace("−", "-").replace(",", ".")))
        except ValueError:
            pass
    return out


def grounding_check(answer: str, sources: list[str]) -> list[float]:
    """Numbers in the answer that cannot be found (allowing rounding) in any source text.

    Small integers 0..10 are ignored (list numbering, scale anchors, '1 to 5').
    """
    known: set[float] = set()
    for s in sources:
        for v in _numbers_in(s):
            known.update({v, abs(v), round(v), round(v, 1), round(abs(v)), round(abs(v), 1)})
    unverified = []
    for v in sorted(_numbers_in(answer)):
        if abs(v) <= 10 and float(v).is_integer():
            continue
        if v in known or abs(v) in known or round(v, 1) in known or round(v) in known:
            continue
        unverified.append(v)
    return unverified


class CompanionService:
    def __init__(self, session: Session, llm: OllamaClient, model: str,
                 thresholds: ThresholdConfig, policy: BaselinePolicy):
        self.s = session
        self.llm = llm
        self.model = model
        self.thresholds = thresholds
        self.policy = policy
        self.memory = MemoryService(session)

    # -------------------------------------------------------------- conversations
    def create_conversation(self, astronaut_id: int) -> Conversation:
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        c = Conversation(astronaut_id=astronaut_id, title="New conversation")
        self.s.add(c)
        self.s.flush()
        return c

    def _system_block(self, conv: Conversation) -> str:
        a = self.s.get(Astronaut, conv.astronaut_id)
        mem = self.memory.context(conv.astronaut_id)
        lines = [SYSTEM_PROMPT, "", f"Current time: {utcnow():%Y-%m-%d %H:%M} UTC.",
                 f"Astronaut: {a.display_name} (code {a.code}).",
                 "DATA NOTICE: this astronaut's records are SIMULATED demonstration data." if a.is_simulated else "",
                 f"Alert thresholds: {self.thresholds.label} (version {self.thresholds.version})."]
        if mem.mission:
            lines += ["", "Mission memory (significant events, deterministic):"]
            lines += [f"- [{m.occurred_at:%Y-%m-%d %H:%M}] {m.content}" for m in mem.mission]
        if mem.long:
            lines += ["", "Long-term notes:"] + [f"- {m.content}" for m in mem.long]
        if mem.short:
            lines += ["", "Today's notes from the astronaut (self-reported):"]
            lines += [f"- [{m.occurred_at:%H:%M}] {m.content}" for m in mem.short]
        return "\n".join(x for x in lines if x is not None)

    def _history(self, conv: Conversation, exclude_id: int) -> list[dict[str, Any]]:
        rows = self.s.scalars(select(Message).where(
            Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")), Message.id != exclude_id)
            .order_by(Message.id.desc()).limit(HISTORY_MESSAGES)).all()
        return [{"role": m.role, "content": m.content} for m in reversed(rows)]

    def send(self, conversation_id: int, content: str) -> TurnResult:
        conv = self.s.get(Conversation, conversation_id)
        if conv is None:
            raise LookupError("conversation not found")
        content = content.strip()
        if not content:
            raise ValueError("message is empty")
        user_msg = Message(conversation_id=conv.id, role="user", content=content[:4000], generated_by="human")
        self.s.add(user_msg)
        self.s.flush()
        if conv.title == "New conversation":
            conv.title = (content[:60] + "…") if len(content) > 60 else content

        env = ToolEnv(self.s, conv.astronaut_id, self.thresholds, self.policy)
        system = self._system_block(conv)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                          *self._history(conv, user_msg.id),
                                          {"role": "user", "content": content}]
        trace: list[ToolTraceItem] = []
        refs: list[dict[str, Any]] = []
        sources: list[str] = [system, content]
        calls_log: list[dict[str, Any]] = []
        t0 = time.perf_counter()
        try:
            reply = None
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                tools = [t.schema() for t in TOOLS] if round_no < MAX_TOOL_ROUNDS else None
                reply = self.llm.chat(self.model, messages, tools=tools)
                if not reply.tool_calls:
                    break
                messages.append({"role": "assistant", "content": reply.content,
                                 "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                                for c in reply.tool_calls]})
                for call in reply.tool_calls:
                    data, call_refs = run_tool(env, call["name"], call["arguments"])
                    payload = json.dumps(data, default=str)
                    sources.append(payload)
                    refs.extend(call_refs)
                    ok = "error" not in data
                    trace.append(ToolTraceItem(call["name"], call["arguments"], ok, len(call_refs)))
                    calls_log.append({"name": call["name"], "arguments": call["arguments"], "ok": ok})
                    messages.append({"role": "tool", "content": payload, "tool_name": call["name"]})
                    self.s.add(Message(conversation_id=conv.id, role="tool", tool_name=call["name"],
                                       content=payload[:20000], refs=call_refs, generated_by="deterministic"))
            assert reply is not None
            answer = reply.content.strip() or "I could not produce an answer from the available data."
            unverified = grounding_check(answer, sources)
            ref_list = _dedupe(refs)
            if unverified:
                ref_list.append({"type": "grounding_check", "unverified_numbers": unverified})
            assistant = Message(conversation_id=conv.id, role="assistant", content=answer, tool_calls=calls_log,
                                refs=ref_list, model=reply.model or self.model, generated_by="llm",
                                latency_ms=int((time.perf_counter() - t0) * 1000))
            self.s.add(assistant)
            self.s.flush()
            return TurnResult(user_msg, assistant, trace, True, None)
        except LLMUnavailable as exc:
            text, fb_refs = self._fallback(env, exc.reason)
            assistant = Message(conversation_id=conv.id, role="assistant", content=text, refs=fb_refs,
                                generated_by="deterministic", latency_ms=int((time.perf_counter() - t0) * 1000))
            self.s.add(assistant)
            self.s.flush()
            return TurnResult(user_msg, assistant, trace, False, exc.reason)

    def _fallback(self, env: ToolEnv, reason: str) -> tuple[str, list[dict[str, Any]]]:
        """Deterministic status summary used when the language model is unavailable."""
        data, refs = run_tool(env, "get_health_overview", {})
        why = {"service_unreachable": "the local language model service is not running",
               "model_not_installed": f"the model '{self.model}' is not installed"}.get(reason, reason)
        lines = [f"AI conversation is unavailable because {why}. Monitoring and alerts are unaffected.",
                 "", "Current status from stored records:"]
        if data.get("simulated_data"):
            lines.append("(simulated data)")
        alerts = data.get("open_alerts", [])
        if alerts:
            for a in alerts:
                lines.append(f"- Open alert: {a['metric'].replace('_', ' ')} ({a['context']}), "
                             f"{a['current_severity']} now, peak {a['peak_severity']}, status {a['status']}.")
        else:
            lines.append("- No open alerts.")
        flagged = [r for r in data.get("latest_readings", []) if r.get("severity") not in (None, "NORMAL")]
        for r in flagged:
            lines.append(f"- Latest {r['metric'].replace('_', ' ')} ({r['context']}): {r['value']} {r['unit']}, "
                         f"{r['severity']} (robust z {r['robust_z']}).")
        lines.append(f"Thresholds: {data.get('threshold_label')}.")
        return "\n".join(lines), _dedupe(refs)


def _dedupe(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in refs:
        k = (r.get("type"), r.get("id"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out
