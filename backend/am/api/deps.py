"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from am.ai.ollama import OllamaClient
from am.auth.service import LoginThrottle
from am.config import Settings
from am.policy import BaselinePolicy, ThresholdConfig


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    thresholds: ThresholdConfig
    policy: BaselinePolicy
    llm: OllamaClient
    login_throttle: LoginThrottle = field(default_factory=LoginThrottle)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.ctx.session_factory()
    try:
        yield session
    finally:
        session.close()
