"""Encrypted database backups.

A backup is a consistent snapshot taken with SQLite's online backup API into memory,
sealed in the AMBK1 container (am.admin.crypto: AES-256-GCM, scrypt key) and written to
data_dir/backups/. The plaintext snapshot never touches the disk, and verification also
decrypts in memory. Each backup is recorded as a SyncPackage (kind 'backup') with the
SHA-256 of the encrypted file, plus an audit entry. The passphrase is never stored, logged
or returned.

Restore is deliberately not offered over HTTP: scripts/restore_backup.py does it from the
command line with --confirm, after copying the current database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from am.admin.crypto import FORMAT_DESCRIPTION, DecryptionError, decrypt_bytes, encrypt_bytes
from am.admin.files import atomic_write, resolve_in_data_dir, sha256_bytes
from am.audit import audit
from am.config import Settings
from am.db.base import utcnow
from am.db.models_m5 import SyncPackage
from am.settings_store import get_setting

KIND = "backup"
STATUS_STORED = "stored"  # encrypted file present in data_dir/backups
STATUS_PRUNED = "pruned"  # file deleted by the retention setting; the record stays for the history
FILE_SUFFIX = ".ambk"


class BackupUnavailable(RuntimeError):
    """This deployment's database cannot be backed up (not a SQLite file)."""


class BackupFileMissing(RuntimeError):
    """The backup record exists but its file is gone (pruned or moved)."""


def backups_dir(settings: Settings) -> Path:
    return settings.data_dir / "backups"


def sqlite_file(engine: Engine) -> Path:
    if engine.url.get_backend_name() != "sqlite":
        raise BackupUnavailable("encrypted backups support SQLite databases only")
    database = engine.url.database
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        raise BackupUnavailable("an in-memory database cannot be backed up")
    return Path(database)


def snapshot_bytes(engine: Engine) -> bytes:
    """Consistent copy of the live database, taken with the SQLite online backup API.

    The copy goes into an in-memory database and is serialized from there, so no plaintext
    copy of the health records is ever written to disk.
    """
    sqlite_file(engine)
    raw = engine.raw_connection()
    try:
        source = raw.driver_connection
        if not isinstance(source, sqlite3.Connection):
            raise BackupUnavailable("the database driver does not expose the SQLite backup API")
        memory = sqlite3.connect(":memory:")
        try:
            source.backup(memory)
            return memory.serialize()
        finally:
            memory.close()
    finally:
        raw.close()


@dataclass
class DatabaseCheck:
    integrity: list[str]
    alembic_revision: str | None
    tables: int

    @property
    def integrity_ok(self) -> bool:
        return self.integrity == ["ok"]


def check_database_bytes(data: bytes) -> DatabaseCheck:
    """Run PRAGMA integrity_check on a serialized database without writing it to disk."""
    image = bytearray(data)
    if len(image) >= 20 and image[:16] == b"SQLite format 3\x00":
        # Bytes 18-19 = 2 mark a WAL-mode file, which an in-memory database cannot open
        # (documented sqlite3_deserialize limitation). Setting them to 1 (rollback journal)
        # changes only the journal mode of this temporary copy, not its content.
        image[18] = image[19] = 1
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.deserialize(bytes(image))
            rows = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            return DatabaseCheck([f"not a readable SQLite database: {exc}"], None, 0)
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = None if row is None else str(row[0])
        except sqlite3.DatabaseError:
            revision = None
        return DatabaseCheck(rows, revision, int(tables))
    finally:
        conn.close()


def alembic_revision(session: Session) -> str | None:
    try:
        return session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:  # a database created without Alembic simply has no revision
        return None


@dataclass
class BackupResult:
    package: SyncPackage
    pruned: list[SyncPackage] = field(default_factory=list)


def create_backup(session: Session, engine: Engine, settings: Settings, *, passphrase: str, actor: str,
                  reason: str) -> BackupResult:
    """Snapshot, encrypt and record a backup; mark older ones for pruning.

    Old backup files are only deleted by delete_pruned_files() after the caller commits,
    so a failed transaction can never lose a backup that the database still lists.
    """
    plain = snapshot_bytes(engine)
    sealed = encrypt_bytes(plain, passphrase)
    revision = alembic_revision(session)
    created = utcnow()
    pkg = SyncPackage(astronaut_id=None, kind=KIND, authorized_by=actor, reason=reason, encrypted=True,
                      status=STATUS_STORED, created_at=created,
                      scope={**FORMAT_DESCRIPTION, "plaintext_bytes": len(plain), "alembic_revision": revision})
    session.add(pkg)
    session.flush()  # the id goes into the file name

    folder = backups_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"am-backup-{created:%Y%m%dT%H%M%SZ}-{pkg.id:05d}{FILE_SUFFIX}"
    atomic_write(path, sealed)
    try:
        pkg.file_path = path.relative_to(settings.data_dir).as_posix()
        pkg.sha256 = sha256_bytes(sealed)
        pkg.size_bytes = len(sealed)
        audit(session, actor=actor, action="backup.created", entity="sync_package", entity_id=pkg.id,
              payload={"file": path.name, "sha256": pkg.sha256, "size_bytes": pkg.size_bytes, "reason": reason,
                       "cipher": FORMAT_DESCRIPTION["cipher"], "kdf": FORMAT_DESCRIPTION["kdf"],
                       "alembic_revision": revision})
        pruned = _mark_pruned(session, keep=int(get_setting(session, "backup_retention")), actor=actor)
        session.flush()
    except Exception:
        path.unlink(missing_ok=True)  # never leave a backup file the database does not list
        raise
    return BackupResult(pkg, pruned)


