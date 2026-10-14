"""Job ``backfill`` (SPEC §8). Owner: ingest.

Fills a project's time window so a new project has candidates on day one. Source: arXiv OAI-PMH
(``arXivRaw``), harvested per OAI set covering the project's categories (``cs.IR`` -> set ``cs``).
OAI-PMH selects by datestamp (last change), so each page is filtered to papers whose v1 date falls in
the window and that carry at least one of the project's categories. Decision 2026-10-12: the arXiv
query API throttled us (30 s hang, then HTTP 429) while OAI-PMH, arXiv's bulk-metadata interface,
answered in seconds.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from radar.api.serializers import project_settings
from radar.connectors.arxiv_oai import harvest, set_for_category
from radar.ingestion._upsert import UpsertCounts, upsert_records
from radar.models import Project, utcnow
from radar.net.http import SourceUnavailable

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)


def run_backfill(ctx: JobContext) -> dict[str, Any]:
    """Harvest the project's categories for the last ``time_window_days`` (OAI-PMH, see module doc)."""
    if ctx.project_id is None:
        raise ValueError("backfill needs a project")
    with ctx.session(write=False) as session:
        project = session.get(Project, ctx.project_id)
        if project is None:
            raise LookupError(f"project {ctx.project_id} not found")
        settings = project_settings(project)
    categories = set(settings.arxiv_categories)
    if not categories:
        return {"categories": 0, "skipped": "project has no arXiv categories"}

    until = utcnow().date()
    since = until - timedelta(days=settings.time_window_days)
    sets = sorted({set_for_category(c) for c in categories})
    totals = UpsertCounts()
    scanned = pages = 0
    failed: list[str] = []
    for index, set_spec in enumerate(sets):
        try:
            for page in harvest(set_spec, since, until, job_id=ctx.job_id, project_id=ctx.project_id):
                pages += 1
                scanned += page.raw_count
                wanted = [
                    record
                    for record in page.records
                    if record.publication_date is not None
                    and record.publication_date >= since
                    and categories.intersection(record.categories)
                ]
                totals.add(upsert_records(ctx, wanted))
                ctx.progress(
                    min(0.95, (index + min(pages, 10) / 10) / len(sets)),
                    f"Harvesting arXiv ({set_spec}): {scanned} records scanned, {totals.records} in your categories",
                )
        except SourceUnavailable as exc:
            log.warning("arXiv OAI-PMH set %s unavailable: %s", set_spec, exc)
            failed.append(set_spec)

    if failed and len(failed) == len(sets):
        raise SourceUnavailable("arxiv", message="arXiv OAI-PMH harvesting failed")
    ctx.progress(1.0, f"Found {totals.records} recent papers in your categories ({totals.created} new)")
    return {
        "categories": len(categories),
        "sets": len(sets),
        "failed_sets": len(failed),
        "pages": pages,
        "scanned": scanned,
        "since": since.isoformat(),
        "until": until.isoformat(),
        **totals.as_stats(),
    }
