#!/bin/sh
# Container entrypoint (Dockerfile, docs/DEPLOY.md).
#
# Runs as root only long enough to make the data volume writable for the unprivileged `radar` user,
# then runs the command (default: `python -m radar.cli serve`) as that user. Fly.io mounts a new
# volume owned by root over /data, which hides the image's own /data, so ownership is fixed here on
# every start. This also repairs files that a root shell (`fly ssh console`) created on the volume.
set -eu

DATA_DIR="${RADAR_DATA_DIR:-/data}"

if [ "$(id -u)" -ne 0 ]; then
  exec "$@"
fi

mkdir -p "$DATA_DIR"
# Not fatal: when `radar <command>` runs next to the live app, a file may vanish during the walk.
find "$DATA_DIR" -xdev \( ! -user radar -o ! -group radar \) -exec chown -h radar:radar {} + \
  || echo "docker-entrypoint: could not change the owner of some files in $DATA_DIR" >&2
chown radar:radar "$DATA_DIR"
chmod 0700 "$DATA_DIR"

export HOME=/home/radar
exec setpriv --reuid=10001 --regid=10001 --init-groups -- "$@"
