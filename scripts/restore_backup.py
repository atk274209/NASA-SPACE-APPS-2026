"""Restore AM's database from an encrypted backup. Command line only, by design.

Stop the AM backend first. With --confirm the script:
  1. decrypts the backup in memory and runs PRAGMA integrity_check on it,
  2. copies the current database with SQLite's backup API to <data dir>/pre-restore/,
  3. folds the current write-ahead log into the database file and replaces the file
     (an old -wal file left next to the restored database would be replayed into it),
  4. records who restored which backup, and why, in the restored database's audit log.
Without --confirm nothing is read or changed; the script only prints what it would do.

The passphrase is read from the terminal (or one line of stdin with --passphrase-stdin),
never from the command line, where it would end up in shell history and process lists.

Usage, from the backend folder:
  uv run python ../scripts/restore_backup.py <file.ambk> --actor "Name" --reason "Why" --confirm
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy.engine import make_url  # noqa: E402

from am.admin.backup import check_database_bytes, copy_database_file  # noqa: E402
from am.admin.crypto import DecryptionError, decrypt_bytes  # noqa: E402
from am.admin.files import sha256_file  # noqa: E402
from am.audit import audit  # noqa: E402
from am.config import Settings  # noqa: E402
from am.db.base import make_engine, make_session_factory  # noqa: E402


class RestoreError(RuntimeError):
    """The restore stopped before the current database was replaced."""


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Restore the AM database from an encrypted backup (.ambk).")
    p.add_argument("backup", type=Path, help="the encrypted backup file")
    p.add_argument("--actor", required=True, help="your name, recorded in the audit log")
    p.add_argument("--reason", required=True, help="why the database is being restored")
    p.add_argument("--confirm", action="store_true", help="actually restore (without it nothing changes)")
    p.add_argument("--passphrase-stdin", action="store_true",
                   help="read the passphrase from the first line of standard input instead of the terminal")
    p.add_argument("--data-dir", type=Path, help="data folder (default: AM_DATA_DIR or the AM default)")
    p.add_argument("--database-url", help="database URL (default: AM_DATABASE_URL or the file in the data folder)")
    return p.parse_args(argv)


def _target(settings: Settings) -> Path:
    url = make_url(settings.resolved_database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RestoreError("restore supports a SQLite database file only")
    return Path(url.database)


def _recorded_backup(db: Path, digest: str) -> str:
    """Describe the backup record matching this file's SHA-256 in the current database, if any."""
    if not db.exists():
        return "No current database; the backup cannot be matched to a record."
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT id, created_at, authorized_by FROM sync_package WHERE kind = 'backup' "
                           "AND sha256 = ? ORDER BY id DESC LIMIT 1", (digest,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        conn.close()
    if row is None:
        return "This file is not recorded in the current database (for example, a backup from another installation)."
    return f"Matches backup {row[0]}, created {row[1]} UTC by {row[2]}."


def _read_passphrase(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass("Backup passphrase: ")


def _fold_wal(db: Path) -> None:
    """Move everything from the write-ahead log into the database file before it is replaced."""
    conn = sqlite3.connect(str(db))
    try:
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    finally:
        conn.close()
    if busy:
        raise RestoreError("the database is in use; stop the AM backend and try again")


def _replace(db: Path, plain: bytes) -> None:
    tmp = db.with_name(db.name + ".restoring")
    with tmp.open("wb") as f:
        f.write(plain)
        f.flush()
        os.fsync(f.fileno())
    try:
        for suffix in ("-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
        os.replace(tmp, db)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RestoreError(f"could not replace the database file ({exc.__class__.__name__}); stop the AM backend "
                           "and try again") from exc


def _record(url: str, *, actor: str, reason: str, source: Path, digest: str, revision: str | None,
            pre: Path | None) -> None:
    engine = make_engine(url)
    try:
        with make_session_factory(engine)() as s:
            audit(s, actor=actor, action="backup.restored", entity="database",
                  payload={"backup_file": source.name, "backup_sha256": digest, "backup_alembic_revision": revision,
                           "pre_restore_copy": None if pre is None else str(pre), "reason": reason})
            s.commit()
    finally:
        engine.dispose()


def restore(args: argparse.Namespace) -> int:
    overrides: dict[str, object] = {}
    if args.data_dir:
        overrides["data_dir"] = args.data_dir
    if args.database_url:
        overrides["database_url"] = args.database_url
    settings = Settings(**overrides)
    target = _target(settings)
    source = args.backup.resolve()
    if not source.is_file():
        raise RestoreError(f"backup file not found: {source}")
    digest = sha256_file(source)
    pre_dir = settings.data_dir / "pre-restore"
    print(f"Backup file:      {source}")
    print(f"Backup SHA-256:   {digest}")
    print(f"                  {_recorded_backup(target, digest)}")
    print(f"Database to replace: {target}")
    print(f"Copy of the current database goes to: {pre_dir}")
    if not args.confirm:
        print("Nothing was changed. Run again with --confirm to restore.")
        return 2

    try:
        plain = decrypt_bytes(source.read_bytes(), _read_passphrase(args.passphrase_stdin))
    except DecryptionError as exc:
        raise RestoreError(str(exc)) from exc
    check = check_database_bytes(plain)
    if not check.integrity_ok:
        raise RestoreError(f"the backup failed the integrity check: {'; '.join(check.integrity[:3])}")
    if check.alembic_revision is None:
        raise RestoreError("the backup is not an AM database (no schema revision)")

    pre = None
    if target.exists():
        pre_dir.mkdir(parents=True, exist_ok=True)
        pre = pre_dir / f"am-pre-restore-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
        copy_database_file(target, pre)
        print(f"Copied the current database to {pre}")
        _fold_wal(target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    _replace(target, plain)
    print(f"Restored {source.name} (schema revision {check.alembic_revision}) into {target}.")
    try:
        _record(settings.resolved_database_url, actor=args.actor.strip(), reason=args.reason.strip(), source=source,
                digest=digest, revision=check.alembic_revision, pre=pre)
    except Exception as exc:  # noqa: BLE001 - the restore itself succeeded; say so instead of failing
        print(f"Warning: the restore succeeded but its audit entry could not be written ({exc.__class__.__name__}).",
              file=sys.stderr)
    print("Start the AM backend; it applies any newer database migrations at startup.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if not args.actor.strip() or len(args.reason.strip()) < 3:
        print("Restore refused: --actor must name a person and --reason needs at least 3 characters.", file=sys.stderr)
        return 2
    try:
        return restore(args)
    except (RestoreError, sqlite3.Error) as exc:
        print(f"Restore aborted: {exc}. The current database was not replaced.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
