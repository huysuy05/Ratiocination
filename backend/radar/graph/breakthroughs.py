"""Breakthrough analysis of a paper's authors (SPEC §13). Owner: graph.

Job ``breakthroughs`` (``run_breakthroughs(ctx, paper_id, force=False)``):

1. look the paper up in OpenAlex (free) and store its authors' OpenAlex ids;
2. fetch the authors (one list call) as ``scholars`` with AUTHORED edges to the paper;
3. CD index of the paper itself (three list calls, skipped when computed in the last 30 days);
4. for the first ``MAX_AUTHORS`` authors plus the last author: their ``WORKS_PER_AUTHOR`` most-cited
   works (one list call each, ``referenced_works`` included) as stub papers with AUTHORED edges, and
   the CD index of each (three list calls, skipped when fresh).

About 150 list calls (~$0.015) per paper. The OpenAlex budget stop ends the job early but cleanly:
everything computed so far is kept and the stats say how far it got. An author's "breakthroughs" are
their analyzed works ordered by CD (positive = disruptive).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from radar.connectors import openalex
from radar.connectors.openalex import OpenAlexAuthor, OpenAlexWork
from radar.graph import disruption
from radar.graph.expand import openalex_key
from radar.models import Paper, PaperIdentifier, Scholar, utcnow
from radar.net.http import BudgetExceeded
from radar.store.graph import upsert_edge
from radar.store.papers import attach_openalex_author_ids, upsert_paper

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

MAX_AUTHORS = 6
WORKS_PER_AUTHOR = 8


def chosen_authors(author_ids: list[str | None]) -> list[tuple[int, str]]:
    """``(position, author id)`` for the first ``MAX_AUTHORS`` known authors plus the last author."""
    known = [(position, aid) for position, aid in enumerate(author_ids) if aid]
    chosen = known[:MAX_AUTHORS]
    if known and known[-1] not in chosen:
        chosen.append(known[-1])
    return chosen


def upsert_scholar(session: Session, author: OpenAlexAuthor) -> Scholar:
    row = session.scalar(select(Scholar).where(Scholar.openalex_id == author.openalex_id))
    if row is None:
        row = Scholar(openalex_id=author.openalex_id, display_name=author.display_name)
        session.add(row)
    row.display_name = author.display_name
    row.orcid = author.orcid
    row.works_count = author.works_count
    row.cited_by_count = author.cited_by_count
    row.h_index = author.h_index
    row.last_known_institution = author.institution
    row.fetched_at = utcnow()
    session.flush()
    return row


def _authored(session: Session, scholar_id: int, paper_id: int, position: int | None) -> None:
    upsert_edge(
        session,
        src=("scholar", scholar_id),
        relation="AUTHORED",
        dst=("work", paper_id),
        provenance="openalex",
        verified=True,
        evidence={"position": position},
    )


def _store_work(session: Session, work: OpenAlexWork) -> int:
    record = work.to_record()
    record.status = "stub"  # an existing active paper stays active
    paper_id = upsert_paper(session, record).paper_id
    attach_openalex_author_ids(session, paper_id, work.authors, work.author_ids)
    return paper_id


def _disruption_fresh(ctx: JobContext, paper_id: int) -> bool:
    with ctx.session(write=False) as session:
        computed = session.scalar(select(Paper.disruption_at).where(Paper.id == paper_id))
    return disruption.is_fresh(computed)


def _measure(ctx: JobContext, paper_ids: list[int], work: OpenAlexWork, force: bool) -> bool:
    """Compute the work's CD index and store it on ``paper_ids``; False when fresh and skipped."""
    if not force and all(_disruption_fresh(ctx, pid) for pid in paper_ids):
        return False
    result = disruption.compute(
        work.openalex_id, work.referenced_works, job_id=ctx.job_id, project_id=ctx.project_id
    )
    with ctx.session(write=True) as session:
        for pid in paper_ids:
            disruption.store(session, pid, result)
    return True


