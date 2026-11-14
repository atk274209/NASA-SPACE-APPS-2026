# M5 implementation spec

This is the contract for the M5 feature work. Each feature is built by a separate engineer
working **at the same time in the same checkout**. File ownership is therefore strict.

Read these first:
- `docs/SAFETY.md`: non-negotiable safety rules.
- `README.md`: how the system works.

Then read the code you integrate with.

## 0. Rules for every feature (mandatory)

1. **Own only your files.**
   - Edit only the files listed under your feature.
   - New files must go inside your own folders.
   - If you need a change in a file you do not own, do not make it. Describe it in your
     final report under "integration requests".
   - Shared files are owned by the integrator, not by any feature:
     - `am/main.py`
     - `am/db/*` (models and enums)
     - the Alembic migration
     - `frontend/src/api/client.ts`
     - `frontend/src/App.tsx`
     - `frontend/src/components/Shell.tsx`
     - `frontend/src/components/ui.tsx`
     - `frontend/src/state/session.tsx`
     - `am/settings_store.py`
     - `am/symptoms/catalog.py` (F2 may add synonyms only)
2. **Database models are frozen.** M5 tables and columns already exist; see
   `am/db/models_m5.py` and the new columns described in §1. Do not edit models and do not
   create migrations. If a column is missing, report it.
3. **No fabricated science or medicine.**
   - No clinical thresholds, doses, durations ("if it lasts 3 days"), diagnoses or
     treatment advice.
   - Anything that resembles a rule is labelled `DEMO` and lives in `config/*.demo.yaml`
     or in a clearly named constant.
   - Physical unit conversions are allowed: exact constants, or the glucose molar mass of
     180.16 g/mol. Each one must carry a code comment stating where it comes from.
4. **The LLM never writes health records or decides status.**
   - LLM output may only be stored as clearly labelled drafts or proposals, with a
     grounding check.
   - Each feature must work when Ollama is unavailable, with a deterministic fallback.
     Tests must not need a model; use `FakeOllama` from `tests/test_companion.py`.
5. **Real functionality only.** No placeholder buttons. If something is not implemented,
   leave it out of the UI.
6. **Tests are required.**
   - Add `backend/tests/test_<feature>.py`. Use the fixtures in `tests/conftest.py`
     (`client`, `ctx`, `db`), and the simulator (`POST /api/v1/simulation/run`) for data.
   - The full suite must stay green: `cd backend && uv run pytest -q`.
   - Never touch the live database in `%LOCALAPPDATA%\AM\data`.
   - Do not start servers on ports 8000 or 5173; they are already running.
7. **Frontend conventions.**
   - Typecheck with `cd frontend && npx tsc -p tsconfig.app.json --noEmit`.
   - Put API calls in `frontend/src/api/<feature>.ts`. Import `request` from `./client`,
     and hand-write the TypeScript types that match your Pydantic schemas there.
     Do not regenerate `schema.d.ts`; the integrator does that.
   - Styling:
     - Use the Tailwind tokens: `bg-void|hull|deck`, `border-seam|seam-strong`,
       `text-ink|ink-dim|ink-faint`, `text-signal`, `sev-normal|watch|warning|critical`,
       `sim`.
     - Use the primitives in `components/ui.tsx` (`Section`, `Button`, `Field`,
       `inputCls`, `Empty`, `ErrorNote`, `Loading`, `SeverityTag`, `SimulatedMark`,
       `KeyValue`) and `useSession()`.
     - Write sentence case. No all-caps labels and no decorative cards.
     - Write UI copy in English.
8. **Do not run git commands that change state** (no commit, reset, checkout or stash).
9. **Final report.** List:
   - the files you changed
   - the endpoints you added
   - the tests you added
   - what you could not do
   - integration requests

## 1. Foundation already in place

The integrator built these:

**Models** (`am/db/models_m5.py`):
- `SymptomReport`
- `SymptomProposal`
- `AppSetting`
- `AccessCode`
- `DeviceSession`
- `SyncPackage`
- `EscalationEvent`

**New columns:**
- `Mission.environment`: `leo_station | lunar_surface | deep_space_transit | mars_surface | ground_analog | unspecified`.
- `Measurement.original_value`, `Measurement.original_unit`: the value and unit as entered, before conversion.
- `Message.device_name`, `Message.device_session_id`, `Message.client_host`.
- `JournalEntry` fields:
  - `day_level`: `none | green | yellow | red`
  - `day_level_reasons`
  - `narrative`
  - `narrative_generated_by`
  - `narrative_model`
  - `narrative_grounding`
  - `narrative_generated_at`

