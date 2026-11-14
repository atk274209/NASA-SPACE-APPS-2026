#!/usr/bin/env bash
# Start AM locally: one process, one address (http://localhost:8000), no internet needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8000}"
[ -f "$ROOT/frontend/dist/index.html" ] || (cd "$ROOT/frontend" && npm run build)
echo "AM is starting at http://localhost:$PORT (Ctrl+C to stop)"
cd "$ROOT/backend" && exec uv run uvicorn am.main:app --host 127.0.0.1 --port "$PORT"
