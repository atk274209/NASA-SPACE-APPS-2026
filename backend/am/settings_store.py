"""Runtime settings stored in the database (app_setting), with typed defaults and audit.

Environment/config (am.config.Settings) holds deployment facts (paths, URLs). This store
holds operator choices that can change at runtime (model selection, feature switches).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from am.audit import audit
from am.config import Settings
from am.db.models_m5 import AppSetting

DEFAULTS: dict[str, Any] = {
    "chat_model": None,              # None -> use Settings.chat_model
    "embed_model": None,             # None -> use Settings.embed_model
    "symptom_llm_detection": True,   # also ask the local LLM to spot symptoms in chat (proposals only)
    "crew_network_enabled": False,   # effective only when the server also runs with AM_CREW_NETWORK=1
    "backup_retention": 10,          # number of encrypted backups to keep
}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    if key not in DEFAULTS and default is None:
        raise KeyError(f"unknown setting '{key}'")
    row = session.get(AppSetting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_setting(session: Session, key: str, value: Any, actor: str) -> AppSetting:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting '{key}'")
    row = session.get(AppSetting, key)
    was_default = row is None
    old = DEFAULTS[key] if row is None else row.value
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=actor)
        session.add(row)
    else:
        row.value = value
        row.updated_by = actor
    audit(session, actor=actor, action="setting.changed", entity="app_setting", entity_id=key,
          payload={"old": old, "old_was_default": was_default, "new": value})
    session.flush()
    return row


def all_settings(session: Session) -> dict[str, Any]:
    return {k: get_setting(session, k) for k in DEFAULTS}


def effective_chat_model(session: Session, settings: Settings) -> str:
    return get_setting(session, "chat_model") or settings.chat_model


def effective_embed_model(session: Session, settings: Settings) -> str:
    return get_setting(session, "embed_model") or settings.embed_model
