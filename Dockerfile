# Ratiocination container image (docs/DEPLOY.md; container contract: docs/SPEC.md "Deployment").
#
# Build context: the repository root (.dockerignore keeps it small). `fly deploy` builds this remotely,
# so no local Docker is needed. Locally: docker build -t ratiocination .
#
# Layout inside the image mirrors the repository, because radar.config finds the Alembic migrations
# (backend/migrations) and the web build (web/dist) relative to the source tree; a non-editable
# `pip install ./backend` would ship neither. Dependencies come pinned from backend/requirements.lock.txt.
#
#   /opt/venv            Python dependencies (no dev tools)
#   /app/backend         radar package, migrations, alembic.ini (PYTHONPATH)
#   /app/web/dist        built SPA, served by the backend
#   /data                RADAR_DATA_DIR: radar.db, uploads/, models/, logs/ (mount a volume here)

ARG NODE_IMAGE=node:24-slim
ARG PYTHON_IMAGE=python:3.13-slim

# ---- 1. Web app: npm ci + npm run build -------------------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. Python dependencies: pinned, binary wheels only ---------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements.lock.txt /tmp/requirements.lock.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --only-binary=:all: -r /tmp/requirements.lock.txt \
 && /opt/venv/bin/pip check

# ---- 3. Runtime --------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# sqlite3: `radar backup` (on-demand database copies over `fly ssh console`). setpriv (util-linux, part
# of the base image) lets the entrypoint drop root after fixing the data volume's ownership.
RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/* \
 && command -v setpriv \
 && groupadd --gid 10001 radar \
 && useradd --uid 10001 --gid radar --home-dir /home/radar --create-home --shell /bin/bash radar

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RADAR_ENV_FILE="" \
    RADAR_DATA_DIR=/data \
    RADAR_HOST=0.0.0.0 \
    RADAR_PORT=8080

COPY --from=deps /opt/venv /opt/venv
COPY backend/pyproject.toml backend/alembic.ini /app/backend/
COPY backend/migrations /app/backend/migrations
COPY backend/radar /app/backend/radar
COPY --from=web /web/dist /app/web/dist
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint
COPY scripts/container-radar.sh /usr/local/bin/radar

# Code stays root-owned and read-only for the app; only the data directory belongs to `radar`.
RUN chmod 0755 /usr/local/bin/docker-entrypoint /usr/local/bin/radar \
 && python -m compileall -q /app/backend/radar /app/backend/migrations \
 && mkdir -p /data \
 && chown radar:radar /data \
 && chmod 0700 /data \
 && python -c "import radar.cli, radar.main; from radar.config import MIGRATIONS_DIR, WEB_DIST_DIR; assert (MIGRATIONS_DIR / 'env.py').is_file(), MIGRATIONS_DIR; assert (WEB_DIST_DIR / 'index.html').is_file(), WEB_DIST_DIR"

WORKDIR /app/backend
EXPOSE 8080

# Starts as root only to chown the volume, then runs as `radar` (scripts/docker-entrypoint.sh).
# `radar.cli serve` = uvicorn, ONE worker, RADAR_HOST/RADAR_PORT, the app's redacting log config and
# the startup safety checks (a non-loopback bind needs RADAR_AUTH=accounts, RADAR_ADMIN_EMAILS and TLS).
ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]
CMD ["python", "-m", "radar.cli", "serve"]
