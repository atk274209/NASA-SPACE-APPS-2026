# AM — Allied Mastercomputer

AM is an offline-first astronaut health monitoring and scientific intelligence prototype.

> Research prototype. Not a medical device. Alert thresholds are demonstration values.
> See [docs/SAFETY.md](docs/SAFETY.md).

## For the jury: install and try AM

AM runs entirely on one computer. It needs no cloud account and no internet connection once it
is installed. All crew members and all health data in the demo are **fictional and simulated**.

### What you need

| Tool | Why | Get it |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | runs the Python backend | `winget install astral-sh.uv` or the website |
| [Node.js 20+](https://nodejs.org/) | builds the web console | the website |
| [Ollama](https://ollama.com/) (optional) | the local AI for conversation, diary and notes | the website |

Without Ollama everything still works: monitoring, alerts, the doctor's feed, cabin control,
space events and duty planning are rule-based, and AM answers with fixed, safe replies.

### Install (once)

Windows PowerShell, from the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Demo -PullModels
```

macOS, Linux or Git Bash:

```bash
./scripts/setup.sh --demo --pull-models
```

This installs the dependencies, builds the console, creates the local database, and adds the
fictional test crew. Leave out `-PullModels` / `--pull-models` to skip the AI models (about 5 GB).

### Start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Demo
```

```bash
./scripts/run.sh --demo
```

Then open **http://localhost:8000** (the Windows script opens it for you). `-Demo` / `--demo`
turns on the simulated space events and the Test Console controls. Stop AM with Ctrl+C.

### Sign in

Passwords are the username followed by `-test`. The full list is in `TEST_CREDENTIALS.txt`
at the repository root.

| Username | Password | Who |
|---|---|---|
| `doctor` | `doctor-test` | the flight surgeon on Earth |
| `ast001` … `ast005` | `ast001-test` … `ast005-test` | the five astronauts |

Use two browser windows (one normal, one private) to watch the astronaut and the doctor at the
same time.

### A short tour

**As an astronaut (`ast001`)**

1. **Companion.** At the top, *Today's duties* shows the work AM gave you today and a note
   about why. Below, chat with AM in English or Turkish. Try:
   - `Odam çok soğuk` or `The light hurts my eyes`: AM adjusts your quarters' temperature or
     lighting, within simulated safety limits, and you can undo it.
   - `Göğsümde sıkışma var` (chest tightness): AM switches to fixed safety words and sends it to
     the doctor at once. The model never decides an emergency.
   - `Dün gece 6 saat uyudum`: AM offers to record the value with one tap.
2. **Diary.** A private diary written by talking with AM. Tap *Look back on this day* to get the
   day's colour (green, yellow or red) and a short summary. Nobody else can read it until the
   mission is back on Earth. Only a medical problem leaves it: an urgent one at once, anything
   else only if you choose *Send to my doctor*.
3. **Health** and **Journal**: your readings, alerts, symptoms and AM's daily journal.

**As the doctor (`doctor`)**

1. **Crew.** Entries that wait for your approval, the live feed of everything the crew writes
   (flagged yellow or red by rules; the AI may only raise a flag, with a quote), crew
   analytics, and *Today's duty roster*: each astronaut's energy and morale and the work AM
   shared out. *Plan again from the latest check-ins* recalculates it.
2. **AM Core.** AM's own reports and decisions. They stay sealed; you can break the seal for
   two hours in an emergency, with a written reason, and the crew is told.
3. **Environment.** Every change AM made to the cabin, and why.
4. **Settings → System and audit.** A live map of the system, and the *Simulated space
   events* panel.

### Simulated space events

With `-Demo` on, AM starts a random event a few seconds after start-up and then about every
three minutes: a solar particle event, a lost link with Earth, an overheating room, a slow
pressure drop, a sensor fault, a crew member's heart rate or temperature rising, or a reported
symptom. Each event changes one real input; what you see afterwards is AM's own reaction: alerts,
paused automation, flags for the doctor, approvals. A card appears for everyone affected.

To show a specific event, sign in as the doctor and use **Settings → System and audit →
Simulated space events**: *Start a random event*, or pick one by name. Untick *Automatic events*
to pause them during a talk.

### Test Console (optional)

For entering check-ins, readings and symptoms by hand, or closing the day (*End Day*), start the
separate Test Console in a second terminal while AM runs with `-Demo`:

```bash
cd frontend && npm run dev:testconsole
```

Then open http://localhost:5174 and sign in with the same accounts.

### If something goes wrong

- **The page says the AI is offline:** Ollama is not running or the models are not pulled. Start
  Ollama and run `ollama pull llama3.1:8b`. Everything else keeps working without it.
- **Wrong username or password:** reset the test passwords with
  `cd backend && uv run python -m am.cli seed-crew --simple-passwords --reset-passwords`.
- **Port 8000 is busy:** start with `scripts\run.ps1 -Demo -Port 8010` (or
  `./scripts/run.sh --demo 8010`) and open that port.
- **Start again from a clean database:** `cd backend && uv run python -m am.cli seed-crew
  --simple-passwords --fresh-db` (the old database file is renamed, not deleted).

## Status

**M1 is done: the core pipeline from measurement to alert.** Implemented:

- Measurement ingest with validation:
  - unit checks and aliases
  - engineering sanity bounds
  - context checks
  - timestamp checks
  - quality flags
- Versioned personal baselines with audited, rate-limited adaptation and freeze rules
- Deterministic, context-aware anomaly classification with persistence gating
- Alert lifecycle: open → escalate → acknowledge → resolve, with measurement evidence
- Seeded scenarios that are clearly labelled as simulated
- REST API (FastAPI)

**M2 is done: the operations console** (React + TypeScript + Vite + Tailwind). It has five
screens:

- **Overview:** mission day, health status, latest readings drawn on deviation strips,
  and open alerts
- **Health:** time series inside personal baseline bands
- **Baselines:** versions, change log, and resuming adaptation after review
- **Alerts:** evidence, explanation, acknowledge and resolve
- **System:** local services, DEMO thresholds, simulated-mission generator, audit trail

Fonts are bundled locally, so the UI works offline.

**M3 is done: the AM companion.** It runs on a local LLM through Ollama
(`llama3.1:8b` by default).

- **Data access:** the model reads data only through read-only tools: overview,
  measurements (with a mission-day and time-of-day lookup), baselines, alerts and check-ins.
- **Traceability:** every answer stores the tools it called and the record IDs it relied
  on. A grounding check flags numbers that do not appear in the retrieved data.
- **Offline fallback:** if the model is unavailable, AM answers with a rule-based status
  summary. Monitoring and alerts never depend on the model.
- **Memory:**
  - short-term: today's check-ins
  - mission: alert and baseline events, written deterministically
  - long-term: the table exists and will be filled by the journal (M4)
- **Daily check-in:** 1–5 self-ratings, which are not clinical instruments.

Known limitation: a 7B local model sometimes omits tool arguments or misreads instructions.
The tools reject ambiguous calls (for example, a missing context) instead of guessing.

**M4 is done: scientific intelligence, missions and journal.**

- **Knowledge base (`knowledge/sources/`):** 15 curated public sources, mostly NASA Human
  Research Program risk pages.
  - Verbatim passages are stored only for US government works.
  - Each source records its license basis and verification notes.
  - Retrieval uses `nomic-embed-text` embeddings, with an offline keyword (TF-IDF)
    fallback when the embedding model is unavailable.
- **Correlation engine:** trends (Spearman, Theil-Sen) and lagged associations
  (0–3 days) with Benjamini-Hochberg FDR control. Each association is re-tested on
  first differences, so two unrelated trends are labelled "shared trend only".
- **Research observations (ARI):** each observation combines the statistical finding,
  retrieved literature (quotes and AM's own summaries are labelled differently) and a
  hypothesis drafted by the LLM with validated citations. The hypothesis states its own
  confidence and goes through a review workflow.
- **Mission journal:** deterministic daily summaries compared with a fixed reference
  period (mission days 1–7). Patterns that persist for 5 or more days are written to
  long-term memory.
- **Adaptive daily missions:** rule-based tasks with a stated reason for each.
  - Completion is verified from recorded data where possible.
  - Protocol IDs are DEMO placeholders.

The backend has 64 pytest tests in total.

**M6 is done: a signed-in, role-based app** (see [docs/M6_SPEC.md](docs/M6_SPEC.md)).

- Sign-in with five FICTIONAL test astronauts and one doctor (`seed-crew --simple-passwords`
  gives passwords like `ast001-test`). Astronaut menu: Companion, Health, Journal. Doctor menu:
  Crew, Environment, Alerts, Settings.
- Whatever an astronaut writes reaches the doctor's live feed at once, flagged yellow or red: the
  deterministic rules set the floor, the local model may only raise it, and rule-level red waits
  for the doctor's approval.
- Companion: replies in Turkish or English, a versioned persona, fixed safety answers for urgent
  symptoms and medication questions, one-tap recording of values said in conversation, AM's notes.
- Routine exams with DEMO intervals and adherence points; explainable risk factors.
- Comfort-aware cabin control against a SIMULATED cabin, inside SIMULATED DEMONSTRATION LIMITS,
  with manual override and an Environment screen.
- Separate Test Console (port 5174): live test entries, End Day, scenarios, wearable feed, cabin faults.

## Layout

```
backend/   FastAPI + SQLAlchemy + Alembic (Python, managed with uv)
config/    thresholds.demo.yaml, baseline_policy.yaml (safety configuration)
docs/      SAFETY.md, architecture notes
frontend/  React + TS + Vite operations console
knowledge/ source-traceable scientific documents (knowledge/sources/*.json + _index.md)
```

## Local model

Install Ollama, then pull the chat model and the embedding model:

```bash
ollama pull llama3.1:8b
```

```bash
ollama pull nomic-embed-text
```

To use a different chat model, set `AM_CHAT_MODEL`.

## Data location

The database lives **outside** the source tree, so health data is never synced with the code:

- Windows: `%USERPROFILE%\.am\data\am.sqlite3`
- Other systems: `~/.local/share/am/am.sqlite3`

On Windows it is deliberately **not** under `%LOCALAPPDATA%`. Packaged (MSIX) apps such as the
Claude desktop app see a virtualized copy of AppData. A SQLite file and its `-wal`/`-shm` files
can then be split between the real folder and the app's private copy, which corrupts the database.

To use a different location, set `AM_DATA_DIR` or `AM_DATABASE_URL`.

## Run

Start the API:

```bash
cd backend && uv run uvicorn am.main:app --reload --port 8000
```

- API docs: http://127.0.0.1:8000/docs
- Migrations run automatically at startup.

To try a simulated scenario, run:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/simulation/run -H "content-type: application/json" -d "{\"scenario\":\"acute_rest_hr\",\"astronaut_code\":\"SIM-001\"}"
```

Available scenarios: `nominal`, `acute_rest_hr`, `gradual_decline`
(list them with `GET /api/v1/simulation/scenarios`).

Start the console in a second terminal (it proxies `/api` to port 8000):

```bash
cd frontend && npm run dev
```

Then open http://localhost:5173.

If the backend schemas change, regenerate the frontend API types:

```bash
cd frontend && npm run gen:api
```

## Test crew and Test Console (M6a)

Create the five FICTIONAL test astronauts and the doctor account. The sign-in details are
written to `TEST_CREDENTIALS.txt` at the repository root (gitignored, testing only):

```bash
cd backend && uv run python -m am.cli seed-crew
```

`--fresh-db` first renames the current database file (it is kept, not deleted).
`--reset-passwords` issues new passwords.

The Test Console is a separate app on port 5174. It needs the backend started with
`AM_TEST_MODE=1`, which mounts `/api/test/*`:

```bash
cd backend && AM_TEST_MODE=1 uv run uvicorn am.main:app --port 8000
```

```bash
cd frontend && npm run dev:testconsole
```

Then open http://localhost:5174. It has live test entries, End Day and scenario fixtures. See
[docs/M6_SPEC.md](docs/M6_SPEC.md).

In this OneDrive folder, set `UV_LINK_MODE=copy` if `uv run` fails with "os error 396".

## Test

```bash
cd backend && uv run pytest -q
```

## Database migrations

After changing models, create a new migration:

```bash
cd backend && uv run alembic revision --autogenerate -m "describe change"
```
