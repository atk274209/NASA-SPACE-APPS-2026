"""Settings, diagnostics, encrypted backup, authorized ground-report outbox (owner: feature F6)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import AfterValidator, BaseModel, Field, SecretStr, StringConstraints
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from am.admin import backup, diagnostics, outbox, privacy, settings_api
from am.admin.crypto import MIN_PASSPHRASE_CHARS
from am.auth.guard import doctor_only, signed_in
from am.api.deps import AppContext, get_ctx, get_db
from am.db.models import AuditLog
from am.schemas import AuditOut

log = logging.getLogger(__name__)


class RedactedValidationRoute(APIRoute):
    """422 responses on this router never repeat the submitted values.

    FastAPI's default validation error echoes each offending input, and for a missing field
    the whole request body. Backup and report requests carry passphrases, so only the error
    type, location and message are returned.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def redacting_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                detail = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
                          for e in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})

        return redacting_handler


router = APIRouter(tags=["admin"], route_class=RedactedValidationRoute)
DB = Annotated[Session, Depends(get_db)]
CTX = Annotated[AppContext, Depends(get_ctx)]

Actor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def _long_enough(v: SecretStr) -> SecretStr:
    if len(v.get_secret_value()) < MIN_PASSPHRASE_CHARS:
        raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE_CHARS} characters")
    return v


# SecretStr keeps the value out of reprs and logs; the validator avoids echoing it in errors.
NewPassphrase = Annotated[SecretStr, Field(max_length=1024), AfterValidator(_long_enough)]
Section = Literal["alerts", "journal", "observations", "symptoms", "checkins", "missions"]


# ------------------------------------------------------------------ schemas
class SettingOut(BaseModel):
    key: str
    kind: str
    value: Any
    default: Any
    effective: Any
    deployment_default: str | None  # model settings: the model used when the value is empty
    is_default: bool
    description: str
    min_value: int | None
    max_value: int | None
    updated_by: str | None
    updated_at: datetime | None


class SettingIn(BaseModel):
    value: Any
    actor: Actor


class DatabaseDiag(BaseModel):
    engine: str
    path: str | None
    size_bytes: int | None
    wal_bytes: int
    alembic_revision: str | None
    alembic_head: str | None
    at_head: bool
    row_counts: dict[str, int]


class StorageDiag(BaseModel):
    data_dir: str
    backups_dir: str
    outbox_dir: str
    free_bytes: int
    total_bytes: int
    backups_bytes: int
    outbox_bytes: int


class OllamaDiag(BaseModel):
    url: str
    reachable: bool
    reason: str | None
    installed_models: list[str]
    chat_model: str
    chat_model_source: str
    chat_model_installed: bool | None
    embed_model: str
    embed_model_source: str
    embed_model_installed: bool | None


class EmbeddingDiag(BaseModel):
    embed_model: str
    chunks_total: int
    chunks_current_model: int
    chunks_other_model: int
    chunks_without_embedding: int
    coverage_percent: float | None
    search_method: str


class BackupDiag(BaseModel):
    last_backup_at: datetime | None
    last_backup_id: int | None
    stored: int
    retention: int


class VersionsDiag(BaseModel):
    app: str
    python: str
    sqlite: str
    sqlalchemy: str
    fastapi: str
    os: str
    thresholds_version: str
    thresholds_label: str
    baseline_policy_version: str


class DiagnosticsOut(BaseModel):
    generated_at: datetime
    database: DatabaseDiag
    storage: StorageDiag
    ollama: OllamaDiag
    embeddings: EmbeddingDiag
    backups: BackupDiag
    versions: VersionsDiag


class StoreOut(BaseModel):
    key: str
    what: str
    where: str
    protection: str


class ModelServiceOut(BaseModel):
    url: str
    host: str
    is_local: bool
    note: str


class PrivacyOut(BaseModel):
    stores: list[StoreOut]
    model_service: ModelServiceOut
    transmission: str
    warnings: list[str]
    counts: dict[str, int]


class BackupIn(BaseModel):
    passphrase: NewPassphrase
    actor: Actor
    reason: Reason


class BackupOut(BaseModel):
    id: int
    created_at: datetime
    authorized_by: str
    reason: str
    status: str
    file_name: str | None
    location: str | None
    file_present: bool
    sha256: str | None
    size_bytes: int | None
    plaintext_bytes: int | None
    encrypted: bool
    cipher: str | None
    kdf: dict[str, Any]
    alembic_revision: str | None
    pruned_backup_ids: list[int] = []


class VerifyIn(BaseModel):
    passphrase: SecretStr = Field(min_length=1, max_length=1024)
    actor: Actor = "unnamed operator"


class VerifyOut(BaseModel):
    backup_id: int
    ok: bool
    checksum_ok: bool
    decrypted: bool
    integrity: list[str]
    alembic_revision: str | None
    tables: int | None
    message: str
    verified_at: datetime


class GroundReportIn(BaseModel):
    astronaut_id: int
    first_day: int = Field(ge=1)
    last_day: int = Field(ge=1)
    sections: list[Section] = Field(min_length=1, max_length=len(outbox.SECTIONS))
    authorized_by: Actor
    reason: Reason
    encrypt: bool = False
    passphrase: NewPassphrase | None = None


class PackageFileOut(BaseModel):
    name: str
    media_type: str
    sha256: str
    size_bytes: int


class PackageOut(BaseModel):
    id: int
    kind: str
    astronaut_id: int | None
    created_at: datetime
    authorized_by: str
    reason: str
    status: str
    encrypted: bool
    sha256: str | None
    size_bytes: int | None
    location: str | None
    first_day: int | None
    last_day: int | None
    sections: list[str]
    files: list[PackageFileOut]
    record_counts: dict[str, int]
    simulated: bool
    thresholds_label: str | None
    exported_by: str | None
    exported_at: datetime | None


