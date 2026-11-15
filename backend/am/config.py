"""Application settings.

All runtime data (database, backups, vector index) lives OUTSIDE the source tree by
default: health data must never be synced implicitly with the code.

Windows: %USERPROFILE%\\.am\\data. NOT under %LOCALAPPDATA%: packaged (MSIX) apps, such as the
Claude desktop app, see a virtualized copy of AppData. Processes started from such an app write
new files into a private package folder, while other processes see only the real folder. A
SQLite database and its -wal/-shm files then end up split across the two views, which corrupts
the database (this happened on 2026-11-14). The user profile root is not virtualized.
Other systems: ~/.local/share/am. Override with AM_DATA_DIR or AM_DATABASE_URL.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    if os.name == "nt":
        return Path.home() / ".am" / "data"  # not AppData: see the module docstring
    return Path.home() / ".local" / "share" / "am"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AM_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    database_url: str | None = None  # defaults to sqlite file inside data_dir
    thresholds_path: Path = REPO_ROOT / "config" / "thresholds.demo.yaml"
    baseline_policy_path: Path = REPO_ROOT / "config" / "baseline_policy.yaml"

    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"
    llm_timeout_s: float = 180.0

    # Cross-origin browser access is off by default: the console is served from the API's own
    # origin (and the Vite dev server proxies /api), so no other origin needs access.
    cors_origins: list[str] = []
    # Host header allow-list: blocks DNS-rebinding access to local health data.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "[::1]"]
    run_migrations_on_startup: bool = True
    # Built console served by the API when present (None disables). See am.main.mount_frontend.
    frontend_dist: Path | None = REPO_ROOT / "frontend" / "dist"

    # Sign-in. argon2id cost profile, see am.auth.passwords ("fast" is for automated tests only).
    password_hash_profile: str = "standard"
    # Plain-text sign-in details of the TEST crew accounts, written by `am.cli seed-crew` (gitignored).
    test_accounts_file: Path = REPO_ROOT / "TEST_CREDENTIALS.txt"
    # AM_TEST_MODE=1 mounts the /api/test router used by the separate Test Console (End Day,
    # live test entries, scenario fixtures). Off by default: production-like behaviour.
    test_mode: bool = False
    # Test bench (simulated telemetry, mission clock control). Off = production-like behaviour.
    test_bench: bool = True
    # Background generation of simulated live telemetry while the test bench is on.
    live_ticker: bool = True
    # Background AI triage of the doctor's live feed (am.triage.ai). The rule level never depends on it.
    ai_triage_worker: bool = True
    # Background writer of AM Core report narratives (am.amcore.narrative).
    am_core_worker: bool = True
    # SIMULATED demo space events (jury demonstrations): only in test mode (AM_TEST_MODE=1).
    demo_events: bool = True
    demo_event_interval_s: float = 180.0

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
