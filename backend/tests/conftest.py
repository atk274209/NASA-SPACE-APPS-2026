from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from am.api.deps import AppContext
from am.config import Settings
from am.db.enums import SourceKind
from am.db.models import Astronaut
from am.health.ingest import IngestService, MeasurementInput
from am.main import build_context, create_app

NOW = datetime(2026, 11, 14, 12, 0, 0)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}",
        ollama_url="http://127.0.0.1:9",  # deliberately unreachable: tests must not need a model
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
    )


@pytest.fixture
def ctx(settings) -> Iterator[AppContext]:
    c = build_context(settings)
    yield c
    c.llm.close()
    c.engine.dispose()


@pytest.fixture
def db(ctx) -> Iterator[Session]:
    with ctx.session_factory() as s:
        yield s


@pytest.fixture
def astronaut(db) -> Astronaut:
    a = Astronaut(code="T-001", display_name="Test Astronaut", is_simulated=True)
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def ingest(db, ctx) -> IngestService:
    return IngestService(db, ctx.thresholds, ctx.policy)


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def hr(astronaut_id: int, value: float, t: datetime, context: str = "rest", **kw) -> MeasurementInput:
    return MeasurementInput(
        astronaut_id=astronaut_id,
        metric_code=kw.pop("metric", "heart_rate"),
        value=value,
        unit=kw.pop("unit", "bpm"),
        context=context,
        recorded_at=t,
        source_name=kw.pop("source_name", "test-fixture"),
        source_kind=SourceKind.SIMULATED,
        **kw,
    )


def feed_baseline(
    svc: IngestService,
    astronaut_id: int,
    *,
    start: datetime,
    n: int,
    every_min: float,
    mean: float = 70.0,
    sd: float = 2.5,
    context: str = "rest",
    metric: str = "heart_rate",
    unit: str = "bpm",
    seed: int = 1,
) -> datetime:
    """Feed n synthetic readings; returns timestamp of the last one."""
    rng = np.random.default_rng(seed)
    t = start
    for i in range(n):
        t = start + timedelta(minutes=every_min * i)
        svc.ingest(hr(astronaut_id, round(float(rng.normal(mean, sd)), 1), t, context, metric=metric, unit=unit),
                   now=NOW + timedelta(days=365))
    svc.s.commit()
    return t
