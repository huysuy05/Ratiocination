"""Feedback side effects (SPEC §11, §12). Owner: jobs.

``maybe_queue_expansion(session, project_id, paper_id, action)`` queues an AUTO graph expansion after
``relevant``/``save`` feedback (the feed router calls it with its write session, right after recording
the interaction). It returns the new job id, or ``None`` when any rule says no:

1. the action is ``relevant`` or ``save``;
2. ``users.preferences_json.auto_expand`` of the project's owner is true (missing = true, the API
   default);
3. the paper was not expanded in the last 7 days (``paper_expansions.last_expanded_at``);
4. no ``expand`` job for the paper is queued or running (any project);
5. fewer than ``RADAR_MAX_EXPANSIONS_PER_DAY`` AUTO ``expand`` jobs were created today (UTC, all
   users); manual expansions do not count here (they have a per-user quota).

The job is ``expand`` with ``params={"paper_id": id, "auto": true}``, the project as ``project_id``
(cost attribution) and the project's owner as ``owner_id``, inserted in the caller's session, so it is
dispatched only after that session commits. ``params_json`` is filtered in Python, never in SQL
(CONVENTIONS "Database rules").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.config import get_settings
from radar.jobs.runner import ACTIVE_STATUSES, submit_job
from radar.models import Job, PaperExpansion, Project, User, utcnow

AUTO_EXPAND_ACTIONS: frozenset[str] = frozenset({"relevant", "save"})
RECENT_EXPANSION = timedelta(days=7)


def _now() -> datetime:
    """Current aware UTC time (tests monkeypatch this)."""
    return utcnow()


def auto_expand_enabled(session: Session, project_id: int) -> bool:
    """The project owner's ``auto_expand`` preference (default true); false for unknown projects."""
    owner_id = session.scalar(select(Project.owner_id).where(Project.id == project_id))
    if owner_id is None:
        return False
    prefs = session.scalar(select(User.preferences_json).where(User.id == owner_id))
    value = dict(prefs or {}).get("auto_expand", True)
    return value is True


def expanded_recently(session: Session, paper_id: int, now: datetime) -> bool:
    """True when the paper was expanded within the last 7 days."""
    last = session.scalar(select(PaperExpansion.last_expanded_at).where(PaperExpansion.paper_id == paper_id))
    return last is not None and last >= now - RECENT_EXPANSION


def has_active_expand_job(session: Session, paper_id: int) -> bool:
    """True when an ``expand`` job for this paper is queued or running (any project)."""
    rows = session.scalars(
        select(Job.params_json).where(Job.kind == "expand", Job.status.in_(ACTIVE_STATUSES))
    ).all()
    return any(dict(params or {}).get("paper_id") == paper_id for params in rows)


def auto_expansions_today(session: Session, now: datetime) -> int:
    """Number of AUTO ``expand`` jobs created since today's 00:00 UTC (any status)."""
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.scalars(
        select(Job.params_json).where(Job.kind == "expand", Job.created_at >= day_start)
    ).all()
    return sum(1 for params in rows if dict(params or {}).get("auto") is True)


def maybe_queue_expansion(session: Session, project_id: int, paper_id: int, action: str) -> int | None:
    """Queue an AUTO expansion for relevant/save feedback when allowed; returns the job id or ``None``."""
    if action not in AUTO_EXPAND_ACTIONS:
        return None
    if not auto_expand_enabled(session, project_id):
        return None
    now = _now()
    if expanded_recently(session, paper_id, now):
        return None
    if has_active_expand_job(session, paper_id):
        return None
    cap = get_settings().max_expansions_per_day
    if cap <= 0 or auto_expansions_today(session, now) >= cap:
        return None
    owner_id = session.scalar(select(Project.owner_id).where(Project.id == project_id))
    return submit_job(
        "expand",
        project_id=project_id,
        params={"paper_id": paper_id, "auto": True},
        session=session,
        owner_id=owner_id,
    )


__all__ = [
    "AUTO_EXPAND_ACTIONS",
    "auto_expand_enabled",
    "auto_expansions_today",
    "expanded_recently",
    "has_active_expand_job",
    "maybe_queue_expansion",
]
