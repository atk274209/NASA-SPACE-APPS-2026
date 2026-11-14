from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from am.api.deps import AppContext
from am.auth.service import ROLE_ASTRONAUT, ROLE_DOCTOR, SESSION_COOKIE, create_account, open_session
from am.config import Settings
from am.db.base import set_clock_offset
from am.db.enums import SourceKind
from am.db.models import Astronaut
from am.db.models_m6 import UserAccount
from am.health.ingest import IngestService, MeasurementInput
from am.main import build_context, create_app

NOW = datetime(2026, 11, 14, 12, 0, 0)
TEST_PASSWORD = "test-password-123"  # for accounts created inside tests only


@pytest.fixture(autouse=True)
def _mission_clock_at_real_time():
    """The mission clock offset is process-wide; no test may leak a moved clock into the next."""
    set_clock_offset(timedelta(0), allow_backward=True)
    yield
    set_clock_offset(timedelta(0), allow_backward=True)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}",
        ollama_url="http://127.0.0.1:9",  # deliberately unreachable: tests must not need a model
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        password_hash_profile="fast",  # cheap hashing: tests create many accounts
        live_ticker=False,          # tests drive the test bench explicitly
        test_accounts_file=tmp_path / "TEST_CREDENTIALS.txt",
        test_mode=True,             # mount /api/test (the Test Console router)
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


def sign_in(client: TestClient, *, role: str = ROLE_DOCTOR, astronaut_id: int | None = None,
            username: str | None = None) -> UserAccount:
    """Give `client` a signed-in session (the account is created on first use).

    Goes straight to the service layer, so tests do not pay for password hashing on every
    request; the sign-in endpoint itself is covered in tests/test_auth.py.
    """
    ctx: AppContext = client.app.state.ctx
    name = username or ("testdoctor" if role == ROLE_DOCTOR else f"astro{astronaut_id}")
    with ctx.session_factory() as s:
        user = s.query(UserAccount).filter(UserAccount.username == name).first()
        if user is None:
            user = create_account(s, username=name, display_name=name.title(), role=role, password=TEST_PASSWORD,
                                  astronaut_id=astronaut_id, is_test_account=True,
                                  profile=ctx.settings.password_hash_profile)
        token = open_session(s, user, device_name="pytest", client_host="testclient")
        s.commit()
    client.cookies.set(SESSION_COOKIE, token)
    return user


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    """API client signed in as the DOCTOR (may read and write every crew member's records)."""
    with TestClient(create_app(settings)) as c:
        sign_in(c, role=ROLE_DOCTOR)
        yield c


@pytest.fixture
def anon(client) -> TestClient:
    """Client for the same app with no session."""
    return TestClient(client.app)


@pytest.fixture
def as_astronaut(client) -> Callable[[int], TestClient]:
    """Factory: a client for the same app signed in as the astronaut account of `astronaut_id`."""
    def make(astronaut_id: int) -> TestClient:
        c = TestClient(client.app)
        sign_in(c, role=ROLE_ASTRONAUT, astronaut_id=astronaut_id)
        return c
    return make


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
