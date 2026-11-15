"""AM Core narratives: the local model writes each sealed report from its stored facts only.

Background worker (no database transaction open during the model call). Every number in the text is
checked against the facts (grounding check) and risky wording is listed (wording check); both are kept
with the report. When the model is unavailable the deterministic template written at End Day stays.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from sqlalchemy import select

from am.ai.ollama import LLMUnavailable
from am.db.models_amcore import AmReport

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2

PROMPT = """You are AM, the onboard intelligence of a deep-space station. You keep a SEALED daily log that only
the flight surgeon can open in an emergency or after the mission returns.
Write the report for the FACTS below (JSON) in English, in AM's calm, precise voice.
Sections (use exactly these headings, one short paragraph each):
{sections}
Rules:
- Use only the FACTS. Do not add numbers, events, diagnoses, causes or treatments.
- Behavioural statements are observations from self-ratings and what the astronaut said, not clinical
  assessments. Say so when you describe them.
- State the attention level and its reasons as given. Do not upgrade or downgrade anything.
- If something is missing, say it was not recorded."""

SECTIONS = {
    "astronaut_day": ["Health", "Behaviour", "Habitat", "AM's attention"],
    "crew_day": ["Crew status", "Crew attitude", "Habitat", "Priorities for tomorrow"],
}


def write_one(llm, model: str, kind: str, facts: dict[str, Any], assessment: dict[str, Any]) -> tuple[str, dict]:
    from am.ai.companion import grounding_check
    from am.journal.narrative import wording_flags

    payload = json.dumps({"facts": facts, "assessment": assessment}, default=str, ensure_ascii=False)
    reply = llm.chat(model, [{"role": "system", "content": PROMPT.format(sections="\n".join(
                         f"## {x}" for x in SECTIONS[kind]))},
                             {"role": "user", "content": payload}], temperature=0.2, num_ctx=8192)
    text = reply.content.strip()
    checks = {"unverified_numbers": grounding_check(text, [payload]), "wording": wording_flags(text)}
    return text, checks


def process_pending(ctx: Any, limit: int = 3) -> int:
    from am.settings_store import effective_chat_model

    with ctx.session_factory() as s:
        rows = s.scalars(select(AmReport).where(AmReport.narrative_status == "pending",
                                                AmReport.narrative_attempts < MAX_ATTEMPTS)
                         .order_by(AmReport.id).limit(limit)).all()
        jobs = [(r.id, r.kind, r.facts, r.assessment) for r in rows]
        model = effective_chat_model(s, ctx.settings)
    for rid, kind, facts, assessment in jobs:
        try:
            text, checks = write_one(ctx.llm, model, kind, facts, assessment)
            err = None
        except LLMUnavailable as exc:
            text, checks, err = None, {}, exc.reason
        except Exception as exc:  # a narrative must never break the core
            log.warning("AM Core narrative failed for report %s", rid, exc_info=True)
            text, checks, err = None, {}, f"{type(exc).__name__}: {exc}"
        with ctx.session_factory() as s:
            r = s.get(AmReport, rid)
            if r is None:
                continue
            r.narrative_attempts += 1
            if text:
                r.narrative, r.narrative_status, r.narrative_model, r.checks = text, "llm", model, checks
            elif r.narrative_attempts >= MAX_ATTEMPTS:
                r.narrative_status, r.checks = "template", {"model_error": err}
            s.commit()
    return len(jobs)


class CoreWorker:
    def __init__(self, ctx: Any, interval: float = 5.0):
        self.ctx, self.interval = ctx, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="am-core-narrative", daemon=True)

    def start(self) -> CoreWorker:
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
                log.warning("AM Core worker error", exc_info=True)
                n = 0
            if n == 0:
                self._stop.wait(self.interval)
