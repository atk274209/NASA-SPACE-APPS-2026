"""Private diary: AM talks with the astronaut about their day, colours the day, and keeps it private.

Design and privacy rules: see am.db.models_diary. What leaves the diary is decided here, by
deterministic code only; the language model writes conversation and a day summary, nothing else.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.ollama import LLMUnavailable, OllamaClient
from am.ai.persona import limit_questions, load_persona
from am.ai.safety_text import NO_ADVICE_INTRO, NO_ADVICE_WHO, detect_language, safety_reply
from am.ai.safety_text import label as tr_label
from am.amcore.access import mission_returned_at
from am.audit import audit
from am.db.base import utcnow
from am.db.models import Astronaut
from am.db.models_diary import DiaryDay, DiaryForward, DiaryMessage
from am.db.models_m6b import ReportFlag, _needs_approval
from am.research.series import current_mission_day
from am.symptoms import catalog
from am.symptoms.detect import asks_for_advice, asks_for_treatment, detect_keywords
from am.symptoms.service import SymptomService
from am.triage.rules import text_rules

log = logging.getLogger(__name__)

LEVELS = ("none", "green", "yellow", "red")
HISTORY = 16
MAX_CHARS = 4000


def rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def day_level(d: DiaryDay) -> str:
    """The colour shown: the higher of the rule floor and a validated AI suggestion."""
    ai = d.ai_level if d.ai_status == "done" else None
    return max(d.rule_level, ai or "none", key=rank)


class DiarySealed(Exception):
    """The diary is not readable by this person yet."""


SYSTEM_PROMPT = """You are AM, the onboard companion of a long-duration space mission. Right now you are
helping one astronaut keep their PRIVATE DIARY. Nobody else reads it during the mission.

How to talk:
1. Be warm, calm and curious, like a trusted crewmate who listens well. Use their words back.
2. Ask ONE open follow-up question at a time about their day: what they did, how they felt, what was
   good, what was hard, how they slept, how things are with the crew and with home.
3. Keep replies short: one or two sentences of reflection, then your question. Always END with exactly
   one open question (not a yes/no question and not a rhetorical tag like "right?"), unless the
   astronaut is saying goodbye. Do not thank them for trusting you or talk about yourself.
4. Be motivating and supportive. When they are struggling: acknowledge the feeling in their words,
   then name a real strength or effort from what they told you, connect the day to why they are up
   here (the mission, the crew, home), and offer one small step they could choose. Speak with quiet
   confidence in them; no empty cheerfulness, no "don't worry". When the day went well, celebrate
   it by name. Mention that the crew medical officer and the behavioural health team are there
   whenever they want. Do not lecture.
5. Never diagnose, never give medical, medication or treatment advice, never judge how serious a
   symptom is. If they mention a physical symptom, say that AM can pass it to the doctor if they
   want, and continue the conversation.
6. Do not invent facts about their health or data; you have none here, only what they tell you.
7. Reply in the astronaut's language."""

ANALYSIS_PROMPT = """You read one day of an astronaut's private diary (their own words only) and classify
how the day went for them:
  green  = a normal or good day
  yellow = a difficult day: stress, low mood, conflict, poor sleep, worry, physical discomfort
  red    = serious distress: hopelessness, thoughts of self-harm, panic, an urgent medical complaint
Return JSON: {"level": "green|yellow|red", "reason": "<one sentence>", "quote": "<exact words from the
diary that support the level, copied character for character>", "summary": "<two short sentences to
the astronaut, in second person and in their language, about their day; warm, no advice>"}.
Do not diagnose."""

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"level": {"type": "string", "enum": ["green", "yellow", "red"]},
                   "reason": {"type": "string"}, "quote": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["level", "reason", "quote", "summary"],
}

