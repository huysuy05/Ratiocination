#!/bin/sh
# `radar` inside the container image (installed as /usr/local/bin/radar; docs/DEPLOY.md).
#
#   radar <command> [args]   the Ratiocination CLI (python -m radar.cli), e.g. `radar reset-link --email x`
#   radar backup             consistent copy of the SQLite database (safe while the app runs) to
#                            <data>/backups/radar-<UTC time>.db, then an integrity check; prints the path
#
# Commands run as the `radar` user (through docker-entrypoint), so files they create on the data volume
# stay writable for the app even when called from a root shell (`fly ssh console`).
set -eu

DATA_DIR="${RADAR_DATA_DIR:-/data}"
export RADAR_DATA_DIR="$DATA_DIR"
export PYTHONPATH="${PYTHONPATH:-/app/backend}"
export PATH="/opt/venv/bin:$PATH"

if [ "${1:-}" = "backup" ]; then
  out="$DATA_DIR/backups/radar-$(date -u +%Y%m%dT%H%M%SZ).db"
  docker-entrypoint mkdir -p "$DATA_DIR/backups"
  docker-entrypoint sqlite3 "$DATA_DIR/radar.db" ".backup '$out'"
  # The copy inherits WAL mode; switch it to a single self-contained file for download.
  docker-entrypoint sqlite3 "$out" 'PRAGMA journal_mode=DELETE;' > /dev/null
  check="$(docker-entrypoint sqlite3 "$out" 'PRAGMA integrity_check;')"
  if [ "$check" != "ok" ]; then
    echo "backup written to $out but its integrity check failed: $check" >&2
    exit 1
  fi
  echo "$out"
  exit 0
fi

exec docker-entrypoint python -m radar.cli "$@"
