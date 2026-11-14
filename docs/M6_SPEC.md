# M6 — Accounts, Test Console, End Day (master prompt v1)

Status: M6a done (backend, Test Console, tests). M6b-M6f pending. Approved on 2026-11-14 together with the 13 default decisions of the
architecture proposal. This file records what M6a builds and the decisions behind it.

## Decisions (approved)

| # | Decision |
|---|----------|
| 1 | Cabin pressure is cabin-wide. Autonomy only inside a narrow band; anything beyond needs doctor approval with a logged reason (M6e). |
| 2 | The astronaut is told after an autonomous cabin change and can undo it with one tap (M6e). |
| 3 | System settings are doctor-only. The astronaut Profile holds only their own preferences. |
| 4 | Switching astronaut in the Test Console requires a new sign-in. |
| 5 | In the MVP only the Test Console's End Day advances the mission day. |
| 6 | Passwords are hashed with argon2id. Older scrypt hashes still verify and are re-hashed on the next successful sign-in. |
| 7 | Manual measurement forms move to the Test Console; the main app keeps routine-exam result entry (M6b/M6d). |
| 8 | Navigation regroup: Overview/Missions/Streaks into the Companion side strip; Baselines/Science/BodyMap into Health; System into Settings (M6b). |
| 9 | Chat model chosen by a scripted TR/EN benchmark; `llama3.1:8b` until then. |
| 10 | The five official challenge resources are added to the knowledge base; CSA pages as summaries only. |
| 11 | Simulated Earth-link latency is a proposal only; not built without approval. |
| 12 | The existing database is kept as a renamed file; a fresh database is seeded with the new crew. |
| 13 | `TEST_CREDENTIALS.txt` is written at the repository root and is gitignored. |
| 14 | **One shared mission clock.** End Day closes the day for the whole crew (a spacecraft has one timeline). Each astronaut still has their own mission-day counter, counted from their own mission start date. |

## Time model

- Mission time = real UTC + a forward-only offset (`am.clock`, unchanged since M5).
- A mission day is a UTC calendar day. Mission day N of an astronaut = days since their
  `mission.start_date` + 1.
- **End Day** moves the shared clock to `WAKE_HOUR` (07:00) mission time of the next calendar
  day. The clock then keeps running in real time, so Test Console entries made "now" fall
  inside the new day.
- **Day lock:** `day_lock_through` (app setting) is the end of the most recently closed day.
  The Test Console refuses entries timestamped before it. Real ingest (sensors, store-and-forward)
  is not locked, because late delivery of real data is legitimate.

## End Day pipeline (`am.endday`)

Run for the date being closed, D. `day_close_run.close_date` is UNIQUE, so the pipeline is
idempotent: a repeated request for D returns the stored run. A run that stopped half-way resumes
from the first step that did not finish.

Steps, in order (each recorded in `day_close_run.steps`):

0. `check_logs`: per astronaut, count the day's check-ins, measurements and symptom reports and
   sync the day's mission-task completion. Missing logs become warnings.
1. `lock_day`: set `day_lock_through` to the end of D.
2. `baselines`: re-evaluate every (metric, context) baseline of each astronaut through
   `BaselineService.maybe_update` (Module C safety rules apply unchanged).
3. `anomaly_persistence`: list alerts still open at the end of D, with their persistence count
   (alerts themselves are raised on ingest; nothing is re-graded here).
4. `correlation`: `run_correlation` per astronaut.
5. `journal`: `JournalService.generate` for each astronaut's mission day that D falls on.
6. `advance_clock`: move the shared clock to 07:00 on D+1.
7. `missions`: `MissionService.ensure_today` for the new day.
8. `streaks`: refresh today's task completion and the streak report.

Step 6 runs before 7 because the mission engine plans "today". The final state matches the
order in the master prompt. Per-astronaut failures inside `correlation` are recorded and do not
stop the crew's day from closing; a failure in any other step stops the run, which then resumes
at that step.

Missing daily logs (no check-in, no measurement) produce warnings and never block the run.

## Data origin

`data_origin` on `measurement`, `symptom_report` and `self_report`:

- `MANUAL_TEST_ENTRY`: typed by a tester (Test Console, or the main app in this prototype).
- `SIMULATED_GENERATOR`: written by the simulator or a scenario fixture.
- `REAL_DEVICE`: reserved. Nothing in the prototype assigns it.

Rows inserted without an explicit value get `SIMULATED_GENERATOR` when `is_simulated` is true,
otherwise `MANUAL_TEST_ENTRY`. All data in this prototype is test data.

## Accounts

- `uv run python -m am.cli seed-crew` creates five fictional astronauts (AST-001..005) with
  fictional profiles and medical histories, one mission per astronaut, and the doctor account.
  Passwords are random and written only to `TEST_CREDENTIALS.txt`.
- `astronaut_profile` holds the fictional profile. Every row is marked `is_fictional`.

## Test Console

- Separate Vite entry (`frontend/testconsole.html`, `frontend/vite.testconsole.config.ts`) on
  port 5174. Every page shows a TEST banner.
- Backend router `/api/test/*`, mounted only when `AM_TEST_MODE=1`. Same sign-in, same
  authorization guard and same validation as the main API.
- **Separate sessions:** browsers send cookies to every port of a host, so the two dev servers
  would share one cookie. The Test Console dev proxy adds `X-AM-Client: test-console`. With that
  header the backend uses the `am_tc_session` cookie instead of `am_session`, so signing in or
  out on one origin never affects the other.
- Scenario fixtures: `nominal`, `exercise_decline`, `poor_sleep`, `acute_symptom_escalation`.
  Loading one back-fills the astronaut's mission with SIMULATED data. It is refused when the
  astronaut already has data.

## Dev servers

The Vite dev proxies keep the browser's Host header (`changeOrigin: false`). With
`changeOrigin: true` the backend saw Host `127.0.0.1:8000` but Origin `localhost:517x`, and its
same-origin check refused every write (sign-in included).

The main app's dev server returns 404 for `/testconsole/*` and `/src/testconsole/*`.

## Not in M6a

- Main-app sign-in screen, role-based navigation (3 astronaut / 4 doctor items), doctor console: M6b.
  Until then the main app's dev UI has no sign-in screen and its API calls get 401.
- Doctor READ audit events: M6b.
