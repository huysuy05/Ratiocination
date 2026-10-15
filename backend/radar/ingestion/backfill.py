"""Job ``backfill`` (SPEC §6). Owner: ingest.

Fills a project's time window so a new project has candidates on day one. Two sources, in order:

1. **arXiv OAI-PMH** (``arXivRaw``), harvested per OAI set covering the project's categories
   (``cs.IR`` -> set ``cs``). OAI-PMH selects by datestamp (last change), so each page is filtered to
   papers whose v1 date falls in the window and that carry at least one of the project's categories.
   Decision 2026-10-12: the arXiv query API throttled us (30 s hang, then HTTP 429) while OAI-PMH,
   arXiv's bulk-metadata interface, answered in seconds.
2. **arXiv query API** (``cat:<category> AND submittedDate:[since TO until]``), for the categories of
   every OAI set that failed. Decision 2026-10-14: from the Fly.io machine arXiv throttles bulk OAI
   harvesting - every ``ListRecords`` request hung until the read timeout (90 s and 120 s both) while
   the query API answered in 3.6 s - so a project could never get its first papers. The API is scoped
   to one category and the window server-side, so its answers need no further filtering.

``stats["source"]`` is ``arxiv_oai``, ``arxiv_api`` or ``arxiv_oai+arxiv_api``, the final progress
message names the API when it was used, and falling back is logged as a warning: no silent degradation.
The job fails ``source_unavailable`` only when both sources are unavailable, and ``RADAR_OFFLINE=1``
blocks both (``EgressBlocked`` from the first request, before any fallback).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from radar.api.serializers import project_settings
from radar.connectors.arxiv_api import query_recent
from radar.connectors.arxiv_oai import harvest, set_for_category
from radar.ingestion._upsert import UpsertCounts, upsert_records
from radar.models import Project, utcnow
from radar.net.http import SourceUnavailable

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

API_PAGE_SIZE = 200
"""Results per fallback request. arXiv answers a slice this size in a few seconds; the shared
HttpClient keeps one request every 3 s on ``export.arxiv.org`` (arXiv's terms), so pages are not free."""

API_MAX_PAGES = 5
"""Pages per category (so at most 1000 papers per category): a window of a busy category holds a few
hundred papers, and the deck only ranks the window anyway."""


def run_backfill(ctx: JobContext) -> dict[str, Any]:
    """Harvest the project's categories for the last ``time_window_days`` (sources: see module doc)."""
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
    by_set: dict[str, list[str]] = {}
    for category in sorted(categories):
        by_set.setdefault(set_for_category(category), []).append(category)
    sets = sorted(by_set)

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

    source = "" if len(failed) == len(sets) else "arxiv_oai"
    ctx.log_stat("failed_sets", len(failed))  # logged, not only returned: a failing job keeps its stats
    stats: dict[str, Any] = {
        "categories": len(categories),
        "sets": len(sets),
        "failed_sets": len(failed),
        "pages": pages,
        "scanned": scanned,
        "since": since.isoformat(),
        "until": until.isoformat(),
    }
    if failed:
        fallback = sorted({category for set_spec in failed for category in by_set[set_spec]})
        log.warning(
            "arXiv OAI-PMH unavailable for %d of %d set(s); falling back to the arXiv query API for "
            "%d categor(ies) of project %s",
            len(failed),
            len(sets),
            len(fallback),
            ctx.project_id,
        )
        source = _run_fallback(ctx, fallback, since, until, totals, stats, source)

    stats["source"] = source
    ctx.progress(1.0, _final_message(totals, source))
    return {**stats, **totals.as_stats()}


def _run_fallback(
    ctx: JobContext,
    categories: list[str],
    since: date,
    until: date,
    totals: UpsertCounts,
    stats: dict[str, Any],
    source: str,
) -> str:
    """Harvest ``categories`` from the arXiv API into ``totals``/``stats``; returns the source label.

    Raises ``SourceUnavailable`` only when OAI-PMH harvested nothing either (``source`` empty): a
    partial OAI harvest keeps its papers and records ``api_failed`` instead of failing the job.
    """
    stats["api_categories"] = len(categories)
    ctx.log_stat("api_categories", len(categories))
    try:
        api_totals, queries, api_failed = _harvest_api(ctx, categories, since, until)
    except SourceUnavailable as exc:
        log.warning("arXiv API fallback unavailable as well: %s", exc)
        stats["api_failed"] = True
        ctx.log_stat("api_failed", True)
        if not source:
            raise SourceUnavailable(
                "arxiv", message="arXiv OAI-PMH harvesting failed and the arXiv API is unavailable too"
            ) from None
        return source
    stats["api_queries"] = queries
    stats["api_failed_categories"] = len(api_failed)
    stats.update(api_totals.as_stats("api_"))
    totals.add(api_totals)
    return f"{source}+arxiv_api" if source else "arxiv_api"


def _harvest_api(
    ctx: JobContext, categories: list[str], since: date, until: date
) -> tuple[UpsertCounts, int, list[str]]:
    """Query the arXiv API per category (paged) and upsert the answers; no local filtering is needed.

    Returns ``(counts, requests sent, categories that failed part-way)``. Raises ``SourceUnavailable``
    when the very first request fails, since then nothing was imported at all.
    """
    totals = UpsertCounts()
    queries = 0
    failed: list[str] = []
    for index, category in enumerate(categories):
        for page in range(API_MAX_PAGES):
            try:
                records = query_recent(
                    category,
                    since,
                    until,
                    API_PAGE_SIZE,
                    start=page * API_PAGE_SIZE,
                    job_id=ctx.job_id,
                    project_id=ctx.project_id,
                )
            except SourceUnavailable:
                if queries == 0:
                    raise
                log.warning(
                    "arXiv API: %s page %d unavailable; keeping what was imported", category, page + 1
                )
                failed.append(category)
                break
            queries += 1
            totals.add(upsert_records(ctx, records))
            ctx.progress(
                min(0.95, (index + (page + 1) / API_MAX_PAGES) / len(categories)),
                f"Importing recent {category} papers from the arXiv API: {totals.records} found",
            )
            if len(records) < API_PAGE_SIZE:
                break
    return totals, queries, failed


def _final_message(totals: UpsertCounts, source: str) -> str:
    found = f"Found {totals.records} recent papers in your categories ({totals.created} new)"
    if source == "arxiv_api":
        return f"{found}, imported from the arXiv API because bulk harvesting was unavailable"
    if source.endswith("+arxiv_api"):
        return f"{found}, partly from the arXiv API because bulk harvesting was incomplete"
    return found
