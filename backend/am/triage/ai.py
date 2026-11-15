"""AI triage suggestion for the doctor's live feed (yellow / red), by the local model.

Safety design (master prompt Engineering Rule 8, approved by the user on 2026-11-14):
  * The deterministic rule level (am.triage.rules) is the floor. The model's suggestion can only
    raise the level the doctor sees; it can never lower it, and it never starts an escalation or
    the doctor-approval procedure.
  * The model must cite an exact quote from what the astronaut wrote. A suggestion whose quote is
    not in the text, or that uses an unknown level, is rejected and shown as such.
  * No diagnosis, no treatment: the model only says how soon a person should look at the text.
  * The model call runs with no database transaction open (review finding R01), so a slow model
    never blocks other writes.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from am.ai.ollama import LLMUnavailable, OllamaClient
from am.db.base import utcnow
from am.db.models_m6b import LEVELS, ReportFlag

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(seconds=60)  # when the model was unavailable
BATCH = 5
POLL_SECONDS = 2.0

SYSTEM_PROMPT = """You help a flight surgeon on Earth decide how soon to read notes written by astronauts.
You do NOT diagnose, you do NOT suggest treatment, and you do NOT decide emergencies.
Read the astronaut's text (English or Turkish) and answer with JSON only:
  level: "red"    = a person should look now (possible chest pain, breathing trouble, fainting,
                    severe or sudden pain, confusion, vision change, bleeding, thoughts of self-harm)
         "yellow" = the doctor should read it today (any other symptom, discomfort, low mood,
                    poor sleep, worry, conflict)
         "none"   = routine, no health concern
  reason: one short English sentence saying what in the text led to the level (no diagnosis)
  quote:  the exact words from the text that support the level, copied character for character;
          empty only when level is "none"
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": list(LEVELS)},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["level", "reason", "quote"],
}


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


def validate(text: str, raw: str) -> tuple[str, str, str] | str:
    """Return (level, reason, quote) or an error string explaining the rejection."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "the reply was not valid JSON"
    if not isinstance(data, dict):
        return "the reply was not a JSON object"
    level, reason, quote = data.get("level"), str(data.get("reason") or "").strip(), str(data.get("quote") or "").strip()
    if level not in LEVELS:
        return f"unknown level {level!r}"
    if level != "none":
        if not quote:
            return "no supporting quote"
        if _norm(quote) not in _norm(text):
            return "the quote is not in the astronaut's text"
    return level, reason[:400], quote[:400]


def triage_one(llm: OllamaClient, model: str, text: str) -> tuple[str, str, str] | str:
    """Ask the model; raises LLMUnavailable when the model cannot be reached."""
    reply = llm.chat(model, [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": f"Astronaut's text:\n<<<\n{text}\n>>>"}],
                     format=SCHEMA, temperature=0.0, num_ctx=4096)
    return validate(text, reply.content)


def process_pending(ctx: Any, limit: int = BATCH) -> int:
    """Triage up to `limit` waiting flags. Returns how many were processed."""
    from am.settings_store import effective_chat_model

    with ctx.session_factory() as s:
        rows = s.scalars(select(ReportFlag).where(
            or_(ReportFlag.ai_status == "pending",
                (ReportFlag.ai_status == "unavailable") & (ReportFlag.ai_attempts < MAX_ATTEMPTS)
                & (ReportFlag.ai_at < utcnow() - RETRY_AFTER)))
            .order_by(ReportFlag.id.desc()).limit(limit)).all()
        jobs = [(r.id, r.text) for r in rows]
        model = effective_chat_model(s, ctx.settings)
    done = 0
    for flag_id, text in jobs:
        # The model call happens with no session open.
        try:
            result: tuple[str, str, str] | str | None = triage_one(ctx.llm, model, text)
            unavailable = None
        except LLMUnavailable as exc:
            result, unavailable = None, exc.reason
        except Exception as exc:  # a suggestion must never break the feed
            log.warning("AI triage failed for flag %s", flag_id, exc_info=True)
            result, unavailable = f"{type(exc).__name__}: {exc}", None
        with ctx.session_factory() as s:
            row = s.get(ReportFlag, flag_id)
            if row is None:
                continue
            row.ai_attempts += 1
            row.ai_at = utcnow()
            row.ai_model = model
            if unavailable is not None:
                row.ai_status = "unavailable"
                row.ai_detail = {"reason": unavailable}
            elif isinstance(result, str):
                row.ai_status, row.ai_level = "rejected", None
                row.ai_detail = {"rejected": result}
            else:
                row.ai_status = "done"
                row.ai_level, row.ai_reason, row.ai_quote = result
                row.ai_detail = {}
            s.commit()
        done += 1
    return done


class TriageWorker:
    """Background thread that triages new feed entries every few seconds."""

    def __init__(self, ctx: Any, interval: float = POLL_SECONDS):
        self.ctx = ctx
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="am-ai-triage", daemon=True)

    def start(self) -> TriageWorker:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                n = process_pending(self.ctx)
            except Exception:
                log.warning("AI triage loop error", exc_info=True)
                n = 0
            if n == 0:
                self._stop.wait(self.interval)
