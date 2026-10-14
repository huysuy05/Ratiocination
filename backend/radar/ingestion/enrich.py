"""Job ``enrich`` (SPEC §6). Owner: enrich.

For papers not yet linked to OpenAlex: one free single-work lookup each (by DOI, or by arXiv DOI),
then in one short write transaction per paper: merge the record, link OpenAlex topics (concepts +
``HAS_TOPIC`` edges), link ``CITES`` edges to referenced works already in the database, store the
OpenAlex author id of each author position, and stamp ``openalex_checked_at``. New arXiv papers reach
OpenAlex days later, so a miss is retried after two days and given up after 60. Network calls never
run inside a write transaction (SPEC §2.1).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from radar.connectors import openalex
from radar.connectors.openalex import OpenAlexWork
from radar.models import Paper, PaperIdentifier, utcnow
from radar.net.http import BudgetExceeded, SourceUnavailable
from radar.store.graph import get_or_create_concept, upsert_edge
from radar.store.papers import attach_openalex_author_ids, upsert_paper

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

TOPIC_MIN_SCORE = 0.1
"""OpenAlex lists three topics per work; secondary ones often score ~0 (fixture: 1.0, 0.0, 0.0). The
top topic is always kept; others only at this score or above."""
RETRY_AFTER = timedelta(days=2)
GIVE_UP_AFTER = timedelta(days=60)
MAX_CONSECUTIVE_FAILURES = 5


def _primary(session: Session, paper_ids: list[int], scheme: str) -> dict[int, str]:
    rows = session.execute(
        select(PaperIdentifier.paper_id, PaperIdentifier.value).where(
            PaperIdentifier.paper_id.in_(paper_ids),
            PaperIdentifier.scheme == scheme,
            PaperIdentifier.is_primary.is_(True),
        )
    )
    return {paper_id: value for paper_id, value in rows}


def _openalex_targets(session: Session, paper_ids: list[int] | None, limit: int) -> list[int]:
    has_openalex = exists().where(PaperIdentifier.paper_id == Paper.id, PaperIdentifier.scheme == "openalex")
    query = select(Paper.id).where(~has_openalex)
    if paper_ids is not None:
        query = query.where(Paper.id.in_(paper_ids))
    else:
        now = utcnow()
        query = query.where(
            or_(Paper.openalex_checked_at.is_(None), Paper.openalex_checked_at < now - RETRY_AFTER),
            Paper.first_seen_at >= now - GIVE_UP_AFTER,
        )
    return list(session.scalars(query.order_by(Paper.first_seen_at, Paper.id).limit(limit)))


def _link_topics(session: Session, paper_id: int, work: OpenAlexWork) -> list[tuple[int, float]]:
    """Concepts + HAS_TOPIC edges for the work's topics; returns ``[(concept_id, score)]``."""
    linked: list[tuple[int, float]] = []
    for rank, topic in enumerate(work.topics):
        score = topic.score if topic.score is not None else 0.0
        if rank > 0 and score < TOPIC_MIN_SCORE:
            continue
        concept = get_or_create_concept(
            session,
            "openalex_topic",
            topic.external_id,
            topic.label,
            extra={"subfield": topic.subfield, "field": topic.field, "domain": topic.domain},
        )
        upsert_edge(
            session,
            src=("work", paper_id),
            relation="HAS_TOPIC",
            dst=("concept", concept.id),
            provenance="openalex",
            verified=False,  # OpenAlex's classifier output, not a bibliographic fact
            confidence=topic.score,
            evidence={"score": topic.score, "subfield": topic.subfield, "field": topic.field},
        )
        linked.append((concept.id, score))
    return linked


def _link_citations(session: Session, paper_id: int, work: OpenAlexWork) -> int:
    """``CITES`` edges to referenced works already in the database (both ends known)."""
    if not work.referenced_works:
        return 0
    known = session.execute(
        select(PaperIdentifier.paper_id).where(
            PaperIdentifier.scheme == "openalex", PaperIdentifier.value.in_(work.referenced_works)
        )
    ).scalars()
    count = 0
    for cited_id in set(known):
        if cited_id == paper_id:
            continue
        upsert_edge(
            session,
            src=("work", paper_id),
            relation="CITES",
            dst=("work", cited_id),
            provenance="openalex",
            verified=True,
            evidence={"contexts": [], "intents": [], "is_influential": None},
        )
        count += 1
    return count


def _lookup_key(paper_id: int, dois: dict[int, str], arxiv: dict[int, str]) -> str | None:
    if paper_id in dois:
        return f"doi:{dois[paper_id]}"
    if paper_id in arxiv:
        return f"arxiv:{arxiv[paper_id]}"
    return None


def run_enrich(ctx: JobContext, paper_ids: list[int] | None = None, limit: int = 300) -> dict[str, Any]:
    """OpenAlex lookup, topics, citations, author ids and retractions."""
    ids = [int(p) for p in paper_ids] if paper_ids is not None else None
    with ctx.session(write=False) as session:
        targets = _openalex_targets(session, ids, limit)
        dois = _primary(session, targets, "doi") if targets else {}
        arxiv = _primary(session, targets, "arxiv") if targets else {}

    stats: dict[str, Any] = {
        "openalex_candidates": len(targets),
        "found": 0,
        "not_found": 0,
        "skipped_no_key": 0,
        "failed": 0,
        "budget_stopped": False,
        "topics_linked": 0,
        "citations_linked": 0,
        "author_ids_linked": 0,
        "retracted": 0,
    }
    failures_in_a_row = 0
    for index, paper_id in enumerate(targets):
        if index % 10 == 0:
            ctx.progress(
                index / max(1, len(targets)), f"Enriching papers from OpenAlex ({index}/{len(targets)})"
            )
        key = _lookup_key(paper_id, dois, arxiv)
        if key is None:
            stats["skipped_no_key"] += 1
            continue
        try:
            work = openalex.get_work(key, job_id=ctx.job_id, project_id=ctx.project_id)
        except BudgetExceeded:
            log.warning("enrich: OpenAlex daily budget reached; stopping OpenAlex lookups for this run")
            stats["budget_stopped"] = True
            break
        except SourceUnavailable as exc:
            stats["failed"] += 1
            failures_in_a_row += 1
            log.warning("enrich: OpenAlex lookup for paper %s failed: %s", paper_id, exc)
            if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                log.warning("enrich: %d OpenAlex failures in a row; stopping", failures_in_a_row)
                break
            continue
        failures_in_a_row = 0
        with ctx.session(write=True) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                continue
            paper.openalex_checked_at = utcnow()
            if work is None:
                stats["not_found"] += 1
                continue
            stats["found"] += 1
            target_id = upsert_paper(session, work.to_record()).paper_id
            # Normally target_id == paper_id. When the work's DOI already belongs to another paper the
            # record merges there (and a duplicate candidate is recorded); the looked-up paper still
            # gets its topics.
            for linked_id in dict.fromkeys((paper_id, target_id)):
                stats["topics_linked"] += len(_link_topics(session, linked_id, work))
            stats["citations_linked"] += _link_citations(session, target_id, work)
            stats["author_ids_linked"] += attach_openalex_author_ids(
                session, target_id, work.authors, work.author_ids
            )
            if work.is_retracted:
                stats["retracted"] += 1

    ctx.progress(1.0, f"Enriched {stats['found']} papers ({stats['topics_linked']} topic links)")
    return stats
