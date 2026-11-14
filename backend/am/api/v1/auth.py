"""Sign-in, sign-out, first-run setup and account management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.api.deps import AppContext, get_ctx, get_db
from am.audit import audit
from am.auth.guard import CurrentUser, MaybeUser, Principal, doctor_only, is_loopback, public, signed_in
from am.auth.service import (
    ROLE_DOCTOR,
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    Throttled,
    authenticate,
    close_session,
    count_accounts,
    create_account,
    open_session,
    set_disabled,
    set_password,
)
from am.clock import clock_state
from am.db.models import Astronaut
from am.db.models_m6 import UserAccount

router = APIRouter(tags=["auth"])
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    astronaut_id: int | None
    is_test_account: bool


class AuthStateOut(BaseModel):
    signed_in: bool
    user: UserOut | None
    accounts_exist: bool
    setup_allowed: bool          # no account exists yet and the request comes from this computer
    test_accounts: bool          # test accounts exist (their sign-in details are in a local text file)
    test_accounts_file: str | None  # shown only to requests from this computer
    mission_now: datetime
    test_clock: bool             # mission time has been moved forward by the test bench


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_name: str = Field(default="", max_length=80)


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=10, max_length=256)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    astronaut_id: int | None
    is_test_account: bool
    created_at: datetime
    disabled_at: datetime | None
    last_login_at: datetime | None


class AccountCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=128)
    role: str
    password: str = Field(min_length=10, max_length=256)
    astronaut_id: int | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=10, max_length=256)


def _user_out(p: Principal) -> UserOut:
    return UserOut(id=p.user_id, username=p.username, display_name=p.display_name, role=p.role,
                   astronaut_id=p.astronaut_id, is_test_account=p.is_test_account)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str) -> None:
    # HttpOnly: page scripts cannot read it. SameSite=Strict: never sent on cross-site requests.
    # Not marked Secure because AM is served over plain HTTP on this computer / the habitat LAN.
    response.set_cookie(SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
                        samesite="strict", path="/")


@router.get("/auth/state", response_model=AuthStateOut)
@public
def auth_state(request: Request, db: DB, ctx: CTX, principal: MaybeUser):
    n = count_accounts(db)
    has_test = db.scalars(select(UserAccount.id).where(UserAccount.is_test_account.is_(True)).limit(1)).first()
    local = is_loopback(request)
    notes = ctx.settings.test_accounts_file
    clock = clock_state()
    return AuthStateOut(
        signed_in=principal is not None,
        user=_user_out(principal) if principal else None,
        accounts_exist=n > 0,
        setup_allowed=n == 0 and local,
        test_accounts=has_test is not None,
        test_accounts_file=str(notes) if (has_test is not None and local and notes.is_file()) else None,
        mission_now=clock["mission_now"],
        test_clock=clock["test_clock"],
    )


@router.post("/auth/login", response_model=UserOut)
@public
def login(body: LoginIn, request: Request, response: Response, db: DB, ctx: CTX):
    client = _client(request)
    throttle = ctx.login_throttle
    try:
        throttle.check(client, body.username)
    except Throttled as t:
        raise HTTPException(429, "Too many failed sign-in attempts. Try again later.",
                            headers={"Retry-After": str(t.retry_after_s)}) from None
    user = authenticate(db, body.username, body.password, n_log2=ctx.settings.password_scrypt_n_log2)
    if user is None:
        throttle.failed(client, body.username)
        audit(db, actor="am-auth", action="auth.login_failed", entity="user_account", entity_id=None,
              payload={"username": body.username.strip().lower()[:64], "client": client})
        db.commit()
        raise HTTPException(401, "Wrong username or password.")
    throttle.succeeded(client, body.username)
    token = open_session(db, user, device_name=body.device_name, client_host=client)
    audit(db, actor=user.username, action="auth.login", entity="user_account", entity_id=user.id,
          payload={"client": client, "device": body.device_name[:80]})
    db.commit()
    _set_cookie(response, token)
    return UserOut(id=user.id, username=user.username, display_name=user.display_name, role=user.role,
                   astronaut_id=user.astronaut_id, is_test_account=user.is_test_account)


@router.post("/auth/logout")
@public
def logout(request: Request, response: Response, db: DB):
    if close_session(db, request.cookies.get(SESSION_COOKIE)):
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_in": False}


@router.post("/auth/setup", response_model=UserOut, status_code=201)
@public
def first_run_setup(body: SetupIn, request: Request, response: Response, db: DB, ctx: CTX):
    """Create the first (doctor) account. Only while no account exists, and only from this computer."""
    if count_accounts(db) > 0:
        raise HTTPException(409, "Setup is already complete. Sign in instead.")
    if not is_loopback(request):
        raise HTTPException(403, "First-run setup must be done on the computer that runs AM.")
    try:
        user = create_account(db, username=body.username, display_name=body.display_name, role=ROLE_DOCTOR,
                              password=body.password, n_log2=ctx.settings.password_scrypt_n_log2,
                              actor="first-run-setup")
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    token = open_session(db, user, device_name="setup", client_host=_client(request))
    db.commit()
    _set_cookie(response, token)
    return UserOut(id=user.id, username=user.username, display_name=user.display_name, role=user.role,
                   astronaut_id=None, is_test_account=False)


@router.get("/auth/me", response_model=UserOut)
@signed_in
def me(principal: CurrentUser):
    return _user_out(principal)


# ------------------------------------------------------------------ account management (doctor)
@router.get("/auth/accounts", response_model=list[AccountOut])
@doctor_only
def list_accounts(db: DB):
    return db.scalars(select(UserAccount).order_by(UserAccount.role.desc(), UserAccount.username)).all()


@router.post("/auth/accounts", response_model=AccountOut, status_code=201)
@doctor_only
def add_account(body: AccountCreateIn, db: DB, ctx: CTX, principal: CurrentUser):
    if body.astronaut_id is not None and db.get(Astronaut, body.astronaut_id) is None:
        raise HTTPException(404, "astronaut not found")
    try:
        user = create_account(db, username=body.username, display_name=body.display_name, role=body.role,
                              password=body.password, astronaut_id=body.astronaut_id,
                              n_log2=ctx.settings.password_scrypt_n_log2, actor=principal.username)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return user


def _account(db: Session, account_id: int) -> UserAccount:
    user = db.get(UserAccount, account_id)
    if user is None:
        raise HTTPException(404, "account not found")
    return user


@router.post("/auth/accounts/{account_id}/password", response_model=AccountOut)
@doctor_only
def reset_password(account_id: int, body: PasswordIn, db: DB, ctx: CTX, principal: CurrentUser):
    user = _account(db, account_id)
    try:
        set_password(db, user, body.password, n_log2=ctx.settings.password_scrypt_n_log2, actor=principal.username)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return user


@router.post("/auth/accounts/{account_id}/disable", response_model=AccountOut)
@doctor_only
def disable_account(account_id: int, db: DB, principal: CurrentUser):
    user = _account(db, account_id)
    if user.id == principal.user_id:
        raise HTTPException(409, "You cannot disable the account you are signed in with.")
    set_disabled(db, user, True, actor=principal.username)
    db.commit()
    return user


@router.post("/auth/accounts/{account_id}/enable", response_model=AccountOut)
@doctor_only
def enable_account(account_id: int, db: DB, principal: CurrentUser):
    user = _account(db, account_id)
    set_disabled(db, user, False, actor=principal.username)
    db.commit()
    return user
