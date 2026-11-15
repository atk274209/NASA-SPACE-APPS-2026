"""Test crew: five FICTIONAL astronauts and one doctor account (master prompt v1, Module J).

Everything here is invented for testing. The names, profiles and medical histories describe
no real person. AM derives no threshold from a listed condition; the history exists so that
person-specific features (symptom context, the doctor's patient view) can be tested.

Passwords are generated at random and written only to the plain-text TEST_CREDENTIALS.txt file
at the repository root (gitignored). The database stores only argon2id hashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from am.audit import audit
from am.auth.passwords import generate_password
from am.auth.service import ROLE_ASTRONAUT, ROLE_DOCTOR, create_account, set_password
from am.db.base import utcnow
from am.db.models import Astronaut, Mission
from am.db.models_m6 import UserAccount
from am.db.models_m6a import AstronautProfile

MISSION_NAME = "AM-TEST-1 (SIMULATED test mission)"
MISSION_ENVIRONMENT = "deep_space_transit"
MISSION_PLANNED_DAYS = 180


@dataclass(frozen=True)
class CrewMember:
    code: str
    username: str
    name: str
    callsign: str
    crew_role: str
    age_years: int
    bio: str
    medical_history: tuple[dict[str, Any], ...] = ()
    chronic_conditions: tuple[str, ...] = ()
    allergies: tuple[str, ...] = ()


TEST_CREW: tuple[CrewMember, ...] = (
    CrewMember(
        "AST-001", "ast001", "Lena Varga", "VEGA", "Commander", 44,
        "FICTIONAL test profile. Former test pilot; second long-duration mission.",
        medical_history=({"condition": "Kidney stone, single episode", "status": "resolved", "since": "2019",
                          "note": "Fictional history for testing kidney-stone-like symptom intake."},),
    ),
    CrewMember(
        "AST-002", "ast002", "Tomas Reyes", "ROOK", "Flight engineer", 38,
        "FICTIONAL test profile. Systems engineer; first long-duration mission.",
        medical_history=({"condition": "Migraine without aura", "status": "monitoring", "since": "2015",
                          "note": "Fictional history for testing headache reports against a known condition."},),
        chronic_conditions=("Migraine",),
        allergies=("Penicillin",),
    ),
    CrewMember(
        "AST-003", "ast003", "Aiko Brandt", "KITE", "Mission specialist (life sciences)", 35,
        "FICTIONAL test profile. Biologist; runs the onboard laboratory.",
        medical_history=({"condition": "Seasonal allergic rhinitis", "status": "active", "since": "2008",
                          "note": "Fictional history for testing respiratory symptom context."},),
        chronic_conditions=("Allergic rhinitis",),
    ),
    CrewMember(
        "AST-004", "ast004", "Samuel Idowu", "ORCA", "Mission specialist (EVA)", 41,
        "FICTIONAL test profile. Geologist and EVA lead.",
        medical_history=({"condition": "Lower back pain after a training injury", "status": "monitoring",
                          "since": "2021", "note": "Fictional history for testing musculoskeletal complaints."},),
        chronic_conditions=("Recurrent lower back pain",),
    ),
    CrewMember(
        "AST-005", "ast005", "Nadia Kerr", "WREN", "Payload specialist", 33,
        "FICTIONAL test profile. Materials scientist; first spaceflight.",
        medical_history=({"condition": "Irritable bowel syndrome", "status": "active", "since": "2017",
                          "note": "Fictional history for testing gastrointestinal flare-up reports."},),
        chronic_conditions=("Irritable bowel syndrome",),
        allergies=("Shellfish",),
    ),
)

DOCTOR_USERNAME = "doctor"
DOCTOR_NAME = "Dr. Elif Arman (flight surgeon, FICTIONAL)"

CREDENTIALS_HEADER = """\
=====================================================================
 AM TEST CREDENTIALS - FOR LOCAL TESTING ONLY
=====================================================================
 These accounts belong to the FICTIONAL test crew of the AM prototype.
 - Never reuse these passwords anywhere else.
 - This file is gitignored; do not commit, sync or share it.
 - The database stores only argon2id hashes, never these passwords.
 - Regenerate with: uv run python -m am.cli seed-crew --reset-passwords
