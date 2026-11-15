"""AMC command line: setup and demo helpers that run in-process (no server needed).

    uv run python -m am.cli migrate
    uv run python -m am.cli ingest-knowledge
    uv run python -m am.cli seed-demo [--narratives]
    uv run python -m am.cli seed-crew [--reset-passwords] [--simple-passwords] [--fresh-db]
    uv run python -m am.cli status

The demo crew is SIMULATED data: every astronaut, mission, measurement, self-report and
symptom report it creates is flagged is_simulated and sourced from the simulator.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import numpy as np
from sqlalchemy import func, select

from am.api.deps import AppContext
from am.config import REPO_ROOT, get_settings
from am.db.base import utcnow
from am.db.models import Astronaut, Measurement, Mission
from am.db.models_ai import SelfReport
from am.db.models_m5 import SymptomReport
from am.health.ingest import IngestService
from am.main import build_context
from am.simulation.generator import SCENARIOS, generate


@dataclass(frozen=True)
class DemoSymptom:
    day: int
    code: str
    severity: str
    resolved_day: int | None = None


@dataclass(frozen=True)
class DemoMember:
    code: str
    name: str
    scenario: str
    environment: str
    mission_name: str
    # check-in trajectory: kind -> (score on day 1, score on the last day); linear, rounded, clipped 1..5
    checkins: dict[str, tuple[float, float]] = field(default_factory=dict)
    symptoms: tuple[DemoSymptom, ...] = ()
    seed: int = 7


DEMO_CREW: tuple[DemoMember, ...] = (
    DemoMember(
        "SIM-ALPHA", "Simulated astronaut Alpha", "gradual_decline", "deep_space_transit",
        "SIMULATED DEMO: transit habitat",
        checkins={"mood": (4.2, 2.1), "stress": (1.8, 4.3), "fatigue": (1.8, 4.2),
                  "sleep_quality": (4.1, 2.0), "motivation": (4.3, 2.8)},
        symptoms=(DemoSymptom(12, "constipation", "moderate", resolved_day=16),
                  DemoSymptom(18, "insomnia", "moderate"),
                  DemoSymptom(21, "headache", "mild", resolved_day=22),
                  DemoSymptom(24, "low_mood", "moderate"),
                  DemoSymptom(27, "back_pain", "severe")),
        seed=11),
    DemoMember(
        "SIM-BRAVO", "Simulated astronaut Bravo", "acute_rest_hr", "leo_station",
        "SIMULATED DEMO: orbital station",
        checkins={"mood": (4.0, 3.6), "stress": (2.0, 3.0), "fatigue": (2.0, 3.0),
                  "sleep_quality": (4.0, 3.5), "motivation": (4.0, 3.8)},
        symptoms=(DemoSymptom(4, "dizziness", "moderate"),),
        seed=12),
    DemoMember(
        "SIM-CHARLIE", "Simulated astronaut Charlie", "nominal", "lunar_surface",
        "SIMULATED DEMO: lunar surface outpost",
        checkins={"mood": (4.0, 4.0), "stress": (2.0, 2.0), "fatigue": (2.0, 2.0),
                  "sleep_quality": (4.0, 4.0), "motivation": (4.0, 4.0)},
        symptoms=(DemoSymptom(6, "nasal_congestion", "mild", resolved_day=8),),
        seed=13),
)


def _ctx() -> AppContext:
    return build_context(get_settings())


def cmd_migrate(_args) -> int:
    ctx = _ctx()  # build_context runs Alembic upgrade + seeds the metric registry
    print(f"Database ready at {ctx.settings.resolved_database_url}")
    return 0


def cmd_ingest(_args) -> int:
    from am.rag.knowledge import ingest_directory
    from am.settings_store import effective_embed_model

    ctx = _ctx()
    with ctx.session_factory() as s:
        rep = ingest_directory(s, REPO_ROOT / "knowledge" / "sources", ctx.llm, effective_embed_model(s, ctx.settings))
        s.commit()
    print(f"Knowledge: {rep.loaded} new, {rep.updated} updated, {rep.unchanged} unchanged, "
          f"{rep.embedded} chunks embedded" + (f" (embeddings unavailable: {rep.embed_error}; keyword search "
                                                  "will be used)" if rep.embed_error else ""))
    for sk in rep.skipped:
        print(f"  skipped {sk}")
    return 0


def _create_member(s, ctx: AppContext, m: DemoMember) -> Astronaut:
    scenario = SCENARIOS[m.scenario]
    a = Astronaut(code=m.code, display_name=m.name, is_simulated=True)
    s.add(a)
    s.flush()
    end = utcnow()
    mission = Mission(name=m.mission_name, astronaut_id=a.id, start_date=(end - timedelta(days=scenario.days)).date(),
                      planned_days=scenario.days, environment=m.environment, is_simulated=True)
    s.add(mission)
    s.flush()
    svc = IngestService(s, ctx.thresholds, ctx.policy)
    for item in generate(scenario, astronaut_id=a.id, end=end, seed=m.seed):
        svc.ingest(item, now=end)
    s.flush()

    last_day = scenario.days  # past days only; today's check-in is left for the live demo
    rng = np.random.default_rng(m.seed)
    for d in range(1, last_day + 1):
        at = datetime.combine(mission.start_date, time(21, 0)) + timedelta(days=d - 1)
        frac = (d - 1) / max(last_day - 1, 1)
        cid = f"demo-{m.code}-{d}"
        for kind, (v0, v1) in m.checkins.items():
            score = int(np.clip(round(v0 + (v1 - v0) * frac + rng.normal(0, 0.35)), 1, 5))
            s.add(SelfReport(astronaut_id=a.id, checkin_id=cid, kind=kind, score=float(score), scale_max=5,
                             note="", is_simulated=True, recorded_at=at))
    for sym in m.symptoms:
        at = datetime.combine(mission.start_date, time(9, 30)) + timedelta(days=sym.day - 1)
        resolved = (datetime.combine(mission.start_date, time(20, 0)) + timedelta(days=sym.resolved_day - 1)
                    if sym.resolved_day else None)
        s.add(SymptomReport(astronaut_id=a.id, symptom_code=sym.code, severity=sym.severity,
                            note="Simulated report created by the demo seed.", onset_at=at, reported_at=at,
                            source="simulated", status="resolved" if resolved else "active", resolved_at=resolved,
                            is_simulated=True))
    s.flush()
    return a


def cmd_seed_demo(args) -> int:
    from am.journal.service import JournalService
    from am.missions.service import MissionService
    from am.research.service import run_correlation

    ctx = _ctx()
    cmd_ingest(args)
    with ctx.session_factory() as s:
        for m in DEMO_CREW:
            a = s.scalars(select(Astronaut).where(Astronaut.code == m.code)).first()
            if a is None:
                a = _create_member(s, ctx, m)
                print(f"Created {m.code} ({m.scenario}, {m.environment})")
            else:
                print(f"{m.code} already exists; refreshing derived records")
            JournalService(s).generate_range(a.id)
            run_correlation(s, a.id)
            MissionService(s).ensure_today(a.id)
            s.commit()
            if args.narratives:
                _narratives(s, ctx, a.id)
    print("Demo crew ready (SIMULATED data). Open the console and pick a crew member in the header.")
    return 0


def _narratives(s, ctx: AppContext, astronaut_id: int, last_n: int = 3) -> None:
    """Optionally pre-write AI journal narratives for the most recent complete days."""
    from am.db.models_research import JournalEntry
    from am.journal.narrative import write_narrative
    from am.research.series import current_mission_day
    from am.settings_store import effective_chat_model

    today = current_mission_day(s, astronaut_id) or 1
    model = effective_chat_model(s, ctx.settings)
    for day in range(max(1, today - last_n), today):
        entry = s.scalars(select(JournalEntry).where(JournalEntry.astronaut_id == astronaut_id,
                                                     JournalEntry.mission_day == day)).first()
        if entry is None:
            continue
        try:
            write_narrative(s, entry, llm=ctx.llm, model=model)
            s.commit()
            print(f"  narrative written for day {day}")
        except Exception as exc:  # demo helper: report and continue
            s.rollback()
            print(f"  narrative for day {day} failed: {type(exc).__name__}: {exc}")


def cmd_seed_crew(args) -> int:
    """Create the five FICTIONAL test astronauts and the doctor account (see am.crew_seed)."""
    from am.config import get_settings
    from am.crew_seed import SeedError, seed_crew, set_aside_database, write_credentials

    settings = get_settings()
    if args.fresh_db:
        if settings.database_url:
            print("--fresh-db only works with the default database file; unset AM_DATABASE_URL first.")
            return 2
        db_file = settings.data_dir / "am.sqlite3"
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        try:
            moved = set_aside_database(db_file, stamp)
        except PermissionError:
            print("The database file is in use. Stop the AMC backend, then run this again.")
            return 2
        for path in moved:
            print(f"Previous database kept as {path}")
    ctx = _ctx()
    with ctx.session_factory() as s:
        try:
            rep = seed_crew(s, credentials_file=settings.test_accounts_file, reset_passwords=args.reset_passwords,
                            simple_passwords=args.simple_passwords, profile=settings.password_hash_profile)
        except SeedError as exc:
            print(f"Not seeded: {exc}")
            return 2
        s.commit()
    if rep.skipped:
        print(f"Nothing to do: {rep.skipped}.")
        return 0
    write_credentials(rep.credentials_file, rep.credentials)
    if rep.created_accounts:
        print(f"Created accounts: {', '.join(rep.created_accounts)}")
    if rep.reset_accounts:
        print(f"New passwords for: {', '.join(rep.reset_accounts)}")
    print(f"Test sign-in details written to {rep.credentials_file} (gitignored; testing only).")
    return 0


def cmd_status(_args) -> int:
    ctx = _ctx()
    with ctx.session_factory() as s:
        crew = s.scalar(select(func.count()).select_from(Astronaut)) or 0
        rows = s.scalar(select(func.count()).select_from(Measurement)) or 0
    st = ctx.llm.status(ctx.settings.chat_model)
    print(f"Database: {ctx.settings.resolved_database_url}\nCrew records: {crew}\nMeasurements: {rows}\n"
          f"Local model: {ctx.settings.chat_model} "
          f"({'ready' if st.get('model_installed') else st.get('reason') or 'unavailable'})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="am", description="AMC setup and demo helpers")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create/upgrade the local database").set_defaults(fn=cmd_migrate)
    sub.add_parser("ingest-knowledge", help="index knowledge/sources").set_defaults(fn=cmd_ingest)
    sd = sub.add_parser("seed-demo", help="create the SIMULATED demo crew")
    sd.add_argument("--narratives", action="store_true", help="also write AI journal narratives (needs the model)")
    sd.set_defaults(fn=cmd_seed_demo)
    sc = sub.add_parser("seed-crew", help="create the FICTIONAL test crew (5 astronauts + doctor)")
    sc.add_argument("--reset-passwords", action="store_true", help="issue new passwords and rewrite the file")
    sc.add_argument("--simple-passwords", action="store_true",
                    help='easy TEST passwords "<username>-test" instead of random ones')
    sc.add_argument("--fresh-db", action="store_true",
                    help="first rename the current database file (kept, not deleted) and start a new one")
    sc.set_defaults(fn=cmd_seed_crew)
    sub.add_parser("status", help="show database and model status").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