**Shared modules:**
- `MeasurementContext` gains `fasting` and `postprandial`.
- `am/symptoms/catalog.py`: 24 symptoms, each with a body system, English and Turkish
  synonym stems, task templates and a `report_promptly` flag (DEMO).
- `am/settings_store.py`: `get_setting`, `set_setting` (audited), `all_settings`,
  `effective_chat_model`, `effective_embed_model`.

**Wiring:**
- Router stubs are registered under `/api/v1`:
  `crew.py`, `symptoms.py`, `streaks.py`, `journal_ext.py`, `admin.py`, `guidance.py`,
  `network.py`.
- `JournalOut` already exposes the new journal fields.
- Placeholder pages and routes exist for `/record`, `/streaks` and `/settings`, and the
  nav has entries for them.

## 2. Features

### F1: Measurement entry, unit conversion, blood glucose, radiation log, crew and missions

**Owns:**
- `am/health/` (`metrics.py`, `validation.py`, `ingest.py`, new `units.py`)
- `am/api/v1/crew.py`
- `am/simulation/generator.py`
- the `blood_glucose` entry in `config/thresholds.demo.yaml`
- `frontend/src/pages/Record.tsx`
- `frontend/src/pages/System.tsx`
- `frontend/src/api/crew.ts`
- `tests/test_f1_entry.py`

**Unit conversion** (`units.py`). Accepted input units convert to the canonical unit:
- Body temperature: °F / degF to °C.
- Body mass: lb to kg (exact factor 0.45359237).
- Blood glucose: mmol/L to mg/dL (×18.016, from glucose molar mass 180.16 g/mol).

Rules:
- Ingest stores the canonical value. When a conversion happened, it also sets
  `original_value` and `original_unit`.
- Validation runs on the canonical value.
- Unknown units are still rejected.

**New metrics:**
- `blood_glucose`:
  - category `metabolic`, unit `mg/dL`
  - contexts: `fasting`, `postprandial`, `other`
  - wide engineering sanity bounds, labelled as such; not a clinical range
  - personal-deviation anomaly rule in `thresholds.demo.yaml` (DEMO); no absolute
    clinical cut-offs
- `radiation_dose`:
  - category `radiation`, unit `mSv`, context `daily` (personal dosimeter daily dose)
  - `baseline_enabled` false; no alert rule
  - `GET /radiation/cumulative?astronaut_id=` returns the daily and cumulative sum.
    Include the note "AM does not evaluate exposure limits."

**Simulator:** add a fasting `blood_glucose` stream (1/day) and a `radiation_dose` daily
stream. Both are labelled simulated, with arbitrary demo parameters. Existing tests must
keep passing.

**Endpoints** (`crew.py`):
- `GET /entry-spec`: per metric, the display name, category, canonical unit, accepted
  units with conversion notes, allowed contexts and the sanity-bound note.
- `POST /missions`: body `{astronaut_id, name, start_date, planned_days?, environment}`.
- `GET /missions/environments`: the five environment codes with plain descriptions. No
  physiological numbers.
- Astronaut creation already exists at `POST /astronauts`.

**UI:**
- **Record page.** A form for the current astronaut:
  - indicator, grouped by category
  - context, limited to the allowed ones
  - value and a unit selector
  - time, defaulting to now (local to UTC)
  - source (defaults to "manual entry")
  - optional confidence or signal quality
  On submit, show the result:
  - accepted, or rejected with the reasons
  - the conversion applied
  - the assessment (severity, robust z, reasons)
  - a link to the alert if one opened
  Also list the last 10 manual entries.
- **System page.** Add a "Crew and missions" section: create an astronaut, and create a
  mission with an environment.

### F2: Symptoms, chat-detected proposals and personalised missions

**Owns:**
- `am/symptoms/` (`service.py`, `detect.py`; may add synonyms to `catalog.py`)
- `am/api/v1/symptoms.py`
- `am/missions/service.py`
- `am/ai/companion.py`
- `am/ai/tools.py`
- `am/api/v1/companion.py`
- `frontend/src/pages/Companion.tsx`
- `frontend/src/pages/Missions.tsx`
- `frontend/src/api/symptoms.ts`
- `tests/test_f2_symptoms.py`

**Reporting:**
- `POST /symptoms` takes `{astronaut_id, symptom_code, severity: mild|moderate|severe, note?, onset_at?}`
  and creates a report with source `manual`.