OPENERS = {
    "en": ["How was your day today? Tell me whatever comes to mind first.",
           "Good to see you. What did today look like for you?",
           "Let's write today's page. How are you feeling right now?"],
    "tr": ["Bugün günün nasıl geçti? Aklına ilk ne geliyorsa anlat.",
           "Hoş geldin. Bugün neler yaptın?",
           "Bugünün sayfasını yazalım. Şu an nasıl hissediyorsun?"],
}
FOLLOW_UPS = {
    "en": ["Thank you for writing that down. What was the best moment of your day, even a small one?",
           "I hear you. What was the hardest part of today?",
           "How did you sleep last night, and how is your energy now?",
           "How are things between you and the rest of the crew today?",
           "Is there anything on your mind about home?"],
    "tr": ["Bunu yazdığın için teşekkürler. Küçük de olsa günün en güzel anı neydi?",
           "Seni anlıyorum. Bugünün en zor kısmı neydi?",
           "Dün gece nasıl uyudun, şu an enerjin nasıl?",
           "Bugün ekiple aranız nasıl?",
           "Aklında evle ilgili bir şey var mı?"],
}
SUPPORT = {
    "en": ["That sounds heavy, and it makes sense that you feel it. You have carried hard days up here before, and "
           "you are still showing up for the crew and the mission. One small thing for tonight: is there someone "
           "at home you could write to in the next window? The crew medical officer and the behavioural health "
           "team are there whenever you want them.",
           "Thank you for being honest about this; that takes strength. Every day you get through out here is part "
           "of something few people will ever do. Be kind to yourself tonight: a break, some music, or a few more "
           "lines here. And the crew medical officer and behavioural health team are one call away.",
           "Feelings like this are part of a long mission, not a sign you are failing at it. Look at what you "
           "already did today. What would help you most in the next hour? The crew medical officer and the "
           "behavioural health team are there for you too."],
    "tr": ["Bu ağır geliyor ve bunu hissetmen çok anlaşılır. Burada daha önce de zor günleri taşıdın ve hâlâ ekip "
           "ve görev için oradasın. Bu akşam için küçük bir şey: bir sonraki iletişim penceresinde evden birine "
           "yazmak ister misin? Ekip sağlık görevlisi ve davranışsal sağlık ekibi istediğin an yanında.",
           "Bunu açıkça yazdığın için teşekkürler; bu güç ister. Burada geçirdiğin her gün çok az insanın "
           "yapabileceği bir şeyin parçası. Bu akşam kendine iyi davran: bir mola, biraz müzik ya da buraya birkaç "
           "satır daha. Ekip sağlık görevlisi ve davranışsal sağlık ekibi de bir çağrı uzağında.",
           "Böyle hissetmek uzun bir görevin parçası; başaramadığın anlamına gelmiyor. Bugün zaten neler yaptığına "
           "bir bak. Önümüzdeki bir saatte sana en çok ne iyi gelir? Ekip sağlık görevlisi ve davranışsal sağlık "
           "ekibi de senin için orada."],
}
OFFER = {
    "en": "I noticed {names} in what you wrote. It stays private unless you choose to send it to your doctor: "
          "you can do that below.",
    "tr": "Yazdıklarında {names} fark ettim. Sen göndermedikçe bu gizli kalır; istersen aşağıdan doktoruna "
          "gönderebilirsin.",
}


@dataclass
class DiaryTurn:
    user: DiaryMessage
    assistant: DiaryMessage
    forwards: list[DiaryForward] = field(default_factory=list)
    ai_available: bool = True


