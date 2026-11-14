"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from am.api.deps import AppContext
from am.ai.ollama import OllamaClient
from am.api.v1 import (
    admin,
    companion,
    core,
    crew,
    guidance,
    journal_ext,
    monitoring,
    network,
    research,
    streaks,
    symptoms,
)
from am.config import Settings, get_settings
from am.db.base import make_engine, make_session_factory
from am.db.migrate import upgrade_to_head
from am.health.metrics import seed_metric_registry
from am.policy import load_baseline_policy, load_thresholds


def build_context(settings: Settings) -> AppContext:
    settings.ensure_dirs()
    url = settings.resolved_database_url
    if settings.run_migrations_on_startup:
        upgrade_to_head(url)
    engine = make_engine(url)
    factory = make_session_factory(engine)
    with factory() as s:
        seed_metric_registry(s)
        s.commit()
    return AppContext(
        settings=settings,
        engine=engine,
        session_factory=factory,
        thresholds=load_thresholds(settings.thresholds_path),
        policy=load_baseline_policy(settings.baseline_policy_path),
        llm=OllamaClient(settings.ollama_url, timeout=settings.llm_timeout_s),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = build_context(settings)
        yield
        app.state.ctx.llm.close()
        app.state.ctx.engine.dispose()

    app = FastAPI(
        title="AM — Allied Mastercomputer API",
        version="0.1.0",
        description="Offline-first astronaut health monitoring. Alert thresholds are DEMO values, "
                    "not clinically validated.",
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(core.router, prefix="/api/v1")
    app.include_router(monitoring.router, prefix="/api/v1")
    app.include_router(companion.router, prefix="/api/v1")
    app.include_router(research.router, prefix="/api/v1")
    for module in (crew, symptoms, streaks, journal_ext, admin, guidance, network):
        app.include_router(module.router, prefix="/api/v1")
    return app


app = create_app()