- `GET /symptoms?astronaut_id&status=`
- `POST /symptoms/{id}/resolve` takes `{note?}`.
- `GET /symptoms/catalog` returns code, label, category, body system and `report_promptly`,
  plus `REPORT_PROMPTLY_LABEL`.
- Each report or resolution is audited.
- A confirmed or manual report also writes a **short** memory item with source `astronaut`.

**Detection from chat:**
- Runs after every user message in `CompanionService.send`. It looks for keyword stems
  from the catalog (English and Turkish), case-insensitively.
- Negation handling:
  - English: no, not, never, without, don't or didn't within 3 words before the match.
  - Turkish: negative verb suffixes in the rest of the matched word (mıyor, miyor,
    muyor, müyor, madı, medi, maz, mez), or "yok"/"değil" within 2 words after.
- `quote` is the sentence containing the match, and must be an exact substring of the
  message.
- Optional LLM detection runs when the setting `symptom_llm_detection` is true and a model
  is available. It uses structured output restricted to catalog codes and an exact quote.
  Quotes that are not exact substrings are dropped.
- De-duplicate by `(message, code)`. The result is a `SymptomProposal` with status
  `pending`. AM never creates a report from chat on its own.
- `TurnOut` gains `proposals` (a list).
- Endpoints:
  - `GET /symptoms/proposals?astronaut_id&status=pending`
  - `POST /symptoms/proposals/{id}/confirm` with `{severity, note?}`, which creates a
    report with source `chat_confirmed`
  - `POST /symptoms/proposals/{id}/dismiss`

**Companion:**
- New read-only tools in `ai/tools.py`:
  - `get_symptom_reports`
  - `search_knowledge(query)`, returning chunk text with refs
    `{type: "knowledge", id: chunk_id, doc_slug, title, url}`
- The system prompt lists active symptoms.
- Use `effective_chat_model` from `settings_store`.

**Personalised missions** (`missions/service.py`):
- Each active symptom report adds that symptom's task templates for today. Reuse generic
  template codes when they already exist, and append the reason instead of duplicating
  the task.
- The reason names the symptom, its severity and the day it was reported. Store refs as
  `{type: "symptom_report", id}`.
- Priority:
  - 1 for moderate or severe
  - 2 for mild
- If the symptom is `report_promptly` or the severity is `severe`, always add
  `symptom_contact_now` with priority 1.
- Protocol refs stay DEMO.

**UI:**
- **Companion page:**
  - a "Report a symptom" panel: searchable catalog, severity, note
  - a "Possible symptoms noticed" panel: each pending proposal shows the quote and offers
    Confirm (with a severity choice) or Dismiss
  - after a chat turn, any new proposals appear inline under that message
  - a list of active symptoms, each with Resolve
- **Missions page:** symptom-linked tasks show which symptom they belong to.

### F3: Streaks (adherence only)

**Owns:**
- `am/missions/streaks.py`
- `am/api/v1/streaks.py`
- `frontend/src/pages/Streaks.tsx`
- `frontend/src/api/streaks.ts`
- `tests/test_f3_streaks.py`

**Principles:**
- Reward **adherence** (logging, check-ins, completing tasks). Never reward health values.
- Reporting a symptom must **count** toward the check-in streak; never discourage
  reporting.
- No penalties and no shaming copy.

**Streak types,** computed per mission day from stored data (day 1 = mission start, UTC):
- `checkin`: any self-report or symptom report that day
- `morning_check`: a rest `heart_rate` reading before 12:00 UTC
- `exercise_logged`: an `exercise_duration` daily measurement exists
- `hydration_logged`: a `water_intake` daily measurement exists
- `missions_complete`: the day has `daily_task` rows and every priority-1 task is done

**Each streak reports:**
- current length (ending today; if today is not done yet, the run ending yesterday still
  counts and is marked `at_risk`)
- longest run
- the last 30 days as a done/missed/no-data calendar
- milestones reached (3, 7, 14, 30 days, labelled as adherence milestones)

**Endpoint:** `GET /streaks?astronaut_id=`.

**UI:** a Streaks page with one row per streak (current, longest, a 30-day calendar strip),
milestone chips and a one-line explanation of the rules. Mark simulated data.

### F4: Journal day marking and AI narrative

**Owns:**
- `am/journal/` (`service.py`, new `levels.py`, `narrative.py`)
- `config/day_levels.demo.yaml`
- `am/api/v1/journal_ext.py`
- `frontend/src/pages/Journal.tsx`
- `frontend/src/api/journal.ts`
- `tests/test_f4_journal.py`

