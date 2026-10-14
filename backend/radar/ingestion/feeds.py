"""Job ``ingest_feeds`` (SPEC §8). Owner: ingest.

Reads the arXiv category feeds for the union of all active projects' categories (≤10 per request),
upserts every entry and remembers the feed-level ``<updated>`` as a watermark per query key, so an
unchanged feed is skipped. Network calls happen outside any write transaction; each upsert batch is a
short write transaction (SPEC §2.1). Re-running is idempotent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.serializers import project_settings
from radar.connectors.arxiv_feed import MAX_CATEGORIES_PER_REQUEST, fetch_feed
from radar.ingestion._upsert import UpsertCounts, upsert_records
from radar.models import IngestionWatermark, Project
from radar.net.http import SourceUnavailable

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

WATERMARK_SOURCE = "arxiv_feed"


def active_categories(session: Session) -> list[str]:
    """Sorted union of the arXiv categories of all active projects."""
    categories: set[str] = set()
    for project in session.scalars(select(Project).where(Project.active.is_(True))):
        categories.update(project_settings(project).arxiv_categories)
    return sorted(categories)


def _set_watermark(session: Session, query_key: str, watermark: str) -> None:
    row = session.scalar(
        select(IngestionWatermark).where(
            IngestionWatermark.source == WATERMARK_SOURCE, IngestionWatermark.query_key == query_key
        )
    )
    if row is None:
        session.add(IngestionWatermark(source=WATERMARK_SOURCE, query_key=query_key, watermark=watermark))
    elif row.watermark != watermark:
        row.watermark = watermark


def run_ingest_feeds(ctx: JobContext) -> dict[str, Any]:
    """Union of active projects' categories -> feeds -> upsert; watermark per query key."""
    with ctx.session(write=False) as session:
        categories = active_categories(session)
        marks = {
            row.query_key: row.watermark
            for row in session.scalars(
                select(IngestionWatermark).where(IngestionWatermark.source == WATERMARK_SOURCE)
            )
        }
    if not categories:
        return {"feeds": 0, "skipped": "no active project has arXiv categories"}

    groups = [
        categories[i : i + MAX_CATEGORIES_PER_REQUEST]
        for i in range(0, len(categories), MAX_CATEGORIES_PER_REQUEST)
    ]
    totals = UpsertCounts()
    entries = unchanged_feeds = 0
    failed: list[str] = []
    for index, group in enumerate(groups):
        query_key = "+".join(group)
        shown = ", ".join(group[:3]) + ("…" if len(group) > 3 else "")
        ctx.progress(index / len(groups), f"Reading arXiv feed {index + 1} of {len(groups)} ({shown})")
        try:
            feed = fetch_feed(group, job_id=ctx.job_id, project_id=ctx.project_id)
        except SourceUnavailable as exc:
            log.warning("arXiv feed %s unavailable: %s", query_key, exc)
            failed.append(query_key)
            continue
        entries += feed.raw_count
        if feed.updated is not None and marks.get(query_key) == feed.updated:
            unchanged_feeds += 1
            continue
        totals.add(upsert_records(ctx, feed.records))
        if feed.updated is not None:
            with ctx.session(write=True) as session:
                _set_watermark(session, query_key, feed.updated)

    if failed and len(failed) == len(groups):
        raise SourceUnavailable("arxiv", message=f"all {len(groups)} arXiv feed requests failed")
    ctx.progress(1.0, f"Read {len(groups)} arXiv feed(s): {totals.created} new papers")
    return {
        "feeds": len(groups),
        "unchanged_feeds": unchanged_feeds,
        "failed_feeds": len(failed),
        "entries": entries,
        **totals.as_stats(),
    }
