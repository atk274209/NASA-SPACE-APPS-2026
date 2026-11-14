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
- REST API (FastAPI) and 37 pytest tests

**M2 is done: the operations console** (React + TypeScript + Vite + Tailwind). It has five
screens:

- **Overview:** mission day, health status, latest readings drawn on deviation strips,
  and open alerts
- **Health:** time series inside personal baseline bands
- **Baselines:** versions, change log, and resuming adaptation after review
- **Alerts:** evidence, explanation, acknowledge and resolve
- **System:** local services, DEMO thresholds, simulated-mission generator, audit trail

Fonts are bundled locally, so the UI works offline.

Next milestones: the AI companion (Ollama), memory, RAG, research intelligence, missions
and the journal.

## Layout

```
backend/   FastAPI + SQLAlchemy + Alembic (Python, managed with uv)
config/    thresholds.demo.yaml, baseline_policy.yaml (safety configuration)
docs/      SAFETY.md, architecture notes
frontend/  React + TS + Vite operations console
knowledge/ (M4) source-traceable scientific documents
```

## Data location

The database lives **outside** this (OneDrive-synced) folder:
`%LOCALAPPDATA%\AM\data\am.sqlite3`.

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

## Test

```bash
cd backend && uv run pytest -q
```

## Database migrations

After changing models, create a new migration:

```bash
cd backend && uv run alembic revision --autogenerate -m "describe change"
```
