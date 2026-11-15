"""AM companion: conversation orchestration over a local LLM with read-only tools.

Safety properties:
  * The LLM reaches data only through read-only tools (am/ai/tools.py).
  * It cannot change alerts, baselines or memory; alert status is deterministic.
  * Every assistant message stores the tool calls and record refs it relied on.
  * A grounding check flags numbers in the answer that do not appear in the retrieved data.
  * If the model is unavailable, AM answers with a deterministic status summary instead.
  * Every user message is scanned for symptoms. A match only becomes a pending proposal that
    the astronaut confirms or dismisses; the model never records a symptom.
  * Asking what to do about a mentioned symptom gets a fixed, rule-based answer (who can advise,
    what AM records) and never reaches the model, which tends to answer with self-care advice.
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
from am.db.models_m5 import SymptomProposal
from am.policy import BaselinePolicy, ThresholdConfig
from am.settings_store import get_setting
from am.symptoms import catalog as symptom_catalog
from am.ai.safety_text import (CONTACT_NOW, NO_ADVICE_INTRO, NO_ADVICE_WHO, RECORDED, SEVERE_NOW, TO_CONFIRM,
                               detect_language, safety_reply)
from am.ai.persona import limit_questions, load_persona
from am.cabin.comfort import extract_comfort
from am.cabin.controller import handle_signal
from am.ai.tool_text import NUDGE, asks_user_to_run_tool, parse_text_tool_calls
from am.extract.service import capture
from am.ai.safety_text import label as tr_label
from am.db.models_m6b import ReportFlag
from am.symptoms.detect import Detection, asks_for_advice, asks_for_treatment, detect_keywords
from am.symptoms.service import SymptomService, symptom_label

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 12
PROMPT_ACTIVE_SYMPTOMS = 10
PROMPT_PENDING_PROPOSALS = 5

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
10. Reply in the same language the astronaut writes in.
11. Symptoms are reported and rated by the astronaut; they are not diagnoses.
    - Do not say what might cause a symptom, and do not link it to a measurement, an alert or
      another symptom (no "could be related to", "may be due to", "associated with"), unless the
      astronaut explicitly asks why; then follow rule 6.
    - Do not give self-care advice: nothing about drinking, eating, resting, sleeping,
      exercising, remedies, medication or doses.
    - A symptom AM only noticed in chat is NOT recorded. You may say that AM noticed it and ask
      the astronaut to confirm or dismiss it in the companion; only a confirmed symptom is
      recorded for the medical team and adds follow-up tasks to the daily missions.
    - For a symptom rated severe or on the DEMO report-promptly list, recommend contacting the
      crew medical officer or flight surgeon.
12. The tools are yours. Call them yourself through the tool interface. Never ask the astronaut to
    call, run or use a tool, and never show them tool or function names."""


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
    proposals: list[SymptomProposal] = field(default_factory=list)  # new, pending, from this user message


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
                 thresholds: ThresholdConfig, policy: BaselinePolicy, *, embed_model: str | None = None):
        self.s = session
        self.llm = llm
        self.model = model
        self.thresholds = thresholds
        self.policy = policy
        self.embed_model = embed_model  # None: knowledge search uses its offline keyword fallback
        self.memory = MemoryService(session)
        self.symptoms = SymptomService(session)

    # -------------------------------------------------------------- conversations
    def create_conversation(self, astronaut_id: int, *, opener: bool = False) -> Conversation:
        """opener: AM starts the check-in itself with a short, deterministic greeting (Module O)."""
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        c = Conversation(astronaut_id=astronaut_id, title="New conversation")
        self.s.add(c)
        self.s.flush()
        if opener:
            last = self.s.scalar(select(Message.content).join(Conversation, Message.conversation_id == Conversation.id)
                                 .where(Conversation.astronaut_id == astronaut_id, Message.role == "user")
                                 .order_by(Message.id.desc()).limit(1))
            lang = detect_language(last) if last else "en"
            persona = load_persona()
            self.s.add(Message(conversation_id=c.id, role="assistant", generated_by="deterministic",
                               content=persona.opener(utcnow().hour, lang, c.id),
                               refs=[{"type": "persona", "version": persona.version, "kind": "opener"}]))
            self.s.flush()
        return c

    def _system_block(self, conv: Conversation, lang: str = "en") -> str:
        a = self.s.get(Astronaut, conv.astronaut_id)
        mem = self.memory.context(conv.astronaut_id)
        lines = [SYSTEM_PROMPT, "", load_persona().prompt_section(), "", f"Current time: {utcnow():%Y-%m-%d %H:%M} UTC.",
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
        lines += self._symptom_lines(conv.astronaut_id)
        lines += ["", "LANGUAGE: the astronaut's last message is in "
                  + ("Turkish. Reply in Turkish." if lang == "tr" else "English. Reply in English.")]
        return "\n".join(x for x in lines if x is not None)

    def _symptom_lines(self, astronaut_id: int) -> list[str]:
        """Active symptom reports (facts the astronaut confirmed) and unconfirmed proposals, kept apart."""
        lines: list[str] = []
        active = self.symptoms.active(astronaut_id)
        if active:
            lines += ["", "Active symptoms reported by the astronaut (self-rated severity; not diagnoses):"]
            for r in active[:PROMPT_ACTIVE_SYMPTOMS]:
                sym = symptom_catalog.get(r.symptom_code)
                flag = " [on the DEMO report-promptly list]" if sym and sym.report_promptly else ""
                lines.append(f"- {symptom_label(r.symptom_code)}: {r.severity}, reported "
                             f"{r.reported_at:%Y-%m-%d %H:%M} UTC (symptom report {r.id}){flag}")
        pending = self.symptoms.proposals(astronaut_id, "pending")
        if pending:
            lines += ["", "Possible symptoms AM noticed in chat, NOT confirmed (the astronaut can confirm or "
                          "dismiss them in the companion):"]
            for p in pending[:PROMPT_PENDING_PROPOSALS]:
                sym = symptom_catalog.get(p.symptom_code)
                flag = " [on the DEMO report-promptly list]" if sym and sym.report_promptly else ""  # finding R06
                lines.append(f'- {symptom_label(p.symptom_code)}, from: "{p.quote[:160]}"{flag}')
        return lines

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

        # Deterministic, model-independent: proposals exist even when the model is offline.
        proposals = self.symptoms.detect_keywords(conv.astronaut_id, user_msg)
        capture(self.s, conv.astronaut_id, user_msg)  # Module L: AM's notes and stated values, with quotes
        lang = detect_language(content)
        # Module P: comfort wishes go through the deterministic cabin controller (never the model).
        comfort = [line for hit in extract_comfort(content)
                   if (line := handle_signal(self.s, conv.astronaut_id, user_msg.id, hit, lang))]
        # Commit now (review finding R01): the astronaut's words, their keyword proposals and the
        # doctor-feed entry (am.db.models_m6b) are saved before the model runs. The doctor sees the
        # message at once, and no write lock is held while a slow local model answers.
        self.s.commit()

        def with_comfort(text: str) -> str:
            return text + "".join("\n\n" + line for line in comfort)

        def fixed(text: str, refs: list[dict[str, Any]], t0: float) -> TurnResult:
            assistant = Message(conversation_id=conv.id, role="assistant", content=with_comfort(text), refs=refs,
                                generated_by="deterministic", latency_ms=int((time.perf_counter() - t0) * 1000))
            self.s.add(assistant)
            self.s.flush()
            return TurnResult(user_msg, assistant, [], True, None, proposals)

        # Safety shift (findings R04/R06, Module O): a symptom on the DEMO report-promptly list, including
        # self-harm, gets fixed contact-now words at once. The model is not asked, online or offline.
        promptly = _report_promptly(proposals)
        if promptly:
            t0 = time.perf_counter()
            awaiting = self.s.scalar(select(ReportFlag.approval_status).where(
                ReportFlag.source_type == "message", ReportFlag.source_id == user_msg.id)) == "awaiting"
            text = safety_reply(lang, [(c, symptom_label(c)) for c in promptly], awaiting)
            refs = [{"type": "symptom_proposal", "id": p.id} for p in proposals]
            refs.append({"type": "safety_rule", "rule": "report_promptly_contact_now"})
            if asks_for_advice(content) or asks_for_treatment(content):  # also answer the question, without advice
                advice, advice_refs = self._advice_reply(conv.astronaut_id, detect_keywords(content), lang)
                text, refs = text + "\n\n" + advice, refs + advice_refs
            return fixed(text, refs, t0)

        mentioned = detect_keywords(user_msg.content) if asks_for_advice(user_msg.content) else []
        treatment = asks_for_treatment(user_msg.content)
        if not mentioned and treatment:  # "what should I take for it?" (finding R02): look at the last messages
            mentioned = self._recent_detections(conv, user_msg.id)
        if mentioned or treatment:  # advice and treatment questions never reach the model
            t0 = time.perf_counter()
            text, advice_refs = self._advice_reply(conv.astronaut_id, mentioned, lang)
            return fixed(text, advice_refs, t0)
        env = ToolEnv(self.s, conv.astronaut_id, self.thresholds, self.policy,
                      llm=self.llm, embed_model=self.embed_model)
        system = self._system_block(conv, lang)
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
            known = {t.name for t in TOOLS}
            corrections = 0
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                tools = [t.schema() for t in TOOLS] if round_no < MAX_TOOL_ROUNDS else None
                reply = self.llm.chat(self.model, messages, tools=tools)
                calls = list(reply.tool_calls)
                if not calls and round_no < MAX_TOOL_ROUNDS:
                    # Small models sometimes write the call as text: AM runs it itself.
                    calls = parse_text_tool_calls(reply.content, known)
                    if not calls and asks_user_to_run_tool(reply.content):
                        # ...or ask the astronaut to "call the X tool": never. Correct the model, try again.
                        messages += [{"role": "assistant", "content": reply.content},
                                     {"role": "system", "content": NUDGE}]
                        corrections += 1
                        continue
                if not calls:
                    break
                messages.append({"role": "assistant", "content": reply.content,
                                 "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                                for c in calls]})
                for call in calls:
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
            if asks_user_to_run_tool(answer) or parse_text_tool_calls(answer, known):
                # Still asking the astronaut to run a tool: answer from stored records instead.
                text, fb_refs = self._fallback(env, "tool_misuse", proposals, lang)
                assistant = Message(conversation_id=conv.id, role="assistant", content=with_comfort(text),
                                    refs=fb_refs + [{"type": "safety_rule", "rule": "no_tool_requests_to_astronaut"}],
                                    tool_calls=calls_log, generated_by="deterministic",
                                    latency_ms=int((time.perf_counter() - t0) * 1000))
                self.s.add(assistant)
                self.s.flush()
                return TurnResult(user_msg, assistant, trace, True, None, proposals)
            answer, extra_questions = limit_questions(answer)  # persona: one open question at a time
            unverified = grounding_check(answer, sources)
            ref_list = _dedupe(refs)
            if unverified:
                ref_list.append({"type": "grounding_check", "unverified_numbers": unverified})
            from am.journal.narrative import wording_flags  # local import: narrative imports this module

            wording = wording_flags(answer)  # finding R02, second defence: shown to the reader, never hidden
            if wording:
                ref_list.append({"type": "wording_check", "flags": wording})
            if extra_questions:
                ref_list.append({"type": "persona_check", "questions_removed": extra_questions})
            if corrections:
                ref_list.append({"type": "tool_correction", "count": corrections})
            assistant = Message(conversation_id=conv.id, role="assistant", content=with_comfort(answer),
                                tool_calls=calls_log,
                                refs=ref_list, model=reply.model or self.model, generated_by="llm",
                                latency_ms=int((time.perf_counter() - t0) * 1000))
            self.s.add(assistant)
            self.s.flush()
            proposals += self._llm_proposals(conv.astronaut_id, user_msg)
            return TurnResult(user_msg, assistant, trace, True, None, proposals)
        except LLMUnavailable as exc:
            text, fb_refs = self._fallback(env, exc.reason, proposals, lang)
            assistant = Message(conversation_id=conv.id, role="assistant", content=with_comfort(text), refs=fb_refs,
                                generated_by="deterministic", latency_ms=int((time.perf_counter() - t0) * 1000))
            self.s.add(assistant)
            self.s.flush()
            return TurnResult(user_msg, assistant, trace, False, exc.reason, proposals)

    def _recent_detections(self, conv: Conversation, exclude_id: int) -> list[Detection]:
        """Symptoms named in the astronaut's last few messages ("what can I take for it?", finding R02)."""
        rows = self.s.scalars(select(Message).where(Message.conversation_id == conv.id, Message.role == "user",
                                                    Message.id != exclude_id).order_by(Message.id.desc()).limit(3))
        out: list[Detection] = []
        for m in rows:
            out += [d for d in detect_keywords(m.content) if all(d.code != o.code for o in out)]
        return out

    def _advice_reply(self, astronaut_id: int, mentioned: list[Detection],
                      lang: str = "en") -> tuple[str, list[dict[str, Any]]]:
        """Fixed answer when the astronaut asks what to do or what to take (English or Turkish).

        A 7B model slips into self-care advice when asked directly, whatever the prompt says, so
        AM states what it can do (record, follow-up missions) and who can advise. The wording comes
        from the catalog's DEMO procedures; nothing here suggests treatment. `mentioned` may be empty
        (a medication question with no symptom named): the reply is then the generic no-advice text.
        """
        active = {r.symptom_code: r for r in reversed(self.symptoms.active(astronaut_id))}  # newest wins
        lines = [NO_ADVICE_INTRO[lang], ""]
        refs: list[dict[str, Any]] = []
        for d in mentioned:
            sym = symptom_catalog.get(d.code)
            name = tr_label(d.code, sym.label if sym else d.code, lang)
            r = active.get(d.code)
            if r is not None:
                refs.append({"type": "symptom_report", "id": r.id})
                lines.append(RECORDED[lang].format(label=name, severity=r.severity))
            else:
                lines.append(TO_CONFIRM[lang].format(label=name))
            if sym is not None and sym.report_promptly:
                lines.append("- " + CONTACT_NOW[lang].format(label=name))
            elif r is not None and r.severity == "severe":
                lines.append(SEVERE_NOW[lang].format(label=name.lower() if lang == "en" else name))
        lines.append(NO_ADVICE_WHO[lang])
        refs.append({"type": "safety_rule", "rule": "no_treatment_advice"})
        return "\n".join(lines), refs

    def _llm_proposals(self, astronaut_id: int, message: Message) -> list[SymptomProposal]:
        """Optional second detector for symptoms phrased in ways the keyword stems miss.

        It runs only when the setting is on and only after the model has just answered, so an
        unavailable model never delays the turn; any failure simply yields no extra proposals.
        """
        if get_setting(self.s, "symptom_llm_detection") is not True:
            return []
        return self.symptoms.detect_llm(astronaut_id, message, self.llm, self.model)

    def _fallback(self, env: ToolEnv, reason: str, proposals: list[SymptomProposal] | None = None,
                  lang: str = "en") -> tuple[str, list[dict[str, Any]]]:
        """Short, friendly status from stored records, used when the language model cannot answer.

        The astronaut gets the gist (what needs attention, what is on record, who can see it); the
        detail (timestamps, scores, thresholds) stays on the Health page and with the doctor.
        """
        tr = lang == "tr"
        data, refs = run_tool(env, "get_health_overview", {})
        if reason == "tool_misuse":
            lines = ["Kısaca durumun:" if tr else "Here's a quick look at how you're doing:"]
        else:
            lines = ["Şu an sohbet edemiyorum (yerel yapay zekâ kapalı), ama izleme ve uyarılar çalışmaya devam ediyor."
                     if tr else
                     "I can't chat right now (the local AI is offline), but monitoring and alerts keep running."]
        alerts = data.get("open_alerts", [])
        alert_metrics = _unique(_metric_name(a["metric"], lang) for a in alerts)
        flagged = _unique(_metric_name(r["metric"], lang) for r in data.get("latest_readings", [])
                          if r.get("severity") not in (None, "NORMAL"))
        flagged = [m for m in flagged if m not in alert_metrics]
        if alert_metrics:
            n = len(alerts)
            lines.append(f"- {n} açık uyarı: {_join(alert_metrics, lang)}. Doktorun görüyor."
                         if tr else
                         f"- {n} open alert{'s' if n != 1 else ''}: {_join(alert_metrics, lang)}. Your doctor can see it.")
        if flagged:
            lines.append(f"- Son ölçümde olağan aralığının dışında: {_join(flagged, lang)}."
                         if tr else f"- Outside your usual range on the last reading: {_join(flagged, lang)}.")
        if not alert_metrics and not flagged:
            lines.append("- Şu an dikkat gerektiren bir şey yok." if tr else "- Nothing needs attention right now.")
        active = self.symptoms.active(env.astronaut_id)[:PROMPT_ACTIVE_SYMPTOMS]
        if active:
            names = _unique(_symptom_name(s.symptom_code, lang) for s in active)
            shown = names[:3] + ([f"{len(names) - 3} {'tane daha' if tr else 'more'}"] if len(names) > 3 else [])
            refs += [{"type": "symptom_report", "id": s.id} for s in active]
            lines.append(f"- Kayıtlı belirtilerin: {_join(shown, lang)}. Doktorun bunları görebiliyor."
                         if tr else
                         f"- Symptoms on record: {_join(shown, lang)}. Your doctor can see them.")
        if proposals:
            names = _join(_unique(_symptom_name(p.symptom_code, lang) for p in proposals), lang)
            lines.append(f"- Mesajında olası bir belirti fark ettim: {names}. Onaylamadan kaydetmiyorum."
                         if tr else
                         f"- I noticed a possible symptom in your message: {names}. Nothing is saved until you confirm.")
        lines.append("Detay istersen sorman yeterli." if tr else "Ask me if you want details on any of this.")
        if data.get("simulated_data"):
            lines.append("(simüle veri)" if tr else "(simulated data)")
        return "\n".join(lines), _dedupe(refs)


_METRIC_TR = {"heart_rate": "nabız", "systolic_bp": "büyük tansiyon", "diastolic_bp": "küçük tansiyon",
              "spo2": "oksijen satürasyonu", "body_temperature": "vücut ısısı", "respiratory_rate": "solunum hızı",
              "sleep_duration": "uyku süresi", "body_mass": "vücut ağırlığı",
              "hrv_rmssd": "kalp ritmi değişkenliği", "blood_glucose": "kan şekeri"}


def _metric_name(metric: str, lang: str) -> str:
    return _METRIC_TR.get(metric, metric.replace("_", " ")) if lang == "tr" else metric.replace("_", " ")


def _symptom_name(code: str, lang: str) -> str:
    return tr_label(code, symptom_label(code), lang).lower()


def _unique(items: Any) -> list[str]:
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def _join(items: list[str], lang: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (" ve " if lang == "tr" else " and ") + items[-1]


def _report_promptly(proposals: list[SymptomProposal]) -> list[str]:
    """Distinct symptom codes on the DEMO report-promptly list among this turn's proposals."""
    out: list[str] = []
    for p in proposals:
        sym = symptom_catalog.get(p.symptom_code)
        if sym is not None and sym.report_promptly and p.symptom_code not in out:
            out.append(p.symptom_code)
    return out


def _dedupe(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in refs:
        k = (r.get("type"), r.get("id"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out