class MarkExportedIn(BaseModel):
    actor: Actor


# ------------------------------------------------------------------ settings
@router.get("/settings", response_model=list[SettingOut])
@doctor_only
def list_settings(db: DB, ctx: CTX):
    return settings_api.describe_all(db, ctx.settings)


@router.put("/settings/{key}", response_model=SettingOut)
@doctor_only
def update_setting(key: str, body: SettingIn, db: DB, ctx: CTX):
    try:
        out = settings_api.update(db, ctx.settings, ctx.llm, key, body.value, body.actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except settings_api.ModelServiceOffline as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return out


# ------------------------------------------------------------------ diagnostics and privacy
@router.get("/admin/diagnostics", response_model=DiagnosticsOut)
@doctor_only
def get_diagnostics(request: Request, db: DB, ctx: CTX):
    return diagnostics.collect(db, ctx, request.app.version)


@router.get("/admin/privacy", response_model=PrivacyOut)
@signed_in
def get_privacy(db: DB, ctx: CTX):
    return privacy.inventory(db, ctx.settings, ctx.engine)


ADMIN_AUDIT_PREFIXES = ("setting.", "backup.", "outbox.")


@router.get("/admin/audit", response_model=list[AuditOut])
@doctor_only
def admin_audit(db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    """Audit entries for settings, backups and ground-report packages (the data-handling trail)."""
    stmt = select(AuditLog).where(or_(*(AuditLog.action.startswith(p) for p in ADMIN_AUDIT_PREFIXES)))
    return db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit)).all()


# ------------------------------------------------------------------ encrypted backups
@router.post("/admin/backups", response_model=BackupOut, status_code=201)
@doctor_only
def create_backup(body: BackupIn, db: DB, ctx: CTX):
    try:
        result = backup.create_backup(db, ctx.engine, ctx.settings, passphrase=body.passphrase.get_secret_value(),
                                      actor=body.actor, reason=body.reason)
    except backup.BackupUnavailable as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    try:
        db.commit()
    except Exception:
        db.rollback()
        backup.discard_file(ctx.settings, result.package)
        raise
    for problem in backup.delete_pruned_files(ctx.settings, result.pruned):
        log.warning("pruned backup file could not be deleted: %s", problem)
    out = BackupOut(**backup.backup_summary(ctx.settings, result.package))
    out.pruned_backup_ids = [p.id for p in result.pruned]
    return out


@router.get("/admin/backups", response_model=list[BackupOut])
@doctor_only
def list_backups(db: DB, ctx: CTX):
    return [backup.backup_summary(ctx.settings, p) for p in backup.list_backups(db)]


@router.post("/admin/backups/{backup_id}/verify", response_model=VerifyOut)
@doctor_only
def verify_backup(backup_id: int, body: VerifyIn, db: DB, ctx: CTX):
    try:
        result = backup.verify_backup(db, ctx.settings, backup_id, passphrase=body.passphrase.get_secret_value(),
                                      actor=body.actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except backup.BackupFileMissing as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return VerifyOut(**result.__dict__)


# ------------------------------------------------------------------ ground-report outbox
@router.post("/admin/outbox/ground-report", response_model=PackageOut, status_code=201)
@doctor_only
def create_ground_report(body: GroundReportIn, db: DB, ctx: CTX):
    req = outbox.ReportRequest(astronaut_id=body.astronaut_id, first_day=body.first_day, last_day=body.last_day,
                               sections=tuple(body.sections), authorized_by=body.authorized_by, reason=body.reason)
    try:
        pkg = outbox.prepare_ground_report(
            db, ctx.settings, ctx.thresholds, req, encrypt=body.encrypt,
            passphrase=None if body.passphrase is None else body.passphrase.get_secret_value())
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    try:
        db.commit()
    except Exception:
        db.rollback()
        outbox.discard_files(ctx.settings, pkg)
        raise
    return outbox.package_summary(ctx.settings, pkg, None)


@router.get("/admin/outbox", response_model=list[PackageOut])
@doctor_only
def list_outbox(db: DB, ctx: CTX, astronaut_id: int | None = None):
    packages = outbox.list_packages(db, astronaut_id)
    exported = outbox.export_records(db, [p.id for p in packages])
    return [outbox.package_summary(ctx.settings, p, exported.get(p.id)) for p in packages]


@router.get("/admin/outbox/{package_id}/download", response_class=FileResponse)
@doctor_only
def download_package(
    package_id: int,
    db: DB,
    ctx: CTX,
    file: str | None = None,
    x_am_actor: Annotated[str | None, Header(max_length=400)] = None,
):
    """Return a package file (the JSON by default; `file` selects another file of the package).

    The download is audited. The person's name comes from the X-AM-Actor header,
    percent-encoded UTF-8, so it stays out of URLs and server access logs.
    """
    try:
        pkg = outbox.get_package(db, package_id)
        path, entry = outbox.package_file(ctx.settings, pkg, file)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except outbox.PackageFileChanged as exc:
        raise HTTPException(409, str(exc)) from exc
    actor = unquote(x_am_actor or "").strip()[:64] or "unnamed operator"
    outbox.record_download(db, pkg, entry, actor)
    db.commit()
    return FileResponse(path, media_type=entry["media_type"], filename=entry["name"])


@router.post("/admin/outbox/{package_id}/mark-exported", response_model=PackageOut)
@doctor_only
def mark_exported(package_id: int, body: MarkExportedIn, db: DB, ctx: CTX):
    try:
        pkg = outbox.mark_exported(db, package_id, body.actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return outbox.package_summary(ctx.settings, pkg, outbox.export_records(db, [pkg.id]).get(pkg.id))
