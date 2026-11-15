#!/usr/bin/env bash
# Start AM locally: one process, one address (http://localhost:8000), no internet needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# ./scripts/run.sh [--demo] [port]   --demo: SIMULATED space events and the Test Console API on
PORT=8000
for a in "$@"; do case "$a" in --demo) export AM_TEST_MODE=1; echo "Demo mode: simulated space events and the Test Console API are on";; *) PORT="$a";; esac; done
[ -f "$ROOT/frontend/dist/index.html" ] || (cd "$ROOT/frontend" && npm run build)
echo "AM is starting at http://localhost:$PORT (Ctrl+C to stop)"
cd "$ROOT/backend" && exec uv run uvicorn am.main:app --host 127.0.0.1 --port "$PORT"