**Day level** (deterministic, DEMO, stored on `JournalEntry` during `generate()` with
reasons that include refs):
- **red:**
  - a symptom reported that day with severity `severe`, or that is `report_promptly`
  - a CRITICAL alert as of the end of that day
  - a behavioural symptom (category `behavioral`) reported as severe
- **yellow:**
  - any other symptom reported that day
  - an unresolved symptom from an earlier day that is still active (an ongoing symptom)
  - a WARNING alert as of that day
  - a "Persistent change" flag
  - a self-report at the scale extreme: mood 1, stress 5, fatigue 5, sleep_quality 1,
    motivation 1
- **green:** data exists for the day and none of the above applies.
- **none:** no data for the day.

**Narrative** (`narrative.py`):
- Input: the entry JSON, the symptoms reported that day, and that day's user chat messages
  (with timestamps and device names, truncated).
- The local LLM returns structured JSON:
  `{mission_continuity, health_status, psychological_status, notes_for_medical_team}`,
  2–3 sentences each, in a scientific, neutral tone.
- It must not diagnose, explain causes or invent numbers.
- Run the grounding check from `am.ai.companion.grounding_check`.
- Store the narrative with `narrative_generated_by='llm'`, the model name and the
  grounding result.
- Fallback: a deterministic template built from the same facts, with
  `narrative_generated_by='deterministic'`.

**Endpoints** (`journal_ext.py`):
- `POST /journal/{astronaut_id}/days/{day}/narrative`
- `POST /journal/{astronaut_id}/narratives` with `{first_day, last_day}`, run sequentially
- `GET /journal/{astronaut_id}/days/{day}/conversations`: that day's messages from all of
  the astronaut's conversations, with timestamp, role, device name and conversation id
- `GET /journal/day-level-rules`: the DEMO rule text for the UI legend

**UI (Journal page):**
- **Day list:** each day carries a coloured marker (yellow / red / green, or none),
  together with a text label so colour is not the only signal.
- **Legend:** shown with a "DEMO rules" note.
- **Selected day:** shows
  - the level reasons
  - the narrative, labelled as drafted by the model from this day's records, with any
    unverified numbers
  - a "Write AI summary" button
  - the symptoms reported that day
  - the conversation timeline (timestamp, device, role)

### F6: Settings, diagnostics, encrypted backup, authorized ground-report outbox

**Owns:**
- `am/admin/` (`settings_api` helpers, `backup.py`, `outbox.py`, `diagnostics.py`)
- `am/api/v1/admin.py`
- `am/api/v1/research.py` (only to switch it to `effective_chat_model`/`effective_embed_model`)
- `scripts/restore_backup.py`
- `frontend/src/pages/Settings.tsx`
- `frontend/src/api/admin.ts`
- `tests/test_f6_admin.py`

**Settings:**
- `GET /settings` returns all settings, each with its default and description.
- `PUT /settings/{key}` takes `{value, actor}` and validates it:
  - `chat_model` and `embed_model` must be installed models when Ollama is reachable;
    when it is offline, reject with a clear message
  - booleans and integers are type-checked
- Every change is audited.

**Diagnostics:** `GET /admin/diagnostics` returns:
- database file size and row counts for the key tables
- the Alembic revision
- free space in the data folder
- Ollama status and installed models
- embedding coverage
- the last backup time
- Python and app versions

**Encrypted backup:**
- `POST /admin/backups` takes `{passphrase (min 12 chars), actor, reason}`.
  - Snapshot with the SQLite online backup API.
  - Encrypt with AES-256-GCM. The key comes from scrypt (random salt; n=2**15, r=8, p=1).
  - File format: magic `AMBK1` + salt + nonce + ciphertext. Write it to `data_dir/backups/`.
  - Record a `SyncPackage` (kind `backup`) and an audit entry.
  - Never store or log the passphrase.
  - Apply the retention setting.
- `GET /admin/backups`
- `POST /admin/backups/{id}/verify` takes `{passphrase}`. It decrypts to a temp file and
  runs `PRAGMA integrity_check`.
- Restore is **CLI only**: `scripts/restore_backup.py` requires `--confirm` and makes a
  copy of the current database first.

**Ground report (outbox):**
- `POST /admin/outbox/ground-report` takes
  `{astronaut_id, first_day, last_day, sections[], authorized_by, reason, encrypt?, passphrase?}`.
  - `sections` is a subset of: alerts, journal, observations, symptoms, checkins, missions.
  - Write deterministic JSON and Markdown to `data_dir/outbox/<id>/`. Include only the
    selected sections, plus provenance: record ids, the thresholds label and the
    simulated flag.
  - Record a `SyncPackage` with its sha256 and size, and an audit entry.
