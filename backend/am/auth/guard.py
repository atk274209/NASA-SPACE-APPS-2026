"""Request authorization for every /api/v1 route (fail closed).

How a request is decided
------------------------
1. `authorize` runs as a dependency on every API router.
2. The endpoint's policy is read from a marker set by one of the decorators below. A route
   without a marker is SCOPED, the most restrictive policy for crew members.

   @public        no sign-in needed (sign-in itself, sign-out, first-run setup)
   @signed_in     any signed-in user; the handler returns only what that user may see
   @doctor_only   medical officer / flight surgeon only
   (no marker)    SCOPED: a doctor may call it for anyone; an astronaut only when every
                  astronaut the request refers to is their own record, and it must refer to
                  at least one.

3. For SCOPED requests from an astronaut, the astronaut records the request refers to are
   collected from: the `astronaut_id` path parameter, every `astronaut_id` query value, the
   JSON body (`astronaut_id`, `items[].astronaut_id`), and record ids that are resolved to
   their owner (see OWNER_PARAMS: `alert_id`, `conversation_id`, `task_id`, ...).

Put the marker decorator BELOW the route decorator:

    @router.post("/alerts/{alert_id}/resolve")
    @doctor_only
    def resolve_alert(...): ...

A new record type addressed by id must be registered with `register_owner`, otherwise an
astronaut's request for it is refused (403) rather than served unchecked.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from am.audit import audit, set_audit_user
from am.auth.service import ROLE_ASTRONAUT, ROLE_DOCTOR, resolve_session, session_cookie_name, touch_session

PUBLIC = "public"
SIGNED_IN = "signed_in"
DOCTOR = "doctor"
SCOPED = "scoped"
POLICY_ATTR = "__am_policy__"
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _mark(policy: str) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        setattr(fn, POLICY_ATTR, policy)
        return fn
    return deco


public = _mark(PUBLIC)
signed_in = _mark(SIGNED_IN)
doctor_only = _mark(DOCTOR)


def policy_of(endpoint: Any) -> str:
    return getattr(endpoint, POLICY_ATTR, SCOPED)


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    display_name: str
    role: str
    astronaut_id: int | None
    session_id: int
    device_name: str
    client_host: str | None
    is_test_account: bool = False

    @property
    def is_doctor(self) -> bool:
        return self.role == ROLE_DOCTOR

    def may_access(self, astronaut_id: int) -> bool:
        return self.is_doctor or self.astronaut_id == astronaut_id


# ------------------------------------------------------------------ record owners
OwnerResolver = Callable[[Session, int], "int | None"]
OWNER_PARAMS: dict[str, OwnerResolver] = {}


def register_owner(param: str, model: type, attr: str = "astronaut_id") -> None:
    """Declare that a path/body parameter named `param` is the id of a `model` row owned by
    the astronaut in `attr`."""
    def resolve(session: Session, record_id: int) -> int | None:
        row = session.get(model, record_id)
        return None if row is None else getattr(row, attr)
    OWNER_PARAMS[param] = resolve


def _register_builtin_owners() -> None:
    from am.db.models import Alert, BaselineProfile
    from am.db.models_ai import Conversation
    from am.db.models_m5 import EscalationEvent, SymptomProposal, SymptomReport
    from am.db.models_m6 import AiNote
    from am.db.models_research import DailyTask, ResearchFinding, ResearchObservation

    for param, model in (
        ("alert_id", Alert), ("profile_id", BaselineProfile), ("conversation_id", Conversation),
        ("task_id", DailyTask), ("finding_id", ResearchFinding), ("obs_id", ResearchObservation),
        ("observation_id", ResearchObservation), ("proposal_id", SymptomProposal),
        ("report_id", SymptomReport), ("symptom_report_id", SymptomReport), ("event_id", EscalationEvent),
        ("note_id", AiNote),
    ):
        register_owner(param, model)


_register_builtin_owners()


class _Deny(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise _Deny(403, "This request does not name a valid astronaut record.") from exc


def referenced_astronauts(session: Session, path_params: dict[str, Any], query_ids: list[str],
                          body: Any) -> set[int]:
    """Every astronaut record a request refers to. Raises _Deny(404) for an unknown record id."""
    owners: set[int] = set()

    def add_record(param: str, raw: Any) -> None:
        owner = OWNER_PARAMS[param](session, _as_int(raw))
        if owner is None:
            raise _Deny(404, "Record not found.")
        owners.add(owner)

    for name, raw in path_params.items():
        if name == "astronaut_id":
            owners.add(_as_int(raw))
        elif name in OWNER_PARAMS:
            add_record(name, raw)
    owners.update(_as_int(v) for v in query_ids)
    if isinstance(body, dict):
        if body.get("astronaut_id") is not None:
            owners.add(_as_int(body["astronaut_id"]))
        for name in OWNER_PARAMS:
            if body.get(name) is not None:
                add_record(name, body[name])
        items = body.get("items")
        if isinstance(items, list):
            owners.update(_as_int(it["astronaut_id"]) for it in items
                          if isinstance(it, dict) and it.get("astronaut_id") is not None)
    return owners


def _token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(session_cookie_name(request.headers))


def _same_origin(request: Request) -> bool:
    """Reject a browser write that comes from another origin (defence in depth next to the
    SameSite=Strict session cookie). Requests without an Origin header are not browser
    cross-site requests and pass."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].lower() == host.lower()


