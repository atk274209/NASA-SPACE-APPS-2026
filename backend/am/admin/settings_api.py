"""Typed descriptions and validation for runtime settings (GET/PUT /settings).

am.settings_store owns storage, defaults and the audit entry. This module adds what an
operator needs to change a setting safely: a description, a type, bounds, and for model
settings a check against the models the local Ollama service actually has installed.
A model AMC cannot confirm is never saved, so the companion cannot silently switch to a
model that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from am.ai.ollama import OllamaClient
from am.config import Settings
from am.db.models_m5 import AppSetting
from am.settings_store import DEFAULTS, effective_chat_model, effective_embed_model, get_setting, set_setting

Kind = Literal["model", "bool", "int", "text"]

# Model names are stored next to every AI output in 64-character columns (message.model,
# research_observation.model, journal_entry.narrative_model).
MODEL_NAME_MAX = 64


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: Kind
    description: str
    min_value: int | None = None
    max_value: int | None = None


SPECS: dict[str, SettingSpec] = {s.key: s for s in [
    SettingSpec("chat_model", "model",
                "Local Ollama model for the companion, journal narratives and research hypotheses. Empty means "
                "the deployment default (AM_CHAT_MODEL). AMC only accepts a model that is installed."),
    SettingSpec("embed_model", "model",
                "Local Ollama embedding model for knowledge-base search. After changing it, re-index the knowledge "
                "base; until then search uses the offline keyword method. Empty means the deployment default "
                "(AM_EMBED_MODEL)."),
    SettingSpec("symptom_llm_detection", "bool",
                "Also ask the local model to notice possible symptoms in chat messages. It only creates proposals: "
                "nothing is recorded until the astronaut confirms. Keyword detection runs either way."),
    SettingSpec("crew_network_enabled", "bool",
                "Crew-network hub switch. It has an effect only when the server also runs with "
                "AM_CREW_NETWORK=1."),
    # Bounds are an engineering limit on disk use, not a recommendation.
    SettingSpec("backup_retention", "int",
                "Number of encrypted backups to keep. After each new backup, older backup files beyond this number "
                "are deleted; their records stay in the list.", min_value=1, max_value=1000),
]}


class ModelServiceOffline(RuntimeError):
    """A model setting cannot be checked because Ollama is not reachable."""


def spec_for(key: str) -> SettingSpec:
    if key not in DEFAULTS:
        raise LookupError(f"unknown setting '{key}'")
    if key in SPECS:
        return SPECS[key]
    # A setting added to the store later still gets a type check from its default value.
    default = DEFAULTS[key]
    kind: Kind = "bool" if isinstance(default, bool) else "int" if isinstance(default, int) else "text"
    return SettingSpec(key, kind, "No description recorded for this setting yet.")


def _effective(session: Session, settings: Settings, key: str) -> Any:
    if key == "chat_model":
        return effective_chat_model(session, settings)
    if key == "embed_model":
        return effective_embed_model(session, settings)
    return get_setting(session, key)


def _deployment_default(settings: Settings, key: str) -> str | None:
    """The model used when the setting is empty (AM_CHAT_MODEL / AM_EMBED_MODEL)."""
    return {"chat_model": settings.chat_model, "embed_model": settings.embed_model}.get(key)


def describe(session: Session, settings: Settings, key: str) -> dict[str, Any]:
    spec = spec_for(key)
    row = session.get(AppSetting, key)
    value = get_setting(session, key)
    return {
        "key": key,
        "kind": spec.kind,
        "value": value,
        "default": DEFAULTS[key],
        "effective": _effective(session, settings, key),
        "deployment_default": _deployment_default(settings, key),
        "is_default": row is None or value == DEFAULTS[key],
        "description": spec.description,
        "min_value": spec.min_value,
        "max_value": spec.max_value,
        "updated_by": None if row is None else row.updated_by,
        "updated_at": None if row is None else row.updated_at,
    }


def describe_all(session: Session, settings: Settings) -> list[dict[str, Any]]:
    return [describe(session, settings, k) for k in DEFAULTS]


def _check_model(value: Any, llm: OllamaClient) -> str | None:
    if value is None:
        return None  # back to the deployment default; nothing to verify
    if not isinstance(value, str):
        raise ValueError("model must be a model name (text) or null for the deployment default")
    name = value.strip()
    if not name:
        return None
    if len(name) > MODEL_NAME_MAX:
        raise ValueError(f"model name must be at most {MODEL_NAME_MAX} characters")
    status = llm.status(name)
    if not status["reachable"]:
        raise ModelServiceOffline(
            "The local model service (Ollama) is not reachable, so AMC cannot confirm that "
            f"'{name}' is installed. Start Ollama and try again; the current model stays in use.")
    if not status["model_installed"]:
        installed = ", ".join(status.get("installed_models") or []) or "none"
        raise ValueError(f"model '{name}' is not installed in Ollama (installed: {installed}). "
                         f"Install it first with: ollama pull {name}")
    return name


def validate(key: str, value: Any, llm: OllamaClient) -> Any:
    """Return the value to store, or raise ValueError / ModelServiceOffline with an operator-readable reason."""
    spec = spec_for(key)
    if spec.kind == "model":
        return _check_model(value, llm)
    if spec.kind == "bool":
        # bool only: 0/1 and "true" are rejected so a typo cannot flip a safety switch
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be a whole number")
        if spec.min_value is not None and value < spec.min_value:
            raise ValueError(f"{key} must be at least {spec.min_value}")
        if spec.max_value is not None and value > spec.max_value:
            raise ValueError(f"{key} must be at most {spec.max_value}")
        return value
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def update(session: Session, settings: Settings, llm: OllamaClient, key: str, value: Any,
           actor: str) -> dict[str, Any]:
    clean = validate(key, value, llm)
    set_setting(session, key, clean, actor)  # writes the audit entry (old and new value)
    return describe(session, settings, key)
