"""Feed, rebuild, feedback and undo (docs/API.md "Feed and feedback"). Owner: rank.

* ``GET feed``: the latest slate with current states, the active build job and a status
  (``needs_setup`` → ``building`` → ``ready`` → ``empty`` → ``not_built``).
* ``POST rebuild``: one active ``build_deck`` job per project (the runner dedupes on the project); the
  same mode returns the active job, another mode is a 409 with ``details.job``.
* ``POST feedback``: records the action (state, training label) and, for relevant/save, may queue an
  automatic expansion in the same transaction (dispatched after commit).
* Every route acts on the signed-in user's project; others are a 404.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from radar.api.deps import OwnerId, get_project_or_404
from radar.api.serializers import job_out, project_settings, slate_out
from radar.db import session_scope
from radar.errors import ApiError
from radar.jobs.runner import active_job, submit_job
from radar.jobs.triggers import maybe_queue_expansion
from radar.models import Job, RecommendationSlate
from radar.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    FeedbackTriggered,
    FeedResponse,
    JobAccepted,
    RebuildRequest,
    UndoRequest,
    UndoResponse,
)
from radar.store.states import get_states, record_interaction, undo_interaction

router = APIRouter(prefix="/api", tags=["feed"])

SETUP_MESSAGE = "Add arXiv categories to this project to start your deck."


@router.get("/projects/{project_id}/feed", response_model=FeedResponse)
def get_feed(project_id: int, owner_id: OwnerId) -> FeedResponse:
    """``200 FeedResponse``."""
    with session_scope() as session:
        project = get_project_or_404(session, project_id, owner_id)
        settings = project_settings(project)
        latest_id = session.scalar(
            select(RecommendationSlate.id)
            .where(RecommendationSlate.project_id == project_id)
            .order_by(RecommendationSlate.generated_at.desc(), RecommendationSlate.id.desc())
            .limit(1)
        )
        slate = slate_out(session, latest_id, project_id) if latest_id is not None else None
        running = active_job(session, "build_deck", project_id)
        last = session.scalars(
            select(Job)
            .where(Job.kind == "build_deck", Job.project_id == project_id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        ).first()
        job = job_out(running) if running is not None else None
        last_job = job_out(last) if last is not None else None

    if not settings.arxiv_categories:
        status, message = "needs_setup", SETUP_MESSAGE
    elif job is not None:
        status, message = "building", job.message
    elif slate is not None and slate.items:
        status, message = "ready", None
    elif slate is not None:
        status, message = "empty", slate.message
    else:
        status, message = "not_built", None
    return FeedResponse(status=status, slate=slate, job=job, last_job=last_job, message=message)


@router.post("/projects/{project_id}/feed/rebuild", response_model=JobAccepted, status_code=202)
def rebuild_feed(project_id: int, owner_id: OwnerId, body: RebuildRequest | None = None) -> JobAccepted:
    """``202 { job }``; same mode -> existing job; different mode -> 409 ``conflict`` (``details.job``)."""
    request = body or RebuildRequest()
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
    params = {"mode": request.mode, "prepare": request.prepare}
    job_id = submit_job("build_deck", project_id=project_id, params=params, owner_id=owner_id)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise ApiError(404, "not_found", "Job not found")
        out = job_out(job)
        if dict(job.params_json or {}).get("mode", "daily") != request.mode:
            raise ApiError(
                409, "conflict", "A deck with a different mode is already being built", {"job": out}
            )
    return JobAccepted(job=out)


@router.post("/projects/{project_id}/feedback", response_model=FeedbackResponse)
def post_feedback(project_id: int, body: FeedbackRequest, owner_id: OwnerId) -> FeedbackResponse:
    """``200 FeedbackResponse``; 404 unknown paper; 422 unknown action."""
    with session_scope(write=True) as session:
        get_project_or_404(session, project_id, owner_id)
        interaction = record_interaction(
            session, project_id, body.paper_id, body.action, slate_id=body.slate_id, context=body.context
        )
        row = get_states(session, project_id, [body.paper_id]).get(body.paper_id)
        expansion_job_id = maybe_queue_expansion(session, project_id, body.paper_id, body.action)
        response = FeedbackResponse(
            interaction_id=interaction.id,
            paper_id=body.paper_id,
            state=row.state if row is not None else None,
            triggered=FeedbackTriggered(expansion_job_id=expansion_job_id),
        )
    return response


@router.post("/projects/{project_id}/feedback/undo", response_model=UndoResponse)
def undo_feedback(project_id: int, owner_id: OwnerId, body: UndoRequest | None = None) -> UndoResponse:
    """``200 UndoResponse``; 404 when nothing to undo."""
    with session_scope(write=True) as session:
        get_project_or_404(session, project_id, owner_id)
        target = undo_interaction(session, project_id, body.interaction_id if body else None)
        row = get_states(session, project_id, [target.paper_id]).get(target.paper_id)
        return UndoResponse(
            undone_interaction_id=target.id,
            paper_id=target.paper_id,
            state=row.state if row is not None else None,
            last_interaction_id=row.last_interaction_id if row is not None else None,
        )
