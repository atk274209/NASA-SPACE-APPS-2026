"""Accounts, sign-in and sessions.

Roles:
  astronaut  linked to exactly one astronaut record; may read and write only that record
  doctor     crew medical officer / flight surgeon; may read every crew member's records

All timers here use REAL time (am.db.base.real_utcnow): moving the mission clock in the test
bench must neither expire nor extend a sign-in session or a lockout.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from am.audit import audit
from am.auth.passwords import DEFAULT_N_LOG2, check_new_password, hash_password, verify_password
from am.db.base import real_utcnow
from am.db.models import Astronaut
from am.db.models_m6 import AuthSession, UserAccount

ROLE_ASTRONAUT = "astronaut"
ROLE_DOCTOR = "doctor"
ROLES = (ROLE_ASTRONAUT, ROLE_DOCTOR)

SESSION_COOKIE = "am_session"
SESSION_TTL = timedelta(hours=12)
LAST_SEEN_RESOLUTION = timedelta(minutes=1)  # do not write to the database on every request

# Sign-in throttle: after this many failures for one (client, username) within the window,
# further attempts are refused until the oldest failure leaves the window.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=5)

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")


class AuthError(Exception):
    """Sign-in or account operation refused; the message is safe to show to the user."""


@dataclass(frozen=True)
class Throttled(Exception):
    retry_after_s: int


class LoginThrottle:
    """In-memory failure counter per (client host, username). Process-local by design."""

    def __init__(self, max_failures: int = LOGIN_MAX_FAILURES, window: timedelta = LOGIN_WINDOW):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: datetime) -> deque[datetime]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def check(self, client: str, username: str) -> None:
        now = real_utcnow()
        with self._lock:
            q = self._prune((client, username.lower()), now)
            if len(q) >= self.max_failures:
                raise Throttled(max(1, int((q[0] + self.window - now).total_seconds())))

    def failed(self, client: str, username: str) -> None:
        now = real_utcnow()
        with self._lock:
            self._prune((client, username.lower()), now).append(now)

    def succeeded(self, client: str, username: str) -> None:
        with self._lock:
            self._failures.pop((client, username.lower()), None)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def count_accounts(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(UserAccount)) or 0


def create_account(session: Session, *, username: str, display_name: str, role: str, password: str,
                   astronaut_id: int | None = None, is_test_account: bool = False,
                   n_log2: int = DEFAULT_N_LOG2, actor: str = "am-system") -> UserAccount:
    username = normalize_username(username)
    if not _USERNAME.match(username):
        raise AuthError("username must be 3 to 40 characters: lowercase letters, digits, dot, dash or underscore")
    if role not in ROLES:
        raise AuthError(f"role must be one of {list(ROLES)}")
    if not display_name.strip():
        raise AuthError("display name is required")
    try:
        check_new_password(password)
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    if role == ROLE_ASTRONAUT:
        if astronaut_id is None or session.get(Astronaut, astronaut_id) is None:
            raise AuthError("an astronaut account must be linked to an existing astronaut record")
        if session.scalars(select(UserAccount).where(UserAccount.astronaut_id == astronaut_id)).first():
            raise AuthError("this astronaut record already has an account")
    elif astronaut_id is not None:
        raise AuthError("a doctor account is not linked to an astronaut record")
    if session.scalars(select(UserAccount).where(UserAccount.username == username)).first():
        raise AuthError("this username is already taken")
    user = UserAccount(username=username, display_name=display_name.strip(), role=role, astronaut_id=astronaut_id,
                       password_hash=hash_password(password, n_log2), is_test_account=is_test_account)
    session.add(user)
    session.flush()
    audit(session, actor=actor, action="account.created", entity="user_account", entity_id=user.id,
          payload={"username": username, "role": role, "astronaut_id": astronaut_id, "test": is_test_account})
    return user


def set_password(session: Session, user: UserAccount, password: str, *, n_log2: int = DEFAULT_N_LOG2,
                 actor: str) -> None:
    try:
        check_new_password(password)
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    user.password_hash = hash_password(password, n_log2)
    revoke_all_sessions(session, user.id)
    audit(session, actor=actor, action="account.password_changed", entity="user_account", entity_id=user.id)


def set_disabled(session: Session, user: UserAccount, disabled: bool, *, actor: str) -> None:
    user.disabled_at = real_utcnow() if disabled else None
    if disabled:
        revoke_all_sessions(session, user.id)
    audit(session, actor=actor, action="account.disabled" if disabled else "account.enabled",
          entity="user_account", entity_id=user.id)


_dummy_hash: dict[int, str] = {}


def authenticate(session: Session, username: str, password: str, *, n_log2: int = DEFAULT_N_LOG2) -> UserAccount | None:
    """Return the account when the password matches and the account is enabled, else None.

    An unknown username still costs one hash verification, so response time does not reveal
    which usernames exist.
    """
    user = session.scalars(select(UserAccount).where(UserAccount.username == normalize_username(username))).first()
    if user is None:
        if n_log2 not in _dummy_hash:
            _dummy_hash[n_log2] = hash_password(secrets.token_hex(8), n_log2)
        verify_password(password, _dummy_hash[n_log2])
        return None
    if not verify_password(password, user.password_hash) or user.disabled_at is not None:
        return None
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def open_session(session: Session, user: UserAccount, *, device_name: str = "", client_host: str | None = None,
                 ttl: timedelta = SESSION_TTL) -> str:
    """Create a session and return the raw token (only its SHA-256 is stored)."""
    token = secrets.token_urlsafe(32)
    now = real_utcnow()
    session.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), device_name=device_name.strip()[:80],
                            client_host=client_host, created_at=now, last_seen_at=now, expires_at=now + ttl))
    user.last_login_at = now
    session.flush()
    return token


def resolve_session(session: Session, token: str | None) -> tuple[AuthSession, UserAccount] | None:
    if not token or len(token) > 200:
        return None
    row = session.scalars(select(AuthSession).where(AuthSession.token_hash == _token_hash(token))).first()
    now = real_utcnow()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    user = session.get(UserAccount, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    return row, user


def touch_session(session: Session, row: AuthSession) -> bool:
    """Record activity at most once per LAST_SEEN_RESOLUTION. Returns True if a write is pending."""
    now = real_utcnow()
    if now - row.last_seen_at < LAST_SEEN_RESOLUTION:
        return False
    row.last_seen_at = now
    return True


def close_session(session: Session, token: str | None) -> bool:
    found = resolve_session(session, token)
    if found is None:
        return False
    found[0].revoked_at = real_utcnow()
    return True


def revoke_all_sessions(session: Session, user_id: int) -> int:
    now = real_utcnow()
    rows = session.scalars(select(AuthSession).where(AuthSession.user_id == user_id,
                                                     AuthSession.revoked_at.is_(None))).all()
    for r in rows:
        r.revoked_at = now
    return len(rows)
