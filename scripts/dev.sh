#!/usr/bin/env bash
# Start the Ratiocination backend (uvicorn, one worker) and the Vite dev server together.
#
#   scripts/dev.sh            backend on http://127.0.0.1:8000, frontend on http://localhost:5173
#
# Environment: RADAR_HOST / RADAR_PORT (backend bind), PORT (Vite port), RADAR_API_PROXY (Vite's
# /api proxy target; defaults to the backend). Ctrl-C stops both processes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
HOST="${RADAR_HOST:-127.0.0.1}"
PORT_BACKEND="${RADAR_PORT:-8000}"
PORT_WEB="${PORT:-5173}"

if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "Missing $VENV/bin/uvicorn - run: python3.13 -m venv .venv && .venv/bin/python -m pip install -e 'backend[dev]'" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
LOG_CONFIG="$TMP_DIR/uvicorn-log.json"
"$VENV/bin/python" -m radar.logging_setup > "$LOG_CONFIG"

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  rm -rf "$TMP_DIR"
}
trap cleanup INT TERM EXIT

echo "backend:  http://$HOST:$PORT_BACKEND  (data: ${RADAR_DATA_DIR:-$ROOT/data})"
(cd "$ROOT/backend" && exec "$VENV/bin/uvicorn" radar.main:create_app --factory \
  --host "$HOST" --port "$PORT_BACKEND" --workers 1 --log-config "$LOG_CONFIG") &
PIDS+=("$!")

if [[ -f "$ROOT/web/package.json" ]]; then
  if [[ ! -d "$ROOT/web/node_modules" ]]; then
    echo "web/node_modules is missing - run: (cd web && npm ci)" >&2
  else
    echo "frontend: http://localhost:$PORT_WEB"
    (cd "$ROOT/web" && PORT="$PORT_WEB" RADAR_API_PROXY="${RADAR_API_PROXY:-http://127.0.0.1:$PORT_BACKEND}" \
      exec npm run dev -- --port "$PORT_WEB" --strictPort) &
    PIDS+=("$!")
  fi
else
  echo "web/ not found - starting the backend only" >&2
fi

# Exit (and stop the other process) as soon as either one stops.
wait -n 2>/dev/null || wait
