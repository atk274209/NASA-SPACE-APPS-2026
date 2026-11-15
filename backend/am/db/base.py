"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Stable constraint names keep Alembic migrations deterministic (esp. SQLite batch mode).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Mission clock. In real use the offset stays zero and mission time is UTC wall-clock time.
# The test bench (am.simlive) may move mission time FORWARD to rehearse a long mission in
# minutes; it can never move back, so stored records are never "in the future".
# One process serves one database, so the offset is process-wide (persisted by am.clock).
_clock_offset = timedelta(0)


def real_utcnow() -> datetime:
    """Wall-clock naive UTC. Only for security timers (session expiry, sign-in rate limits)."""
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow() -> datetime:
    """Mission-clock naive UTC timestamp. All datetimes are stored as naive UTC."""
    return real_utcnow() + _clock_offset


def clock_offset() -> timedelta:
    return _clock_offset


def set_clock_offset(offset: timedelta, *, allow_backward: bool = False) -> None:
    """Set how far mission time runs ahead of real time.

    `allow_backward` is only for loading the persisted offset at start-up and for tests.
    """
    global _clock_offset
    if offset < timedelta(0):
        raise ValueError("mission time cannot be behind real time")
    if offset < _clock_offset and not allow_backward:
        raise ValueError("the mission clock cannot move backward")
    _clock_offset = offset


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            # ms; several writers (requests, live ticker, AI triage) share the file. Model calls never hold a
            # write transaction (review findings R01/R03), so short writes only wait for each other.
            cur.execute("PRAGMA busy_timeout=30000")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