class DiaryService:
    def __init__(self, session: Session, llm: OllamaClient | None = None, model: str | None = None):
        self.s = session
        self.llm = llm
        self.model = model

    # ------------------------------------------------------------------ pages
    def today(self, astronaut_id: int) -> DiaryDay:
        if self.s.get(Astronaut, astronaut_id) is None:
            raise LookupError("astronaut not found")
        today = utcnow().date()
        d = self.s.scalar(select(DiaryDay).where(DiaryDay.astronaut_id == astronaut_id, DiaryDay.day_date == today))
        if d is not None:
            return d
        d = DiaryDay(astronaut_id=astronaut_id, day_date=today, mission_day=current_mission_day(self.s, astronaut_id))
        self.s.add(d)
        self.s.flush()
        lang = self._last_language(astronaut_id)
        options = OPENERS[lang]
        self.s.add(DiaryMessage(day_id=d.id, role="assistant", generated_by="deterministic",
                                content=options[d.id % len(options)]))
        self.s.flush()
        return d

    def days(self, astronaut_id: int) -> list[DiaryDay]:
        return list(self.s.scalars(select(DiaryDay).where(DiaryDay.astronaut_id == astronaut_id)
                                   .order_by(DiaryDay.day_date.desc())))

    def messages(self, day_id: int) -> list[DiaryMessage]:
        return list(self.s.scalars(select(DiaryMessage).where(DiaryMessage.day_id == day_id)
                                   .order_by(DiaryMessage.id)))

    def forwards(self, day_id: int) -> list[DiaryForward]:
        return list(self.s.scalars(select(DiaryForward).join(DiaryMessage, DiaryForward.message_id == DiaryMessage.id)
                                   .where(DiaryMessage.day_id == day_id).order_by(DiaryForward.id)))

    def _last_language(self, astronaut_id: int) -> str:
        last = self.s.scalar(select(DiaryMessage.content).join(DiaryDay, DiaryMessage.day_id == DiaryDay.id)
                             .where(DiaryDay.astronaut_id == astronaut_id, DiaryMessage.role == "user")
                             .order_by(DiaryMessage.id.desc()).limit(1))
        return detect_language(last) if last else "en"

    def _user_text(self, day_id: int) -> str:
        return "\n".join(m.content for m in self.messages(day_id) if m.role == "user")

    # ------------------------------------------------------------------ writing
    def send(self, day: DiaryDay, content: str) -> DiaryTurn:
        content = content.strip()
        if not content:
            raise ValueError("message is empty")
        if day.day_date != utcnow().date():
            raise ValueError("only today's page can be written; earlier pages are read-only")
        user = DiaryMessage(day_id=day.id, role="user", content=content[:MAX_CHARS], generated_by="human")
        self.s.add(user)
        self.s.flush()
        lang = detect_language(content)
        forwards = self._medical(day.astronaut_id, user)
        self._apply_rules(day)
        self.s.commit()  # the astronaut's words and any urgent forward are saved before the model runs

        urgent = [f for f in forwards if f.status == "sent_urgent"]
        offered = [f for f in forwards if f.status == "offered"]
        if urgent:
            items = [(f.symptom_code, catalog.get(f.symptom_code).label) for f in urgent]
            text = safety_reply(lang, items, awaiting=True) + "\n\n" + SUPPORT[lang][day.id % len(SUPPORT[lang])]
            return DiaryTurn(user, self._say(day, text, "deterministic", [{"type": "safety_rule",
                                                                           "rule": "diary_report_promptly"}]),
                             forwards)
        if asks_for_advice(content) or asks_for_treatment(content):
            text = NO_ADVICE_INTRO[lang] + "\n" + NO_ADVICE_WHO[lang]
            if offered:
                text += "\n\n" + self._offer_text(offered, lang)
            return DiaryTurn(user, self._say(day, text, "deterministic", [{"type": "safety_rule",
                                                                           "rule": "no_advice"}]), forwards)
        reply, by, model, ok = self._converse(day, lang)
        struggling = any((s := catalog.get(d.code)) is not None and s.category == "behavioral"
                         for d in detect_keywords(content))
        if struggling and by == "deterministic":
            reply = SUPPORT[lang][user.id % len(SUPPORT[lang])] + "\n\n" + reply
        if offered:
            reply += "\n\n" + self._offer_text(offered, lang)
        return DiaryTurn(user, self._say(day, reply, by, [], model), forwards, ok)

    def _say(self, day: DiaryDay, text: str, by: str, refs: list[dict[str, Any]],
             model: str | None = None) -> DiaryMessage:
        m = DiaryMessage(day_id=day.id, role="assistant", content=text, generated_by=by, refs=refs, model=model)
        self.s.add(m)
        self.s.flush()
        return m

    def _offer_text(self, offered: list[DiaryForward], lang: str) -> str:
        names = ", ".join(tr_label(f.symptom_code, catalog.get(f.symptom_code).label, lang).lower() for f in offered)
        return OFFER[lang].format(names=names)

    def _converse(self, day: DiaryDay, lang: str) -> tuple[str, str, str | None, bool]:
        history = [{"role": m.role, "content": m.content} for m in self.messages(day.id)][-HISTORY:]
        system = "\n\n".join([SYSTEM_PROMPT, load_persona().prompt_section(),
                              "LANGUAGE: reply in " + ("Turkish." if lang == "tr" else "English.")])
        if self.llm is not None and self.model:
            try:
                reply = self.llm.chat(self.model, [{"role": "system", "content": system}, *history], temperature=0.6)
                text, _ = limit_questions(reply.content.strip())
                if text:
                    return text, "llm", reply.model or self.model, True
            except LLMUnavailable as exc:
                log.info("diary conversation offline: %s", exc.reason)
        asked = sum(1 for m in history if m["role"] == "assistant")
        options = FOLLOW_UPS[lang]
        return options[(asked - 1) % len(options)], "deterministic", None, False

    # ------------------------------------------------------------------ medical matters
    def _medical(self, astronaut_id: int, msg: DiaryMessage) -> list[DiaryForward]:
        """Symptoms in the message. Report-promptly ones go to the doctor now (one sentence only);
        the rest are offered to the astronaut. Negated mentions ("no headache") are skipped by the detector."""
        out: list[DiaryForward] = []
        a = self.s.get(Astronaut, astronaut_id)
        for det in detect_keywords(msg.content):
            sym = catalog.get(det.code)
            if sym is None or any(f.symptom_code == det.code for f in out):
                continue
            if sym.category == "behavioral" and not sym.report_promptly:
                continue  # feelings stay in the diary: AM supports, it does not report them

            f = DiaryForward(astronaut_id=astronaut_id, message_id=msg.id, symptom_code=det.code, quote=det.quote,
                             status="sent_urgent" if sym.report_promptly else "offered")
            self.s.add(f)
            self.s.flush()
            if sym.report_promptly:
                rules = text_rules(det.quote)
                rules.raise_to("red", "R-DIARY-PROMPTLY", f"{sym.label} written in the private diary "
                                                          "(DEMO report-promptly list).")
                now = utcnow()
                flag = ReportFlag(astronaut_id=astronaut_id, source_type="diary", source_id=f.id,
                                  text=f'From the private diary ({sym.label.lower()}): "{det.quote}"'[:2000],
                                  is_simulated=bool(a and a.is_simulated), rule_level=rules.level,
                                  rule_ids=rules.rule_ids, rule_reasons=rules.reasons, ai_status="pending",
                                  approval_status="awaiting" if _needs_approval(rules.level, False, now) else None,
                                  created_at=now)
                self.s.add(flag)
                self.s.flush()
                f.report_flag_id, f.decided_at = flag.id, now
                audit(self.s, actor=f"astronaut:{a.code if a else astronaut_id}", action="diary.forward_urgent",
                      entity="diary_forward", entity_id=f.id,
                      payload={"symptom_code": det.code, "report_flag_id": flag.id})
            out.append(f)
        return out

    def decide(self, forward: DiaryForward, send: bool, severity: str | None = None) -> DiaryForward:
        if forward.status != "offered":
            raise ValueError(f"this was already {forward.status.replace('_', ' ')}")
        if send:
            if severity not in catalog.SEVERITIES:
                raise ValueError("choose how strong it is: mild, moderate or severe")
            r = SymptomService(self.s).report(forward.astronaut_id, forward.symptom_code, severity, source="diary")
            forward.symptom_report_id, forward.status = r.id, "sent"
        else:
            forward.status = "kept_private"
        forward.decided_at = utcnow()
        audit(self.s, actor="astronaut", action=f"diary.forward_{forward.status}", entity="diary_forward",
              entity_id=forward.id, payload={"symptom_code": forward.symptom_code, "severity": severity})
        self.s.flush()
        return forward

    # ------------------------------------------------------------------ the day's colour
    def _apply_rules(self, day: DiaryDay) -> None:
        rules = text_rules(self._user_text(day.id))
        day.rule_level = {"none": "green"}.get(rules.level, rules.level)
        day.rule_reasons = rules.reasons[:10]

    def analyse(self, day: DiaryDay) -> DiaryDay:
        """Rule floor first; then the model may raise the level, with an exact quote. Never lowers it."""
        self._apply_rules(day)
        text = self._user_text(day.id)
        day.analysed_at = utcnow()
        if not text.strip():
            day.ai_status, day.summary = "none", ""
            return day
        if self.llm is None or not self.model:
            day.ai_status = "unavailable"
            return day
        try:
            reply = self.llm.chat(self.model, [{"role": "system", "content": ANALYSIS_PROMPT},
                                               {"role": "user", "content": text[:12000]}], format=ANALYSIS_SCHEMA)
            data = json.loads(reply.content)
        except LLMUnavailable:
            day.ai_status = "unavailable"
            return day
        except ValueError:
            day.ai_status, day.ai_reason = "rejected", "the model reply was not valid JSON"
            return day
        level, quote = str(data.get("level", "")), str(data.get("quote", "")).strip()
        if level not in ("green", "yellow", "red"):
            day.ai_status, day.ai_reason = "rejected", "unknown level"
            return day
        if rank(level) > rank("green") and (not quote or quote not in text):
            day.ai_status, day.ai_reason = "rejected", "the cited words are not in the diary"
            return day
        day.ai_status, day.ai_level = "done", level
        day.ai_reason, day.ai_quote = str(data.get("reason", ""))[:500], quote[:500]
        day.summary = str(data.get("summary", ""))[:800]
        return day

    # ------------------------------------------------------------------ access after landing
    def doctor_view(self, astronaut_id: int, actor: str) -> list[tuple[DiaryDay, list[DiaryMessage]]]:
        returned = mission_returned_at(self.s)
        if returned is None:
            raise DiarySealed("Diaries are private until the mission has returned to Earth.")
        audit(self.s, actor=actor, action="diary.read_after_return", entity="astronaut", entity_id=astronaut_id,
              payload={"mission_returned_at": returned.isoformat()})
        return [(d, self.messages(d.id)) for d in self.days(astronaut_id)]


def day_dict(d: DiaryDay) -> dict[str, Any]:
    return {"id": d.id, "day_date": d.day_date, "mission_day": d.mission_day, "level": day_level(d),
            "rule_level": d.rule_level, "rule_reasons": d.rule_reasons, "ai_status": d.ai_status,
            "ai_level": d.ai_level, "ai_reason": d.ai_reason, "ai_quote": d.ai_quote, "summary": d.summary,
            "analysed_at": d.analysed_at, "is_today": d.day_date == utcnow().date()}


__all__ = ["DiaryService", "DiarySealed", "day_dict", "day_level"]