def _mark_pruned(session: Session, *, keep: int, actor: str) -> list[SyncPackage]:
    stored = session.scalars(select(SyncPackage).where(SyncPackage.kind == KIND, SyncPackage.status == STATUS_STORED)
                             .order_by(SyncPackage.id.desc())).all()
    out = []
    for pkg in stored[max(keep, 1):]:
        pkg.status = STATUS_PRUNED
        audit(session, actor=actor, action="backup.pruned", entity="sync_package", entity_id=pkg.id,
              payload={"file": Path(pkg.file_path or "").name, "retention": keep})
        out.append(pkg)
    return out


def delete_pruned_files(settings: Settings, pruned: list[SyncPackage]) -> list[str]:
    """Delete the files of pruned backups. Returns problems (a locked file is left in place)."""
    problems = []
    for pkg in pruned:
        if not pkg.file_path:
            continue
        try:
            resolve_in_data_dir(settings, pkg.file_path).unlink(missing_ok=True)
        except (OSError, LookupError) as exc:
            problems.append(f"{pkg.file_path}: {exc.__class__.__name__}")
    return problems


def discard_file(settings: Settings, pkg: SyncPackage) -> None:
    """Remove the file of a backup whose transaction failed, so no unrecorded file remains."""
    if pkg.file_path:
        try:
            resolve_in_data_dir(settings, pkg.file_path).unlink(missing_ok=True)
        except (OSError, LookupError):
            pass


def list_backups(session: Session) -> list[SyncPackage]:
    return list(session.scalars(select(SyncPackage).where(SyncPackage.kind == KIND)
                                .order_by(SyncPackage.id.desc())).all())


def last_backup(session: Session) -> SyncPackage | None:
    return session.scalars(select(SyncPackage).where(SyncPackage.kind == KIND)
                           .order_by(SyncPackage.id.desc()).limit(1)).first()


@dataclass
class VerifyResult:
    backup_id: int
    ok: bool
    checksum_ok: bool
    decrypted: bool
    integrity: list[str]
    alembic_revision: str | None
    tables: int | None
    message: str
    verified_at: datetime


def verify_backup(session: Session, settings: Settings, backup_id: int, *, passphrase: str,
                  actor: str) -> VerifyResult:
    """Decrypt a backup in memory and run PRAGMA integrity_check on it.

    The SHA-256 recorded at creation separates the two decryption failures GCM cannot tell
    apart: an unchanged file that fails to decrypt means a wrong passphrase.
    """
    pkg = session.get(SyncPackage, backup_id)
    if pkg is None or pkg.kind != KIND:
        raise LookupError("backup not found")
    if pkg.status != STATUS_STORED or not pkg.file_path:
        raise BackupFileMissing(f"backup {backup_id} was removed by the retention setting")
    path = resolve_in_data_dir(settings, pkg.file_path)
    if not path.is_file():
        raise BackupFileMissing(f"backup file {path.name} is missing from {path.parent}")
    sealed = path.read_bytes()
    checksum_ok = sha256_bytes(sealed) == pkg.sha256
    check: DatabaseCheck | None = None
    try:
        check = check_database_bytes(decrypt_bytes(sealed, passphrase))
    except DecryptionError as exc:
        if exc.reason == "auth_failed" and checksum_ok:
            message = "Wrong passphrase: the file is unchanged since it was written, but it does not decrypt."
        elif exc.reason == "auth_failed":
            message = "The file has changed since it was written (checksum mismatch) and does not decrypt."
        else:
            message = f"Not a readable backup: {exc}"
    else:
        if not check.integrity_ok:
            message = "Decrypted, but the database integrity check reported problems."
        elif check.alembic_revision is None:
            message = "Decrypted, but this is not an AMC database (no schema revision)."
        elif not checksum_ok:
            message = "Decrypted and intact, but the file checksum differs from the one recorded."
        else:
            message = "Backup decrypts and the database integrity check passed."
    ok = check is not None and check.integrity_ok and check.alembic_revision is not None and checksum_ok
    result = VerifyResult(backup_id=pkg.id, ok=ok, checksum_ok=checksum_ok, decrypted=check is not None,
                          integrity=[] if check is None else check.integrity[:20],
                          alembic_revision=None if check is None else check.alembic_revision,
                          tables=None if check is None else check.tables, message=message, verified_at=utcnow())
    audit(session, actor=actor, action="backup.verified", entity="sync_package", entity_id=pkg.id,
          payload={"ok": ok, "checksum_ok": checksum_ok, "decrypted": result.decrypted,
                   "integrity_ok": check is not None and check.integrity_ok})
    session.flush()
    return result


def copy_database_file(source: Path, target: Path) -> None:
    """Consistent file-to-file copy with the SQLite backup API (includes pending WAL content)."""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def backup_summary(settings: Settings, pkg: SyncPackage) -> dict[str, Any]:
    """API view of a backup record, with the file location resolved against the current data folder."""
    scope = pkg.scope or {}
    location = None
    present = False
    if pkg.file_path:
        try:
            p = resolve_in_data_dir(settings, pkg.file_path)
            location, present = str(p), p.is_file()
        except LookupError:
            pass
    return {
        "id": pkg.id,
        "created_at": pkg.created_at,
        "authorized_by": pkg.authorized_by,
        "reason": pkg.reason,
        "status": pkg.status,
        "file_name": Path(pkg.file_path).name if pkg.file_path else None,
        "location": location,
        "file_present": present,
        "sha256": pkg.sha256,
        "size_bytes": pkg.size_bytes,
        "plaintext_bytes": scope.get("plaintext_bytes"),
        "encrypted": pkg.encrypted,
        "cipher": scope.get("cipher"),
        "kdf": scope.get("kdf") or {},
        "alembic_revision": scope.get("alembic_revision"),
    }
