# AM — Allied Mastercomputer

AM is an offline-first astronaut health monitoring and scientific intelligence prototype.

> Research prototype. Not a medical device. Alert thresholds are demonstration values.
> See [docs/SAFETY.md](docs/SAFETY.md).

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
