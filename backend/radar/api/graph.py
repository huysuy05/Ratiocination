"""Knowledge graph: neighborhood, expansion, project graph, breakthroughs, scholars (API.md
"Knowledge graph"). Owner: graph.

Papers, scholars and their public edges are shared by all users; a ``project_id`` must be the
signed-in user's. Expansion and breakthrough analyses call public APIs, so they have per-user daily
quotas (``RADAR_EXPANSIONS_PER_USER_PER_DAY``, ``RADAR_BREAKTHROUGHS_PER_DAY``); identical active jobs
are reused instead of queued twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.deps import OwnerId, get_project_or_404
from radar.api.serializers import (
    disruption_detail,
    disruption_label,
    expansion_status,
    job_out,
    paper_summaries,
    scholar_summary,
)
from radar.config import get_settings
from radar.db import session_scope
from radar.errors import ApiError
from radar.graph.neighborhood import build_neighborhood, scholar_edges
from radar.graph.project_graph import build_project_graph
from radar.jobs.runner import ACTIVE_STATUSES, submit_job
from radar.models import Job, Paper, Scholar, utcnow
from radar.schemas import (
    BreakthroughWork,
    Breakthroughs,
    ExpandResponse,
    JobAccepted,
    Neighborhood,
    ProjectGraph,
    ScholarBreakthroughs,
    ScholarDetail,
)
from radar.store.graph import edges_for_works

router = APIRouter(prefix="/api", tags=["graph"])

BREAKTHROUGH_MIN_CITING = 10
RECENT_EXPANSION = timedelta(days=7)


def _paper_or_404(session: Session, paper_id: int) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ApiError(404, "not_found", "Paper not found")
    return paper


def _today_start() -> datetime:
    return utcnow().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _jobs_for_paper(session: Session, kind: str, paper_id: int, *, active_only: bool = False) -> list[Job]:
    stmt = select(Job).where(Job.kind == kind)
    if active_only:
        stmt = stmt.where(Job.status.in_(ACTIVE_STATUSES))
    rows = session.scalars(stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(500))
    return [job for job in rows if dict(job.params_json or {}).get("paper_id") == paper_id]


def _jobs_today(session: Session, kind: str, owner_id: int, *, manual_only: bool = False) -> int:
    rows = session.scalars(
        select(Job.params_json).where(
            Job.kind == kind, Job.owner_id == owner_id, Job.created_at >= _today_start()
        )
    ).all()
    return sum(1 for params in rows if not (manual_only and dict(params or {}).get("auto") is True))


# ------------------------------------------------------------------------------------------------
# Neighborhood, expansion, project graph
# ------------------------------------------------------------------------------------------------


@router.get("/papers/{paper_id}/neighborhood", response_model=Neighborhood)
def get_neighborhood(paper_id: int, owner_id: OwnerId, project_id: int | None = None) -> Neighborhood:
    """``200 Neighborhood``."""
    with session_scope() as session:
        _paper_or_404(session, paper_id)
        if project_id is not None:
            get_project_or_404(session, project_id, owner_id)
        return build_neighborhood(session, paper_id, project_id=project_id, owner_id=owner_id)


@router.post("/papers/{paper_id}/expand", response_model=ExpandResponse, status_code=202)
def expand_paper(
    paper_id: int, response: Response, owner_id: OwnerId, project_id: int | None = None, force: bool = False
) -> ExpandResponse:
    """``202 ExpandResponse`` with a job, or ``200`` with ``job: null`` when expanded within 7 days."""
    settings = get_settings()
    with session_scope() as session:
        _paper_or_404(session, paper_id)
        if project_id is not None:
            get_project_or_404(session, project_id, owner_id)
        expansion = expansion_status(session, paper_id)
        active = _jobs_for_paper(session, "expand", paper_id, active_only=True)
        if active:
            return ExpandResponse(
                job=job_out(active[0], hide_project=True), expansion=expansion, message=None
            )
        recent = (
            expansion.last_expanded_at is not None
            and expansion.last_expanded_at > utcnow() - RECENT_EXPANSION
        )
        if recent and not force:
            response.status_code = 200
            return ExpandResponse(
                job=None, expansion=expansion, message="Citations were loaded in the last 7 days"
            )
        if _jobs_today(session, "expand", owner_id, manual_only=True) >= settings.expansions_per_user_per_day:
            raise ApiError(
                429,
                "quota_exceeded",
                f"You can expand {settings.expansions_per_user_per_day} papers per day; try again tomorrow",
            )
    params: dict[str, object] = {"paper_id": paper_id}
    if force:
        params["force"] = True
    job_id = submit_job("expand", project_id=project_id, params=params, owner_id=owner_id)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise ApiError(404, "not_found", "Job not found")
        return ExpandResponse(job=job_out(job), expansion=expansion_status(session, paper_id), message=None)


@router.get("/projects/{project_id}/graph", response_model=ProjectGraph)
def get_project_graph(
    owner_id: OwnerId, project_id: int, limit: int = Query(150, ge=1, le=500)
) -> ProjectGraph:
    """``200 ProjectGraph``."""
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
        return build_project_graph(session, project_id, owner_id, limit)


# ------------------------------------------------------------------------------------------------
# Breakthroughs and scholars
# ------------------------------------------------------------------------------------------------


def _works_of(
    session: Session, scholar_id: int, owner_id: int, *, exclude: int | None = None
) -> list[BreakthroughWork]:
    ids = [e.dst_id for e in scholar_edges(session, scholar_id) if e.dst_id != exclude]
    papers = {p.id: p for p in session.scalars(select(Paper).where(Paper.id.in_(ids or [-1])))}
    summaries = paper_summaries(session, list(papers), user_id=owner_id)
    works = []
    for pid, paper in papers.items():
        if pid not in summaries:
            continue
        n_citing = (paper.disruption_ni or 0) + (paper.disruption_nj or 0) if paper.disruption_at else None
        works.append((paper, summaries[pid], n_citing))
    works.sort(
        key=lambda w: (w[0].disruption_cd is None, -(w[0].disruption_cd or 0.0), -(w[1].cited_by_count or 0))
    )
    out: list[BreakthroughWork] = []
    for index, (paper, summary, n_citing) in enumerate(works):
        label = disruption_label(paper.disruption_cd, paper.disruption_ni or 0, paper.disruption_nj or 0)
        out.append(
            BreakthroughWork(
                paper=summary,
                cd=paper.disruption_cd,
                n_citing=n_citing,
                is_breakthrough=index == 0
                and label == "disruptive"
                and (n_citing or 0) >= BREAKTHROUGH_MIN_CITING,
            )
        )
    return out


def breakthroughs_out(session: Session, paper_id: int, owner_id: int) -> Breakthroughs:
    paper = _paper_or_404(session, paper_id)
    jobs = _jobs_for_paper(session, "breakthroughs", paper_id)
    job = jobs[0] if jobs else None
    authored = [
        e
        for e in edges_for_works(session, [paper_id], {"AUTHORED"})
        if e.src_type == "scholar" and e.dst_id == paper_id
    ]
    authored.sort(
        key=lambda e: ((e.evidence or {}).get("position") is None, (e.evidence or {}).get("position") or 0)
    )
    scholars = {
        s.id: s
        for s in session.scalars(select(Scholar).where(Scholar.id.in_([e.src_id for e in authored] or [-1])))
    }
    groups = [
        ScholarBreakthroughs(
            scholar=scholar_summary(scholars[e.src_id]),
            position=int((e.evidence or {}).get("position") or 0),
            works=_works_of(session, e.src_id, owner_id, exclude=paper_id),
        )
        for e in authored
        if e.src_id in scholars
    ]
    if job is None:
        status = "done" if groups else "none"
    else:
        status = {"queued": "queued", "running": "running", "succeeded": "done", "failed": "failed"}[
            job.status
        ]
    analyzed = [s.analyzed_at for s in scholars.values() if s.analyzed_at is not None]
    message = None
    if job is not None:
        message = dict(job.stats_json or {}).get("stopped") or (
            job.error_message if job.status == "failed" else None
        )
    return Breakthroughs(
        status=status,
        job=job_out(job, hide_project=True) if job is not None else None,
        analyzed_at=max(analyzed) if analyzed else None,
        paper=disruption_detail(paper),
        scholars=groups,
        message=message,
    )


@router.get("/papers/{paper_id}/breakthroughs", response_model=Breakthroughs)
def get_breakthroughs(paper_id: int, owner_id: OwnerId) -> Breakthroughs:
    """``200 Breakthroughs``."""
    with session_scope() as session:
        return breakthroughs_out(session, paper_id, owner_id)


@router.post("/papers/{paper_id}/breakthroughs", response_model=JobAccepted, status_code=202)
def analyze_breakthroughs(paper_id: int, owner_id: OwnerId, force: bool = False) -> JobAccepted:
    """``202 { job }``; 429 ``quota_exceeded``; 409 ``conflict`` when the OpenAlex budget is spent."""
    from radar.net import ledger

    settings = get_settings()
    with session_scope() as session:
        _paper_or_404(session, paper_id)
        active = _jobs_for_paper(session, "breakthroughs", paper_id, active_only=True)
        if active:
            return JobAccepted(job=job_out(active[0], hide_project=True))
        if _jobs_today(session, "breakthroughs", owner_id) >= settings.breakthroughs_per_day:
            raise ApiError(
                429,
                "quota_exceeded",
                f"You can run {settings.breakthroughs_per_day} breakthrough analyses per day; try again tomorrow",
            )
    if ledger.budget_status("openalex").stopped:
        raise ApiError(409, "conflict", "The OpenAlex daily budget is used up; try again tomorrow")
    params: dict[str, object] = {"paper_id": paper_id}
    if force:
        params["force"] = True
    job_id = submit_job("breakthroughs", params=params, owner_id=owner_id)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise ApiError(404, "not_found", "Job not found")
        return JobAccepted(job=job_out(job))


@router.get("/scholars/{scholar_id}", response_model=ScholarDetail)
def get_scholar(scholar_id: int, owner_id: OwnerId) -> ScholarDetail:
    """``200 ScholarDetail``."""
    with session_scope() as session:
        scholar = session.get(Scholar, scholar_id)
        if scholar is None:
            raise ApiError(404, "not_found", "Scholar not found")
        return ScholarDetail(scholar=scholar_summary(scholar), works=_works_of(session, scholar_id, owner_id))
