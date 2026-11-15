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

---

# M6b–M6e (built 2026-11-14, after "complete the app")

## Decisions added by the user

- **AI flag (yellow/red):** deterministic rules set the floor; the local model adds a suggestion that
  must quote the astronaut's text and can only RAISE the level shown, never lower it. Only a rule-level
  red waits for doctor approval (Engineering Rule 8).
- **Commits:** none until the user asks at the end.

## M6b: live doctor feed, sign-in, roles

- `report_flag` (am/db/models_m6b.py): one row per thing an astronaut writes (chat message, symptom
  report, check-in note), created by ORM events the moment it is stored. Rules in `am/triage/rules.py`
  (R-TEXT-SELFHARM, R-TEXT-PROMPTLY, R-TEXT-SYMPTOM, R-SYM-LEVEL); AI suggestion by a background worker
  (`am/triage/ai.py`) with no database transaction open during the model call.
- Doctor: `/doctor/feed` (poll with `after_id`), `/doctor/crew`, `/doctor/approvals`,
  `/doctor/feed/{id}/seen|approve`. Astronaut: `/care/status` (awaiting approval + DEMO protocol).
- Doctor reads of crew records are audited (`doctor.read`), once per session, astronaut and route per
  10 minutes.
- Main app: sign-in page; astronaut menu Companion / Health / Journal (+ Profile); doctor menu Crew /
  Environment / Alerts / Settings. Earlier pages regrouped as tabs (Health: Today, Trends, Baselines,
  Insights, Missions, Streaks; Settings: Settings, System and audit). Manual entry lives in the Test Console.

## M6c: companion safety, persona, conversation capture

- Review findings fixed: R01/R03 (no write lock during model calls; busy timeout 30 s), R02 (medication
  and treatment questions always get the fixed no-advice reply, also as follow-ups; wording check on
  model answers), R04 (self-harm catalog entry, red), R05 (symptoms never add action tasks such as
  exercise), R06 (report-promptly symptoms get fixed contact-now words, online or offline).
- Replies follow the astronaut's language (Turkish/English); fixed safety texts exist in both.
- Persona (Module O): `config/persona/am_companion.v1.yaml`, after the safety rules in the prompt;
  one open question per reply (post-check); deterministic opener for a new conversation.
- Module L: stated values (heart rate, sleep, water, temperature, SpO2, blood pressure) become
  `measurement_proposal` rows; one tap records them through the normal ingest pipeline
  (source "AM conversation (CONVERSATION_EXTRACTED)"). AM writes `ai_note` rows with exact quotes.

## M6d: routine exams, points, wearable feed, explanation

- `config/routine_exams.demo.yaml` (DEMO intervals): blood, urine, reflex, eye exams; due/overdue;
  results are what the astronaut enters. Points reward adherence only (End Day step `exams_points`).
- Test Console: simulated wearable feed (`/api/test/wearable`, presets nominal / elevated heart rate / fever).
- `/explain`: deterministic factors behind the DEMO risk level (alerts with rule ids and evidence,
  flagged reports, 3-day trend vs baseline). No probability is shown.

## M6e: cabin comfort control (Module P)

- `config/cabin_envelope.demo.yaml`: SIMULATED DEMONSTRATION LIMITS (temperature, lighting per quarters;
  pressure cabin-wide with a narrow autonomous band).
- `am/cabin`: comfort extraction (EN/TR, negation-aware), SimulatedCabin behind the CabinActuator
  protocol, deterministic controller with rule ids P-*. Pressure complaints are reported, never acted on.
- Manual control always wins (mode MANUAL until someone re-enables autonomy); pressure outside the band
  needs the doctor and a logged reason; faults and out-of-envelope readings pause autonomy and raise
  environment alerts.
- Astronaut comfort strip (Companion), doctor Environment screen, Test Console cabin fault/drift tab.

## Not done

- The five official challenge resources are not yet in the knowledge base (decision 10): each source
  needs fetching and a licence check first.
- Voice-tone and facial-expression analysis: not built, no UI (roadmap).

# M6f–M6g (2026-11-14)

## M6f: water, vitamins, crew board, streaks

- `config/nutrition.demo.yaml`: DEMO daily water target, quick-add sizes, the protocol's supplement list.
  AM records what was drunk or taken; it never suggests a supplement or a dose.
- `hydration_log` (each drink; water said in chat goes here too) and `supplement_log` (taken today).
  End Day step `hydration` stores the day's total once as a `water_intake` measurement and awards
  adherence points (target met: once a day; supplements logged: once a day).
- Crew hydration board (user decisions): ranked by days the target was met and adherence capped at
  100 % (drinking more earns nothing extra); no amounts shown; opt-in, default on, leave any time in
  Profile (audited).
- Companion shows the mission-completion, check-in and hydration streaks.

## M6g: AM Core (user design)

- AM Core is the station intelligence's own back end. The crew has no access. The doctor always sees
  the decisions marked visible (attention level, doctor notices, mission focus, crew recommendations).
  The full core (daily astronaut and crew reports, behaviour, crew attitude, mission log, summary)
  opens only (a) in an emergency: the doctor breaks the seal with a logged reason for 2 hours and the
  crew is told who opened it and why, or (b) after mission return (Test Console button), for good.
- End Day step `am_core` (after `journal`): deterministic facts and assessment per astronaut and for
  the crew, and AM's own decisions (rules A-*). Behaviour = self-rating trends (DEMO rule: 3-day mean
  moved by >= 1.0) and topics the astronaut raised; observations, not clinical assessments.
- Narratives: background worker; the local model writes each report from its stored facts only, with
  grounding and wording checks kept on the report; template text when the model is offline.
- Safety: nothing safety-relevant is sealed (alerts, red flags and approvals keep reaching the doctor
  through the live feed), and AM Core never decides medical or emergency status.
- Transparency trade-off (master prompt Module O says the astronaut can read what AM notes about them):
  the astronaut can still read AM's notes and journal; they are told the sealed log exists (Profile)
  and are notified whenever it is opened, but not its content.