=====================================================================
"""


@dataclass
class SeedReport:
    created_accounts: list[str] = field(default_factory=list)
    reset_accounts: list[str] = field(default_factory=list)
    credentials: list[tuple[str, str, str, str]] = field(default_factory=list)  # username, password, role, name
    credentials_file: Path | None = None
    skipped: str | None = None


class SeedError(Exception):
    """The crew cannot be seeded as asked; the message says what to do instead."""


def _profile(s: Session, a: Astronaut, m: CrewMember) -> None:
    p = s.get(AstronautProfile, a.id) or AstronautProfile(astronaut_id=a.id)
    p.callsign, p.crew_role, p.age_years, p.bio = m.callsign, m.crew_role, m.age_years, m.bio
    p.medical_history = [dict(h) for h in m.medical_history]
    p.chronic_conditions = list(m.chronic_conditions)
    p.allergies = list(m.allergies)
    p.is_fictional = True
    s.add(p)


def _astronaut(s: Session, m: CrewMember) -> Astronaut:
    a = s.scalars(select(Astronaut).where(Astronaut.code == m.code)).first()
    if a is None:
        a = Astronaut(code=m.code, display_name=m.name, is_simulated=True)
        s.add(a)
        s.flush()
        s.add(Mission(name=MISSION_NAME, astronaut_id=a.id, start_date=utcnow().date(),
                      planned_days=MISSION_PLANNED_DAYS, environment=MISSION_ENVIRONMENT, is_simulated=True))
    _profile(s, a, m)
    s.flush()
    return a


def write_credentials(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    lines = [CREDENTIALS_HEADER, f"{'USERNAME':<10} {'PASSWORD':<16} {'ROLE':<10} NAME"]
    lines += [f"{u:<10} {pw:<16} {role:<10} {name}" for u, pw, role, name in rows]
    lines += ["", "Main app:     http://localhost:5173", "Test Console: http://localhost:5174 (needs AM_TEST_MODE=1)", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def simple_password(username: str) -> str:
    """Easy-to-type TEST password ("ast001-test"). Only for the fictional local test crew."""
    return f"{username}-test"


def seed_crew(s: Session, *, credentials_file: Path, reset_passwords: bool = False,
              simple_passwords: bool = False, profile: str = "standard", actor: str = "am-cli") -> SeedReport:
    """Create the fictional test crew and the doctor account.

    simple_passwords: use "<username>-test" instead of random passwords, so testers can type
    them from memory. They are still stored only as argon2id hashes.

    - No test account exists yet: create everything and write the credentials file.
    - All exist: do nothing unless reset_passwords, which issues new passwords and rewrites the file.
    - Some exist: refuse unless reset_passwords (the file could not list the existing passwords).
    The caller commits, then calls `write_credentials(rep.credentials_file, rep.credentials)`, so the
    file never lists a password the database does not hold.
    """
    usernames = [m.username for m in TEST_CREW] + [DOCTOR_USERNAME]
    existing = {u.username: u for u in s.scalars(select(UserAccount).where(UserAccount.username.in_(usernames)))}
    rep = SeedReport()
    if existing and not reset_passwords:
        if len(existing) == len(usernames):
            rep.skipped = "the test crew already exists; use --reset-passwords to issue new passwords"
            for m in TEST_CREW:  # keep fictional profiles current
                _astronaut(s, m)
            return rep
        raise SeedError("some test accounts already exist; rerun with --reset-passwords to complete the crew "
                        "and rewrite the credentials file")

    rows: list[tuple[str, str, str, str]] = []
    members: list[tuple[str, str, str, int | None]] = []
    for m in TEST_CREW:
        members.append((m.username, m.name, ROLE_ASTRONAUT, _astronaut(s, m).id))
    members.append((DOCTOR_USERNAME, DOCTOR_NAME, ROLE_DOCTOR, None))
    for username, name, role, astronaut_id in members:
        pw = simple_password(username) if simple_passwords else generate_password()
        user = existing.get(username)
        if user is None:
            create_account(s, username=username, display_name=name, role=role, password=pw,
                           astronaut_id=astronaut_id, is_test_account=True, profile=profile, actor=actor)
            rep.created_accounts.append(username)
        else:
            set_password(s, user, pw, profile=profile, actor=actor)
            rep.reset_accounts.append(username)
        rows.append((username, pw, role, name))
    s.flush()
    audit(s, actor=actor, action="test_crew.seeded", entity="user_account", entity_id=None,
          payload={"created": rep.created_accounts, "reset": rep.reset_accounts, "file": str(credentials_file)})
    rep.credentials = rows
    rep.credentials_file = credentials_file
    return rep


def set_aside_database(db_file: Path, stamp: str) -> list[Path]:
    """Rename the SQLite file (and its WAL/SHM side files) so a fresh database can be created.

    Nothing is deleted: the old data stays next to the new database under a dated name.
    """
    moved: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        src = db_file.with_name(db_file.name + suffix)
        if src.exists():
            dst = db_file.with_name(f"{db_file.stem}.pre-m6a-{stamp}{db_file.suffix}{suffix}")
            src.rename(dst)
            moved.append(dst)
    return moved


__all__ = ["TEST_CREW", "DOCTOR_USERNAME", "SeedError", "SeedReport", "seed_crew", "set_aside_database",
           "write_credentials"]
