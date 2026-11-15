"""Journal narrative and day-level marking (owner: feature F4).

The day level is computed deterministically whenever the journal is generated (DEMO rules in
config/day_levels.demo.yaml). The narrative is drafted by the local LLM from that day's stored
facts, grounding-checked and labelled, with a deterministic template when no model is available.
Writing a narrative always regenerates the day first, so the text and the marker come from the
same facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from am.auth.guard import signed_in
from am.api.deps import AppContext, get_ctx, get_db
from am.api.v1.research import JournalOut
from am.db.models import Astronaut
from am.journal.levels import LEVELS, load_day_level_rules
from am.journal.narrative import day_messages, write_narrative
from am.journal.service import JournalService
from am.settings_store import effective_chat_model

router = APIRouter(tags=["journal_ext"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class NarrativeIn(BaseModel):
    use_llm: bool = True


class NarrativeRangeIn(BaseModel):
    first_day: int
    last_day: int
    use_llm: bool = True


class DayMessageOut(BaseModel):
    id: int
    conversation_id: int
    conversation_title: str
    role: str
    content: str
    created_at: datetime
    device_name: str | None
    client_host: str | None
    generated_by: str
    model: str | None


class DayLevelInfoOut(BaseModel):
    level: str
    label: str
    description: str


class DayLevelRuleOut(BaseModel):
    id: str
    level: str
    text: str
    detail: str


class DayLevelRulesOut(BaseModel):
    version: str
    label: str
    note: str
    levels: list[DayLevelInfoOut]
    rules: list[DayLevelRuleOut]


@router.get("/journal/day-level-rules", response_model=DayLevelRulesOut)
@signed_in
def day_level_rules():
    cfg = load_day_level_rules()
    return DayLevelRulesOut(
        version=cfg.version, label=cfg.label, note=cfg.note,
        levels=[DayLevelInfoOut(level=lvl, label=cfg.levels[lvl].label, description=cfg.levels[lvl].description)
                for lvl in reversed(LEVELS)],
        rules=[DayLevelRuleOut(id=r.id, level=r.level, text=r.text, detail=r.detail()) for r in cfg.rules])


@router.post("/journal/{astronaut_id}/days/{day}/narrative", response_model=JournalOut, status_code=201)
def write_day_narrative(astronaut_id: int, day: int, db: DB, ctx: CTX, body: NarrativeIn | None = None):
    use_llm = body.use_llm if body is not None else True
    try:
        entry = JournalService(db).generate(astronaut_id, day)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    model = effective_chat_model(db, ctx.settings)
    db.commit()  # review finding R03: no write transaction may be open while the model writes the narrative
    write_narrative(db, entry, llm=ctx.llm if use_llm else None, model=model)
    db.commit()
    return entry


@router.post("/journal/{astronaut_id}/narratives", response_model=list[JournalOut], status_code=201)
def write_narratives(astronaut_id: int, body: NarrativeRangeIn, db: DB, ctx: CTX):
    """Regenerate the days, then write their narratives one after another (a local model runs one at a time)."""
    if db.get(Astronaut, astronaut_id) is None:
        raise HTTPException(404, "astronaut not found")
    try:
        entries = JournalService(db).generate_range(astronaut_id, body.first_day, body.last_day)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    model = effective_chat_model(db, ctx.settings)
    for entry in entries:
        write_narrative(db, entry, llm=ctx.llm if body.use_llm else None, model=model)
        db.commit()  # keep each finished day even if a later one is interrupted
    return entries


@router.get("/journal/{astronaut_id}/days/{day}/conversations", response_model=list[DayMessageOut])
def day_conversations(astronaut_id: int, day: int, db: DB):
    """That day's user and assistant messages from all of the astronaut's conversations, oldest first."""
    try:
        _start, _today, d0, d1 = JournalService(db).day_window(astronaut_id, day)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [DayMessageOut(id=m.id, conversation_id=m.conversation_id, conversation_title=title, role=m.role,
                          content=m.content, created_at=m.created_at, device_name=m.device_name,
                          client_host=m.client_host, generated_by=m.generated_by, model=m.model)
            for m, title in day_messages(db, astronaut_id, d0, d1)]
