"""``GET /api/health`` (docs/API.md "Jobs, settings, health"). Owner: core."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from radar import __version__
from radar.config import get_settings
from radar.models import utcnow
from radar.schemas import Health

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


def _active_embedder_id() -> str:
    """The active embedder's model id; the configured default until the registry is available."""
    settings = get_settings()
    try:
        from radar.embeddings.registry import get_embedder

        return str(get_embedder().model_id)
    except NotImplementedError:
        return settings.embedder
    except Exception:  # health must never fail because of the embedder
        log.warning("embedder lookup failed in /api/health")
        return settings.embedder


@router.get("/health", response_model=Health)
def health() -> Health:
    """Liveness and basic runtime facts. Needs no session (SPEC §5.1)."""
    settings = get_settings()
    return Health(
        status="ok",
        version=__version__,
        db=settings.db_backend,
        offline=settings.offline,
        embedder=_active_embedder_id(),
        auth=settings.auth,
        time=utcnow(),
    )