- `GET /admin/outbox`
- `GET /admin/outbox/{id}/download` returns the file.
- `POST /admin/outbox/{id}/mark-exported` takes `{actor}`.
- AM never transmits anything itself.

**UI (Settings page) sections:**
- Local AI model
- Data management (create, verify and list backups)
- Privacy: what is stored where, that nothing leaves the device without an authorized
  package, and the audit trail
- Synchronization: create a ground report and list the outbox
- Diagnostics

### F7: "Is this serious?" guidance, escalation events, body map

**Owns:**
- `am/guidance/` (`escalation.py`, `bodymap.py`)
- `config/escalation.demo.yaml`
- `am/alerts/service.py` (only to create `EscalationEvent` on open or escalate to WARNING
  or CRITICAL)
- `am/api/v1/guidance.py`
- `frontend/src/components/BodyMap.tsx`
- `frontend/src/components/Guidance.tsx`
- `frontend/src/pages/Overview.tsx`
- `frontend/src/pages/Alerts.tsx`
- `frontend/src/api/guidance.ts`
- `tests/test_f7_guidance.py`

**Escalation matrix** (`escalation.demo.yaml`, every entry labelled DEMO):

| Level | Plain meaning | Who to involve | Guidance |
|---|---|---|---|
| WATCH | Keep an eye on it | Yourself | Re-measure per protocol and mention it in your check-in |
| WARNING | Tell the crew medical officer today | Crew medical officer | Discuss at the next private medical conference |
| CRITICAL | Contact now | Crew medical officer or flight surgeon | Use the mission's medical contact procedure now |

`report_promptly` symptoms map to the CRITICAL guidance.

**Endpoints:**
- `GET /guidance/levels`
- `GET /guidance/alerts/{alert_id}`
- `GET /guidance/symptoms/{symptom_report_id}`
- `GET /escalations?astronaut_id&status`
- `POST /escalations/{id}/acknowledge` takes `{actor, note}`

Escalation events are created deterministically in `AlertService.process`, one per alert
per level reached.

**Body map:** `GET /bodymap?astronaut_id=` returns the systems from
`catalog.BODY_SYSTEMS`. For each system:
- `status`: `red | yellow | watch | green | no_data`
- `reasons`
- metrics (latest value plus severity)
- active symptoms
- open alerts
- related knowledge documents, matched by topic

Metric-to-system mapping:

| System | Metrics |
|---|---|
| cardiovascular | heart_rate, hrv_rmssd, systolic_bp, diastolic_bp |
| respiratory | spo2, respiratory_rate |
| immune_skin | body_temperature |
| metabolic | blood_glucose, water_intake, energy_intake, body_mass |
| musculoskeletal | exercise_duration, step_count |
| neuro_behavioral | sleep_duration, self-reports |

Symptoms map through `catalog.body_system`.

Status rules:
- **red:** an open CRITICAL alert on a mapped metric, or an active symptom that is severe
  or `report_promptly`
- **yellow:** an open WARNING alert, or an active mild or moderate symptom
- **watch:** only WATCH alerts are open
- **green:** data exists and none of the above applies
- **no_data:** no data for the system

**UI:**
- `BodyMap.tsx`: an original, hand-drawn SVG silhouette with clickable regions for each
  system, with keyboard support and aria labels, plus a text list as a fallback. Selecting
  a region opens a panel with the system's details.
- `Guidance.tsx`: a "Is this serious?" panel showing the plain meaning, who to involve and
  the DEMO label. Use it in the Alerts detail view and on the Overview page for the
  current status.
- Overview: add the body map and the open escalations.

### F5 (later, separate phase): Crew-network hub, access codes, roles

This feature is listed for awareness only; do not build it in phase 1.

- Hub mode is off by default. It is effective only when two things are true:
  - the `crew_network_enabled` setting is on
  - the server runs with `AM_CREW_NETWORK=1`, bound to the LAN
- Each crew member has a personal access code, stored as a scrypt hash, and a role:
  `crew`, `medical` or `operator`.
- Signing in creates a device session (bearer token, hashed in the database).
- Non-loopback requests require a session.
- A crew member can only read and write their own astronaut's records. Medical and
  operator roles can see the whole crew.
- Messages record `device_name`, `device_session_id` and `client_host`.
- A crew log page shows all conversations with their timestamps.
- The backend serves the built frontend so other devices can use it.
- Phase-1 features must therefore **take `astronaut_id` explicitly** on every endpoint;
  F5 will add an authorization dependency that checks it.