# Doctor reads of crew records are audited (master prompt Module J). Polling screens read every few
# seconds, so one entry per (doctor session, astronaut, route) per READ_AUDIT_WINDOW is enough.
READ_AUDIT_WINDOW_S = 600
_read_seen: dict[tuple[int, int | None, str], float] = {}
_read_lock = threading.Lock()


def _audit_doctor_read(s: Session, principal: Principal, route: str, owners: set[int] | None) -> bool:
    now = time.monotonic()
    targets: list[int | None] = sorted(owners) if owners else [None]  # None: a crew-wide doctor screen
    fresh = []
    with _read_lock:
        for owner in targets:
            key = (principal.session_id, owner, route)
            if now - _read_seen.get(key, -1e9) >= READ_AUDIT_WINDOW_S:
                _read_seen[key] = now
                fresh.append(owner)
    for owner in fresh:
        audit(s, actor=principal.username, action="doctor.read", entity="astronaut" if owner else "crew",
              entity_id=owner, payload={"route": route})
    return bool(fresh)


def _evaluate(ctx: Any, token: str | None, policy: str, path_params: dict[str, Any], query_ids: list[str],
              body: Any, client_host: str | None, method: str = "GET", route: str = "") -> Principal | None:
    """Runs in a worker thread with its own short database session."""
    with ctx.session_factory() as s:
        found = resolve_session(s, token)
        if found is None:
            if policy == PUBLIC:
                return None
            raise _Deny(401, "Sign in to continue.")
        row, user = found
        if touch_session(s, row):
            s.commit()
        principal = Principal(user_id=user.id, username=user.username, display_name=user.display_name,
                              role=user.role, astronaut_id=user.astronaut_id, session_id=row.id,
                              device_name=row.device_name, client_host=client_host,
                              is_test_account=user.is_test_account)
        if principal.is_doctor and method == "GET" and policy in (SCOPED, DOCTOR) and route:
            try:
                owners = referenced_astronauts(s, path_params, query_ids, None) if policy == SCOPED else None
            except _Deny:
                owners = None
            if _audit_doctor_read(s, principal, route, owners):
                s.commit()
        if policy in (PUBLIC, SIGNED_IN) or principal.is_doctor:
            return principal
        if policy == DOCTOR:
            raise _Deny(403, "Only the medical officer account can do this.")
        if principal.role != ROLE_ASTRONAUT or principal.astronaut_id is None:
            raise _Deny(403, "This account has no astronaut record.")
        owners = referenced_astronauts(s, path_params, query_ids, body)
        if not owners:
            raise _Deny(403, "This request must name your own astronaut record.")
        if owners != {principal.astronaut_id}:
            raise _Deny(403, "You can only access your own records.")
        return principal


async def authorize(request: Request) -> None:
    endpoint = request.scope.get("endpoint")
    policy = policy_of(endpoint)
    if request.method in _WRITE_METHODS and not _same_origin(request):
        raise HTTPException(403, "Cross-origin requests are not accepted.")
    body: Any = None
    if policy == SCOPED and request.method in _WRITE_METHODS \
            and "json" in request.headers.get("content-type", "").lower():
        try:
            body = await request.json()  # cached by Starlette; the endpoint parses it again
        except Exception:  # malformed JSON: the endpoint's own validation reports it
            body = None
    client_host = request.client.host if request.client else None
    try:
        principal = await run_in_threadpool(
            _evaluate, request.app.state.ctx, _token_from(request), policy, dict(request.path_params),
            request.query_params.getlist("astronaut_id"), body, client_host, request.method,
            getattr(request.scope.get("route"), "path", ""))
    except _Deny as deny:
        raise HTTPException(deny.status, deny.detail) from None
    request.state.principal = principal
    set_audit_user(principal.username if principal else None)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Sign in to continue.")
    return principal


def optional_principal(request: Request) -> Principal | None:
    return getattr(request.state, "principal", None)


CurrentUser = Annotated[Principal, Depends(current_principal)]
MaybeUser = Annotated["Principal | None", Depends(optional_principal)]


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "testclient")