def run_breakthroughs(ctx: JobContext, paper_id: int, force: bool = False) -> dict[str, Any]:
    """Job ``breakthroughs`` (module docstring)."""
    with ctx.session(write=False) as session:
        if session.get(Paper, paper_id) is None:
            raise LookupError(f"paper {paper_id} not found")
        identifiers = dict(
            session.execute(
                select(PaperIdentifier.scheme, PaperIdentifier.value).where(
                    PaperIdentifier.paper_id == paper_id, PaperIdentifier.is_primary.is_(True)
                )
            ).all()
        )
    stats: dict[str, Any] = {"authors": 0, "authors_done": 0, "works": 0, "measured": 0, "stopped": None}
    key = openalex_key(identifiers)
    if key is None:
        stats["stopped"] = "This paper has no DOI, arXiv id or OpenAlex id"
        return stats
    ctx.progress(0.02, "Looking the paper up in OpenAlex")
    work = openalex.get_work(key, job_id=ctx.job_id, project_id=ctx.project_id)
    if work is None:
        stats["stopped"] = "OpenAlex does not know this paper yet"
        return stats
    authors = chosen_authors(work.author_ids)
    stats["authors"] = len(authors)
    try:
        with ctx.session(write=True) as session:
            target = upsert_paper(session, work.to_record()).paper_id
            attach_openalex_author_ids(session, target, work.authors, work.author_ids)
        ctx.progress(0.05, "Fetching the authors")
        profiles = {
            a.openalex_id: a for a in openalex.get_authors([aid for _, aid in authors], job_id=ctx.job_id)
        }
        scholars: dict[str, int] = {}
        with ctx.session(write=True) as session:
            for position, aid in authors:
                profile = profiles.get(aid)
                if profile is None:
                    continue
                scholar = upsert_scholar(session, profile)
                scholars[aid] = scholar.id
                for pid in dict.fromkeys((paper_id, target)):  # a merged duplicate shows it too
                    _authored(session, scholar.id, pid, position)
        ctx.progress(0.1, "Measuring this paper's disruption")
        stats["measured"] += int(_measure(ctx, list(dict.fromkeys((paper_id, target))), work, force))
        for index, (_, aid) in enumerate(authors):
            scholar_id = scholars.get(aid)
            if scholar_id is None:
                continue
            name = profiles[aid].display_name
            ctx.progress(0.12 + 0.86 * index / max(1, len(authors)), f"Analyzing {name}'s most-cited papers")
            top = openalex.get_author_top_works(
                aid, WORKS_PER_AUTHOR, job_id=ctx.job_id, project_id=ctx.project_id
            )
            for top_work in top:
                with ctx.session(write=True) as session:
                    work_id = _store_work(session, top_work)
                    position = top_work.author_ids.index(aid) if aid in top_work.author_ids else None
                    _authored(session, scholar_id, work_id, position)
                stats["works"] += 1
                stats["measured"] += int(_measure(ctx, [work_id], top_work, force))
            with ctx.session(write=True) as session:
                scholar = session.get(Scholar, scholar_id)
                if scholar is not None:
                    scholar.analyzed_at = utcnow()
            stats["authors_done"] += 1
    except BudgetExceeded:
        stats["stopped"] = (
            f"Stopped early: the OpenAlex daily budget was reached ({stats['authors_done']} of "
            f"{stats['authors']} authors analyzed). The rest can be analyzed tomorrow."
        )
        log.warning("breakthroughs for paper %s stopped at the OpenAlex budget", paper_id)
    ctx.progress(
        1.0, stats["stopped"] or f"Analyzed {stats['authors_done']} authors and {stats['works']} papers"
    )
    return stats


__all__ = ["MAX_AUTHORS", "WORKS_PER_AUTHOR", "chosen_authors", "run_breakthroughs", "upsert_scholar"]
