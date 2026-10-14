"""Daily pipeline and deck builds (SPEC §15). Owner: rank.

* ``build_deck(mode, prepare)``: with ``prepare`` (a new project, or new categories/window) it first
  harvests the project's window from arXiv (backfill); then ``ranking.slate.build_slate``. Afterwards
  it queues an ``enrich`` job for the deck's papers that OpenAlex has not seen yet, so topics and
  citations arrive without delaying the deck.
* ``daily``: arXiv feeds -> backfill of projects whose window is thin -> OpenAlex enrichment ->
  cleanup of expired sessions and links -> one ``build_deck`` job per active project (queued, never
  awaited). A failing step is recorded in the stats and the next steps still run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from radar.models import Paper, Project, RecommendationItem, utcnow

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

THIN_WINDOW = 50
"""A project whose window holds fewer papers than this gets a backfill in the daily run."""


def _scaled(ctx: JobContext, start: float, span: float) -> JobContext:
    """A child context whose progress fractions map into ``[start, start + span]`` of this job."""
    child = ctx.child()

    def progress(fraction: float | None, message: str | None = None) -> None:
        ctx.progress(None if fraction is None else start + span * max(0.0, min(1.0, fraction)), message)

    child.progress = progress  # type: ignore[method-assign]
    return child


def _step(stats: dict[str, Any], name: str, run: Callable[[], Any]) -> Any:
    """Run one sub-step; a failure is recorded (error code only) instead of ending the job."""
    from radar.net.http import BudgetExceeded, EgressBlocked, SourceUnavailable

    try:
        result = run()
    except (SourceUnavailable, EgressBlocked, BudgetExceeded) as exc:
        log.warning("step %s failed: %s", name, type(exc).__name__)
        stats[name] = {"error": type(exc).__name__}
        return None
    except Exception:
        log.exception("step %s failed unexpectedly", name)
        stats[name] = {"error": "internal_error"}
        return None
    stats[name] = result
    return result


def run_build_deck(ctx: JobContext, mode: str = "daily", prepare: bool = False) -> dict[str, Any]:
    """Build a deck for ``ctx.project_id``; ``prepare`` first backfills the project's window."""
    from radar.ingestion.backfill import run_backfill
    from radar.jobs.runner import submit_job
    from radar.ranking.slate import build_slate

    if ctx.project_id is None:
        raise ValueError("build_deck needs a project")
    stats: dict[str, Any] = {"mode": mode}
    deck_start = 0.0
    if prepare:
        ctx.progress(0.0, "Importing recent papers from arXiv")
        _step(stats, "backfill", lambda: run_backfill(_scaled(ctx, 0.0, 0.6)))
        deck_start = 0.6
    slate_id = build_slate(
        ctx.project_id,
        mode,
        progress=lambda fraction, message: ctx.progress(deck_start + (1 - deck_start) * fraction, message),
    )
    stats["slate_id"] = slate_id
    with ctx.session(write=False) as session:
        unenriched = list(
            session.scalars(
                select(RecommendationItem.paper_id)
                .join(Paper, Paper.id == RecommendationItem.paper_id)
                .where(RecommendationItem.slate_id == slate_id, Paper.openalex_checked_at.is_(None))
                .order_by(RecommendationItem.rank)
            )
        )
    if unenriched:
        stats["enrich_job_id"] = submit_job(
            "enrich", params={"paper_ids": unenriched}, owner_id=ctx.owner_id, dedupe=True
        )
    return stats


def run_daily(ctx: JobContext) -> dict[str, Any]:
    """The daily pipeline (module docstring); returns per-step stats."""
    from radar.api.serializers import project_settings
    from radar.auth.sessions import purge_expired
    from radar.ingestion.backfill import run_backfill
    from radar.ingestion.enrich import run_enrich
    from radar.ingestion.feeds import run_ingest_feeds
    from radar.jobs.runner import submit_job
    from radar.ranking.slate import window_candidates

    stats: dict[str, Any] = {}
    ctx.progress(0.05, "Fetching today's arXiv feeds")
    _step(stats, "ingest_feeds", lambda: run_ingest_feeds(ctx.child()))

    with ctx.session(write=False) as session:
        projects = list(session.scalars(select(Project).where(Project.active.is_(True)).order_by(Project.id)))
        today = utcnow().date()
        plans: list[tuple[int, int, bool]] = []  # (project id, owner id, thin window)
        for project in projects:
            settings = project_settings(project)
            if not settings.arxiv_categories:
                continue
            _, n_window = window_candidates(session, project.id, settings, "daily", today)
            plans.append((project.id, project.owner_id, n_window < THIN_WINDOW))

    for index, (project_id, _owner, thin) in enumerate(plans):
        if thin:
            ctx.progress(0.2 + 0.3 * index / max(1, len(plans)), f"Backfilling project {project_id}")
            _step(
                stats,
                f"backfill:{project_id}",
                lambda pid=project_id: run_backfill(ctx.child(project_id=pid)),
            )

    ctx.progress(0.5, "Enriching new papers from OpenAlex")
    _step(stats, "enrich", lambda: run_enrich(ctx.child(), limit=300))

    with ctx.session(write=True) as session:
        stats["purged"] = purge_expired(session)

    queued = [
        submit_job("build_deck", project_id=project_id, params={"mode": "daily"}, owner_id=owner)
        for project_id, owner, _thin in plans
    ]
    stats["decks_queued"] = len(queued)
    ctx.progress(1.0, f"Daily run finished; {len(queued)} decks queued")
    return stats
