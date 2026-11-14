"""Operator diagnostics: storage, schema revision, model service, embedding coverage, backups, versions.

Read-only. Every value is measured when the request arrives, from the database, the file
system or the Ollama service; nothing is estimated or cached. The model service is asked
once, with the client's short listing timeout, so diagnostics stay quick when Ollama is down.
"""

from __future__ import annotations

import platform
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import fastapi
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.admin import backup
from am.admin.files import folder_size
from am.admin.outbox import outbox_dir
from am.ai.ollama import LLMUnavailable
from am.api.deps import AppContext
from am.config import Settings
from am.db.base import utcnow
from am.db.migrate import alembic_config
from am.db.models import Alert, Astronaut, AuditLog, BaselineProfile, Measurement, Mission
from am.db.models_ai import Conversation, MemoryItem, Message, SelfReport
from am.db.models_m5 import SymptomProposal, SymptomReport, SyncPackage
from am.db.models_research import DailyTask, DocumentChunk, JournalEntry, KnowledgeDocument, ResearchObservation
from am.settings_store import effective_chat_model, effective_embed_model, get_setting

KEY_TABLES: tuple[type, ...] = (
    Astronaut, Mission, Measurement, BaselineProfile, Alert, SelfReport, SymptomReport, SymptomProposal,
    Conversation, Message, MemoryItem, JournalEntry, DailyTask, ResearchObservation, KnowledgeDocument,
    DocumentChunk, SyncPackage, AuditLog,
)


def row_counts(session: Session) -> dict[str, int]:
    return {m.__tablename__: int(session.scalar(select(func.count()).select_from(m)) or 0) for m in KEY_TABLES}


def _alembic_head(url: str) -> str | None:
    from alembic.script import ScriptDirectory

    try:
        return ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    except Exception:  # noqa: BLE001 - a broken migration folder must not break diagnostics
        return None


def database_info(session: Session, ctx: AppContext) -> dict[str, Any]:
    url = ctx.engine.url
    path: Path | None = None
    try:
        path = backup.sqlite_file(ctx.engine)
    except backup.BackupUnavailable:
        pass
    revision = backup.alembic_revision(session)
    head = _alembic_head(ctx.settings.resolved_database_url)
    wal = None if path is None else Path(f"{path}-wal")
    return {
        "engine": url.get_backend_name(),
        "path": None if path is None else str(path),
        "size_bytes": path.stat().st_size if path is not None and path.exists() else None,
        "wal_bytes": wal.stat().st_size if wal is not None and wal.exists() else 0,
        "alembic_revision": revision,
        "alembic_head": head,
        "at_head": revision is not None and revision == head,
        "row_counts": row_counts(session),
    }


def storage_info(settings: Settings) -> dict[str, Any]:
    settings.ensure_dirs()
    usage = shutil.disk_usage(settings.data_dir)
    backups = backup.backups_dir(settings)
    outbox = outbox_dir(settings)
    return {
        "data_dir": str(settings.data_dir),
        "backups_dir": str(backups),
        "outbox_dir": str(outbox),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "backups_bytes": folder_size(backups),
        "outbox_bytes": folder_size(outbox),
    }


def _installed(model: str, names: list[str]) -> bool:
    # same rule as OllamaClient.status: a name without a tag means ':latest'
    return model in names or (":" not in model and f"{model}:latest" in names)


def ollama_info(session: Session, ctx: AppContext) -> dict[str, Any]:
    chat = effective_chat_model(session, ctx.settings)
    embed = effective_embed_model(session, ctx.settings)
    try:
        names, reachable, reason = ctx.llm.list_models(), True, None
    except LLMUnavailable as exc:
        names, reachable, reason = [], False, exc.reason
    return {
        "url": ctx.settings.ollama_url,
        "reachable": reachable,
        "reason": reason,
        "installed_models": names,
        "chat_model": chat,
        "chat_model_source": "setting" if get_setting(session, "chat_model") else "deployment default",
        "chat_model_installed": _installed(chat, names) if reachable else None,
        "embed_model": embed,
        "embed_model_source": "setting" if get_setting(session, "embed_model") else "deployment default",
        "embed_model_installed": _installed(embed, names) if reachable else None,
    }


def embedding_info(session: Session, embed_model: str, model_usable: bool) -> dict[str, Any]:
    """How much of the knowledge base the current embedding model covers.

    Search uses embeddings only when every chunk carries one from the current model and the
    model answers; otherwise it uses the offline keyword method (am.rag.knowledge.search).
    """
    total = int(session.scalar(select(func.count()).select_from(DocumentChunk)) or 0)
    current = int(session.scalar(select(func.count()).select_from(DocumentChunk).where(
        DocumentChunk.embedding.is_not(None), DocumentChunk.embed_model == embed_model)) or 0)
    other = int(session.scalar(select(func.count()).select_from(DocumentChunk).where(
        DocumentChunk.embedding.is_not(None), DocumentChunk.embed_model != embed_model)) or 0)
    if total == 0:
        method = "no documents ingested"
    elif current == total and model_usable:
        method = "embedding"
    else:
        method = "keyword (offline fallback)"
    return {
        "embed_model": embed_model,
        "chunks_total": total,
        "chunks_current_model": current,
        "chunks_other_model": other,
        "chunks_without_embedding": total - current - other,
        "coverage_percent": round(current / total * 100, 1) if total else None,
        "search_method": method,
    }


def backup_info(session: Session) -> dict[str, Any]:
    last = backup.last_backup(session)
    stored = int(session.scalar(select(func.count()).select_from(SyncPackage).where(
        SyncPackage.kind == backup.KIND, SyncPackage.status == backup.STATUS_STORED)) or 0)
    return {
        "last_backup_at": None if last is None else last.created_at,
        "last_backup_id": None if last is None else last.id,
        "stored": stored,
        "retention": get_setting(session, "backup_retention"),
    }


def version_info(ctx: AppContext, app_version: str) -> dict[str, Any]:
    return {
        "app": app_version,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "sqlalchemy": sqlalchemy.__version__,
        "fastapi": fastapi.__version__,
        "os": f"{platform.system()} {platform.release()}",
        "thresholds_version": ctx.thresholds.version,
        "thresholds_label": ctx.thresholds.label,
        "baseline_policy_version": ctx.policy.version,
    }


def collect(session: Session, ctx: AppContext, app_version: str) -> dict[str, Any]:
    ollama = ollama_info(session, ctx)
    return {
        "generated_at": utcnow(),
        "database": database_info(session, ctx),
        "storage": storage_info(ctx.settings),
        "ollama": ollama,
        "embeddings": embedding_info(session, ollama["embed_model"], bool(ollama["embed_model_installed"])),
        "backups": backup_info(session),
        "versions": version_info(ctx, app_version),
    }
