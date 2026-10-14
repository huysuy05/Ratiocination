"""Seeds (docs/API.md "Seeds"). Owner: enrich.

Seed resolution may call the arXiv API / OpenAlex, so ``POST seeds`` resolves first (no
transaction), then writes the seed and queues ``enrich`` + ``embed`` in one short write transaction;
the jobs dispatch after commit (SPEC §2.1, §15). Every route acts on the signed-in user's project.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.api.deps import OwnerId, ReadSession, WriteSession, get_project_or_404
from radar.api.serializers import job_out, paper_summaries
from radar.db import session_scope
from radar.errors import ApiError
from radar.ingestion.seeds import resolve_seed
from radar.jobs.runner import submit_job
from radar.models import Job, Paper, ProjectSeed
from radar.net.http import BudgetExceeded, EgressBlocked, SourceUnavailable
from radar.schemas import Seed, SeedCreate, SeedCreated

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["seeds"])


# ------------------------------------------------------------------------------------------------ seeds


def _seeds_out(session: Session, rows: list[ProjectSeed], owner_id: int) -> list[Seed]:
    summaries = paper_summaries(session, [row.paper_id for row in rows], user_id=owner_id)
    return [
        Seed(
            paper=summaries[row.paper_id],
            weight=row.weight,
            added_via=row.added_via,
            created_at=row.created_at,
        )
        for row in rows
        if row.paper_id in summaries
    ]


@router.get("/projects/{project_id}/seeds", response_model=list[Seed])
def list_seeds(project_id: int, session: ReadSession, owner_id: OwnerId) -> list[Seed]:
    """``200 Seed[]`` (oldest first)."""
    get_project_or_404(session, project_id, owner_id)
    rows = list(
        session.scalars(
            select(ProjectSeed).where(ProjectSeed.project_id == project_id).order_by(ProjectSeed.created_at)
        )
    )
    return _seeds_out(session, rows, owner_id)


@router.post("/projects/{project_id}/seeds", response_model=SeedCreated, status_code=201)
def add_seed(project_id: int, body: SeedCreate, owner_id: OwnerId) -> SeedCreated:
    """``201 SeedCreated``; 422 ``unresolvable_identifier``; 503 ``source_unavailable``."""
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
        if body.paper_id is not None and session.get(Paper, body.paper_id) is None:
            raise ApiError(404, "not_found", "Paper not found")

    if body.paper_id is not None:
        paper_id, added_via = body.paper_id, "search"
    else:
        try:
            paper_id, added_via = resolve_seed(body.identifier or "", project_id=project_id)
        except BudgetExceeded:
            raise ApiError(
                503, "budget_exceeded", "The OpenAlex daily budget is used up; try again tomorrow"
            ) from None
        except (SourceUnavailable, EgressBlocked):
            raise ApiError(
                503,
                "source_unavailable",
                "A paper source is unavailable right now; try again in a few minutes",
            ) from None

    enrich_job_id: int | None = None
    with session_scope(write=True) as session:
        seed = session.scalar(
            select(ProjectSeed).where(ProjectSeed.project_id == project_id, ProjectSeed.paper_id == paper_id)
        )
        created = seed is None
        if created:
            session.add(
                ProjectSeed(project_id=project_id, paper_id=paper_id, weight=body.weight, added_via=added_via)
            )
            enrich_job_id = submit_job(
                "enrich", params={"paper_ids": [paper_id]}, session=session, owner_id=owner_id
            )
            submit_job("embed", params={"paper_ids": [paper_id]}, session=session, owner_id=owner_id)

    with session_scope() as session:
        row = session.scalar(
            select(ProjectSeed).where(ProjectSeed.project_id == project_id, ProjectSeed.paper_id == paper_id)
        )
        if row is None:  # pragma: no cover - removed concurrently
            raise ApiError(404, "not_found", "Seed not found")
        job = session.get(Job, enrich_job_id) if enrich_job_id is not None else None
        return SeedCreated(
            seed=_seeds_out(session, [row], owner_id)[0],
            created=created,
            enrich_job=job_out(job) if job is not None else None,
        )


@router.delete("/projects/{project_id}/seeds/{paper_id}", status_code=204, response_class=Response)
def remove_seed(project_id: int, paper_id: int, session: WriteSession, owner_id: OwnerId) -> Response:
    """``204``; 404 when the paper is not a seed of the project."""
    get_project_or_404(session, project_id, owner_id)
    result = session.execute(
        delete(ProjectSeed).where(ProjectSeed.project_id == project_id, ProjectSeed.paper_id == paper_id)
    )
    if result.rowcount == 0:
        raise ApiError(404, "not_found", "This paper is not a seed of the project")
    return Response(status_code=204)
