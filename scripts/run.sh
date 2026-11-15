#!/usr/bin/env bash
# Start AMC locally: one process, one address (http://localhost:8000), no internet needed.
# ./scripts/run.sh [--demo] [--lan] [port]
#   --demo: SIMULATED space events and the Test Console API on (demonstrations only)
#   --lan:  other computers on the same local network may connect (this computer's own
#           addresses only), for example the doctor on one laptop and the astronaut on another
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000
BIND=127.0.0.1
LAN=0
for a in "$@"; do case "$a" in
  --demo) export AM_TEST_MODE=1; echo "Demo mode: simulated space events and the Test Console API are on";;
  --lan) LAN=1;;
  *) PORT="$a";;
esac; done
[ -f "$ROOT/frontend/dist/index.html" ] || (cd "$ROOT/frontend" && npm run build)
if [ "$LAN" = 1 ]; then
  IPS="$( (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || true) | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -vE '^(127|169\.254)\.' || true)"
  [ -n "$IPS" ] || { echo "No network address found. Connect to the Wi-Fi first."; exit 1; }
  HOSTS='["localhost","127.0.0.1","[::1]"'
  for ip in $IPS; do HOSTS="$HOSTS,\"$ip\""; done
  export AM_ALLOWED_HOSTS="$HOSTS]"
  BIND=0.0.0.0
  echo; echo "Network mode. On the other computer (same Wi-Fi), open one of:"
  for ip in $IPS; do echo "    http://$ip:$PORT"; done; echo
fi
echo "AMC is starting at http://localhost:$PORT (Ctrl+C to stop)"
cd "$ROOT/backend" && exec uv run uvicorn am.main:app --host "$BIND" --port "$PORT"
