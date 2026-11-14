"""What AM stores where, and how data can leave the device.

Built from the running configuration (paths, model service URL) and stored records, so the
privacy page describes this installation, not an ideal one. Where a setting weakens the
default (a data folder inside the source tree, a model service on another host) the
inventory says so plainly.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from am.admin import backup, outbox
from am.config import REPO_ROOT, Settings
from am.db.models_m5 import SyncPackage

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TRANSMISSION = ("AM has no function that uploads or synchronises data to ground. Health data leaves this device only "
                "as a ground-report package that a named person authorized, and a person moves that file over the "
                "mission's approved link. Preparing, downloading and marking a package as exported are each recorded "
                "in the audit trail.")


def _count(session: Session, kind: str, status: str) -> int:
    return int(session.scalar(select(func.count()).select_from(SyncPackage).where(
        SyncPackage.kind == kind, SyncPackage.status == status)) or 0)


def inventory(session: Session, settings: Settings, engine: Engine) -> dict[str, Any]:
    try:
        db_where = str(backup.sqlite_file(engine))
    except backup.BackupUnavailable:
        db_where = engine.url.render_as_string(hide_password=True)
    stores = [
        {"key": "database", "where": db_where,
         "what": "Health and mission records: measurements, baselines, alerts, check-ins, symptoms, journal, "
                 "conversations with the companion, settings and the audit trail.",
         "protection": "Local SQLite file. AM does not encrypt it at rest; protect the device with disk encryption."},
        {"key": "backups", "where": str(backup.backups_dir(settings)),
         "what": "Encrypted database backups.",
         "protection": "AES-256-GCM with a key derived from your passphrase (scrypt). The passphrase is never stored."},
        {"key": "outbox", "where": str(outbox.outbox_dir(settings)),
         "what": "Ground-report packages: only the sections and days a named person selected.",
         "protection": "Plain JSON and Markdown, or encrypted like a backup when requested."},
        {"key": "pre_restore", "where": str(settings.data_dir / "pre-restore"),
         "what": "Copies of the database made by the restore command just before it replaces the database.",
         "protection": "Plain SQLite files, like the database itself."},
    ]
    host = urlsplit(settings.ollama_url).hostname or ""
    local = host in LOOPBACK_HOSTS
    warnings = []
    if settings.data_dir.resolve().is_relative_to(REPO_ROOT.resolve()):
        warnings.append("The data folder is inside the source folder. If that folder is synchronised to a cloud "
                        "service, health records are copied there too.")
    if not local:
        warnings.append(f"The language model service is configured on {host or 'an unknown host'}, not on this "
                        "device. Chat messages and the records shown to the model are sent there.")
    return {
        "stores": stores,
        "model_service": {
            "url": settings.ollama_url, "host": host, "is_local": local,
            "note": ("The language model runs on this device; conversations do not leave it." if local else
                     "The language model service is on another host; conversations are sent to it."),
        },
        "transmission": TRANSMISSION,
        "warnings": warnings,
        "counts": {
            "ground_reports_prepared": _count(session, outbox.KIND, outbox.STATUS_PREPARED),
            "ground_reports_exported": _count(session, outbox.KIND, outbox.STATUS_EXPORTED),
            "backups_stored": _count(session, backup.KIND, backup.STATUS_STORED),
        },
    }
