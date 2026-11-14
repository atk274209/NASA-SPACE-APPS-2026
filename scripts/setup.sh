#!/usr/bin/env bash
# AM one-command setup (macOS/Linux/Git Bash).  ./scripts/setup.sh [--demo] [--pull-models]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO=0; PULL=0
for a in "$@"; do case "$a" in --demo) DEMO=1;; --pull-models) PULL=1;; esac; done
command -v uv >/dev/null || { echo "Missing uv: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Missing Node.js 20+: https://nodejs.org/"; exit 1; }

echo "1/5 Backend dependencies"; (cd "$ROOT/backend" && uv sync)
echo "2/5 Console dependencies and build"; (cd "$ROOT/frontend" && npm ci && npm run build)
if [ "$PULL" = 1 ]; then
  command -v ollama >/dev/null || { echo "Missing Ollama (optional): https://ollama.com/"; exit 1; }
  echo "Pulling local models (about 5 GB)"; ollama pull qwen2.5:7b-instruct; ollama pull nomic-embed-text
fi
cd "$ROOT/backend"
echo "3/5 Database"; uv run python -m am.cli migrate
echo "4/5 Knowledge base"; uv run python -m am.cli ingest-knowledge
if [ "$DEMO" = 1 ]; then echo "5/5 Demo crew (SIMULATED data)"; uv run python -m am.cli seed-demo
else echo "5/5 Skipped demo crew (add --demo to create it)"; fi
uv run python -m am.cli status
echo "Done. Start AM with: ./scripts/run.sh"
