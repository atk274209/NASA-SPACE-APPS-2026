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
(`qwen2.5:7b-instruct` by default).

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
ollama pull qwen2.5:7b-instruct
```

```bash
ollama pull nomic-embed-text
```

To use a different chat model, set `AM_CHAT_MODEL`.

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
