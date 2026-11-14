"""Application settings.

All runtime data (database, backups, vector index) lives OUTSIDE the source tree by
default, under %LOCALAPPDATA%/AM/data (or ~/.local/share/am on other systems). The
source tree sits in a cloud-synced folder; health data must never be synced implicitly.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "AM" / "data"
    return Path.home() / ".local" / "share" / "am"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AM_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    database_url: str | None = None  # defaults to sqlite file inside data_dir
    thresholds_path: Path = REPO_ROOT / "config" / "thresholds.demo.yaml"
    baseline_policy_path: Path = REPO_ROOT / "config" / "baseline_policy.yaml"

    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen2.5:7b-instruct"
    embed_model: str = "nomic-embed-text"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Host header allow-list: blocks DNS-rebinding access to local health data.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "[::1]"]
    run_migrations_on_startup: bool = True

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'am.sqlite3').as_posix()}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
