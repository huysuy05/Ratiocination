"""Paper detail, library, search, read marks and disruption (API.md "Papers and library"). Owner: library.

* Papers are public; project fields (state, the latest recommendation) appear only for the
  signed-in user's own project (another user's project id is a 404).
* The library is a project's relevant + saved papers, newest label first, with a keyset cursor over
  ``(updated_at, paper_id)``; ``query`` filters title/abstract case-insensitively.
* Search matches an identifier exactly (DOI, arXiv id/URL, OpenAlex id) or else titles, then
  abstracts, newest first. Everything is local: the query never leaves the server.
* Read marks belong to the user, not to a project.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from radar.api.deps import OwnerId, decode_cursor, encode_cursor, get_project_or_404, split_csv
from radar.api.serializers import disruption_detail, paper_detail, paper_summaries
from radar.db import session_scope
from radar.errors import ApiError, validation_error
from radar.models import Paper, PaperIdentifier, PaperRead, ProjectPaperState, utcnow
from radar.schemas import (
    DisruptionDetail,
    LibraryItem,
    LibraryPage,
    PaperDetail,
    ReadMark,
    ReadRequest,
    SearchResults,
)
from radar.store.identifiers import parse_identifier
from radar.store.papers import get_paper_by_identifier, upsert_paper
from radar.store.states import LIBRARY_STATES

router = APIRouter(prefix="/api", tags=["papers"])


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _text_filter(query: str):  # type: ignore[no-untyped-def]
    pattern = _like(query.strip())
    return or_(Paper.canonical_title.ilike(pattern, escape="\\"), Paper.abstract.ilike(pattern, escape="\\"))


@router.get("/papers/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: int, owner_id: OwnerId, project_id: int | None = None) -> PaperDetail:
    """``200 PaperDetail`` / 404."""
    with session_scope() as session:
        if project_id is not None:
            get_project_or_404(session, project_id, owner_id)
        return paper_detail(session, paper_id, project_id, user_id=owner_id)


def _library_filters(
    session: Session, project_id: int, owner_id: int, states: list[str], read: str, query: str | None
) -> list[object]:
    filters: list[object] = [
        ProjectPaperState.project_id == project_id,
        ProjectPaperState.state.in_(states),
    ]
    marked = exists().where(PaperRead.user_id == owner_id, PaperRead.paper_id == ProjectPaperState.paper_id)
    if read == "read":
        filters.append(marked)
    elif read == "unread":
        filters.append(~marked)
    if query and query.strip():
        filters.append(_text_filter(query))
    return filters


@router.get("/papers", response_model=LibraryPage)
def list_library(
    project_id: int,
    owner_id: OwnerId,
    states: str | None = None,
    read: str = Query("any", pattern="^(any|read|unread)$"),
    query: str | None = Query(None, max_length=500),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> LibraryPage:
    """``200 LibraryPage``: the project's library."""
    wanted = split_csv(states, allowed=LIBRARY_STATES, param="states", default=list(LIBRARY_STATES))
    after = decode_cursor(cursor)
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
        filters = _library_filters(session, project_id, owner_id, wanted, read, query)
        base = select(ProjectPaperState).join(Paper, Paper.id == ProjectPaperState.paper_id).where(*filters)
        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        page = base
        if after is not None:
            try:
                after_at = datetime.fromisoformat(str(after["u"]))
                after_id = int(after["i"])
            except (KeyError, TypeError, ValueError):
                raise validation_error(
                    [{"path": "cursor", "message": "Invalid cursor"}], "Invalid cursor"
                ) from None
            page = page.where(
                or_(
                    ProjectPaperState.updated_at < after_at,
                    and_(ProjectPaperState.updated_at == after_at, ProjectPaperState.paper_id < after_id),
                )
            )
        rows = list(
            session.scalars(
                page.order_by(ProjectPaperState.updated_at.desc(), ProjectPaperState.paper_id.desc()).limit(
                    limit + 1
                )
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        summaries = paper_summaries(session, [r.paper_id for r in rows], user_id=owner_id)
        items = [
            LibraryItem(
                paper=summaries[r.paper_id],
                state=r.state,
                updated_at=r.updated_at,
                last_interaction_id=r.last_interaction_id,
            )
            for r in rows
            if r.paper_id in summaries and r.state is not None
        ]
    next_cursor = (
        encode_cursor({"u": rows[-1].updated_at.isoformat(), "i": rows[-1].paper_id})
        if more and rows
        else None
    )
    return LibraryPage(items=items, next_cursor=next_cursor, total=int(total))


@router.get("/search", response_model=SearchResults)
def search_papers(
    owner_id: OwnerId,
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=50),
) -> SearchResults:
    """``200 { items }``: an exact identifier match, else title/abstract matches."""
    text = query.strip()
    if not text:
        return SearchResults(items=[])
    with session_scope() as session:
        parsed = parse_identifier(text)
        if parsed is not None:
            paper = get_paper_by_identifier(session, *parsed)
            ids = [paper.id] if paper is not None else []
        else:
            pattern = _like(text)
            title_hit = Paper.canonical_title.ilike(pattern, escape="\\")
            ids = list(
                session.scalars(
                    select(Paper.id)
                    .where(
                        Paper.status != "withdrawn",
                        or_(title_hit, Paper.abstract.ilike(pattern, escape="\\")),
                    )
                    .order_by(
                        case((title_hit, 0), else_=1),
                        Paper.publication_date.desc().nulls_last(),
                        Paper.id.desc(),
                    )
                    .limit(limit)
                )
            )
        summaries = paper_summaries(session, ids, user_id=owner_id)
        return SearchResults(items=[summaries[i] for i in ids if i in summaries])


@router.put("/papers/{paper_id}/read", response_model=ReadMark)
def set_read(paper_id: int, body: ReadRequest, owner_id: OwnerId) -> ReadMark:
    """``200 ReadMark``."""
    with session_scope(write=True) as session:
        if session.get(Paper, paper_id) is None:
            raise ApiError(404, "not_found", "Paper not found")
        row = session.scalar(
            select(PaperRead).where(PaperRead.user_id == owner_id, PaperRead.paper_id == paper_id)
        )
        if body.read:
            if row is None:
                row = PaperRead(user_id=owner_id, paper_id=paper_id, read_at=utcnow())
                session.add(row)
                session.flush()
            return ReadMark(paper_id=paper_id, read_at=row.read_at)
        if row is not None:
            session.delete(row)
        return ReadMark(paper_id=paper_id, read_at=None)


@router.post("/papers/{paper_id}/disruption", response_model=DisruptionDetail)
def compute_disruption(paper_id: int, owner_id: OwnerId, force: bool = False) -> DisruptionDetail:
    """``200 DisruptionDetail``: computed from three OpenAlex counts (cached 30 days, SPEC §13).

    422 when the paper has no usable identifier or OpenAlex does not know it; 503
    ``source_unavailable``; 429 ``budget_exceeded``. The lookup runs outside any transaction.
    """
    from radar.connectors import openalex
    from radar.graph import disruption
    from radar.graph.expand import openalex_key
    from radar.net.http import BudgetExceeded, EgressBlocked, SourceUnavailable

    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise ApiError(404, "not_found", "Paper not found")
        if not force and disruption.is_fresh(paper.disruption_at):
            detail = disruption_detail(paper)
            if detail is not None:
                return detail
        identifiers = dict(
            session.execute(
                select(PaperIdentifier.scheme, PaperIdentifier.value).where(
                    PaperIdentifier.paper_id == paper_id, PaperIdentifier.is_primary.is_(True)
                )
            ).all()
        )
    key = openalex_key(identifiers)
    if key is None:
        raise ApiError(422, "validation_error", "This paper has no DOI, arXiv id or OpenAlex id to look up")
    try:
        work = openalex.get_work(key)
        if work is None:
            raise ApiError(
                422, "validation_error", "OpenAlex does not know this paper yet; try again in a few days"
            )
        result = disruption.compute(work.openalex_id, work.referenced_works)
    except BudgetExceeded:
        raise ApiError(
            429, "budget_exceeded", "The OpenAlex daily budget is used up; try again tomorrow"
        ) from None
    except (SourceUnavailable, EgressBlocked):
        raise ApiError(
            503, "source_unavailable", "OpenAlex is unavailable right now; try again later"
        ) from None
    with session_scope(write=True) as session:
        if "openalex" not in identifiers:
            upsert_paper(session, work.to_record())
        disruption.store(session, paper_id, result)
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        detail = disruption_detail(paper) if paper is not None else None
        if detail is None:  # pragma: no cover - just stored
            raise ApiError(404, "not_found", "Paper not found")
        return detail
