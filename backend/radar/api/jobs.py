"""Jobs list/get/run (docs/API.md "Jobs, settings, health"). Owner: jobs.

Visibility (SPEC §5.9): a user sees jobs they own or that belong to their projects; admins see all.
``expand`` and ``breakthroughs`` jobs work on public papers and may be shared between users (an
identical active job is reused), so any signed-in user may read them, with another user's
``project_id`` blanked.

* ``GET /api/jobs?limit=20&kind=&project_id=`` - newest first (``created_at`` desc, then id desc).
  Only rows whose kind is an API ``JobKind`` are listed (kinds registered by tests are skipped).
* ``GET /api/jobs/{id}`` - one job; 404 ``not_found`` when it does not exist, is not an API kind, or
  is not visible to the user.
* ``POST /api/jobs/run`` - admins may run any kind; other users only ``backfill`` and ``build_deck``
  for their own projects (403 ``forbidden`` otherwise). ``202 { job }``: queues the job with
  ``runner.submit_job`` (deduplicated
  against an active job with the same key) and returns the row as stored right after submission (with
  ``RADAR_JOBS_INLINE=1`` the job has already finished). Validation (422 ``validation_error``):
  parameters must fit the job function's signature (paths ``params.<name>``; ``project_id`` is never a
  parameter) and be plain JSON (no NaN/Infinity, path ``params``), ``backfill``/``build_deck`` need
  ``project_id``, ``build_deck`` takes ``mode`` in ``SlateMode``. An unknown project is 404. A
  ``build_deck`` request that dedupes to an active build of a different mode is 409 ``conflict`` with
  ``details.job`` (same rule as the feed rebuild endpoint).

Jobs are serialized with ``radar.api.serializers.job_out`` (a private mapper with the same output is
used if that raises ``NotImplementedError``).
"""

from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar import models
from radar.api.deps import ReadSession, UserDep, get_project_or_404
from radar.db import session_scope
from radar.errors import ApiError, validation_error
from radar.jobs import registry
from radar.jobs.runner import submit_job
from radar.schemas import Job, JobAccepted, JobKind, RunJobRequest, SlateMode

router = APIRouter(prefix="/api", tags=["jobs"])

API_JOB_KINDS: tuple[str, ...] = tuple(get_args(JobKind))
SLATE_MODES: tuple[str, ...] = tuple(get_args(SlateMode))
SHARED_KINDS: frozenset[str] = frozenset({"expand", "breakthroughs"})
USER_RUNNABLE_KINDS: frozenset[str] = frozenset({"backfill", "build_deck"})


def _user_project_ids(session: Session, user_id: int) -> set[int]:
    return set(session.scalars(select(models.Project.id).where(models.Project.owner_id == user_id)))


def _fallback_job_out(job: models.Job) -> Job:
    return Job(
        id=job.id,
        kind=job.kind,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
        project_id=job.project_id,
        params=dict(job.params_json or {}),
        stats=dict(job.stats_json or {}),
        progress=job.progress,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def job_to_api(job: models.Job, *, hide_project: bool = False) -> Job:
    """API ``Job`` for a ``jobs`` row (the store serializer when implemented)."""
    from radar.api import serializers

    try:
        return serializers.job_out(job, hide_project=hide_project)
    except NotImplementedError:
        out = _fallback_job_out(job)
        return out.model_copy(update={"project_id": None}) if hide_project else out


def visible_job_out(session: Session, job: models.Job, user_id: int, is_admin: bool) -> Job | None:
    """The job as ``user_id`` may see it, or ``None`` when it is not visible to them."""
    if is_admin or job.owner_id == user_id:
        return job_to_api(job)
    own_projects = _user_project_ids(session, user_id)
    if job.project_id is not None and job.project_id in own_projects:
        return job_to_api(job)
    if job.kind in SHARED_KINDS:
        return job_to_api(job, hide_project=True)
    return None


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Job not found")


@router.get("/jobs", response_model=list[Job])
def list_jobs(
    session: ReadSession,
    user: UserDep,
    limit: int = Query(20, ge=1, le=200),
    kind: JobKind | None = None,
    project_id: int | None = None,
) -> list[Job]:
    """``200 Job[]`` (newest first): the user's own jobs and their projects' jobs (admins: all)."""
    stmt = select(models.Job).where(models.Job.kind.in_(API_JOB_KINDS))
    if not user.is_admin:
        own_projects = sorted(_user_project_ids(session, user.id))
        stmt = stmt.where(
            or_(models.Job.owner_id == user.id, models.Job.project_id.in_(own_projects or [-1]))
        )
    if kind is not None:
        stmt = stmt.where(models.Job.kind == kind)
    if project_id is not None:
        stmt = stmt.where(models.Job.project_id == project_id)
    rows = session.scalars(
        stmt.order_by(models.Job.created_at.desc(), models.Job.id.desc()).limit(limit)
    ).all()
    return [job_to_api(job) for job in rows]


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: int, session: ReadSession, user: UserDep) -> Job:
    """``200 Job`` / 404."""
    job = session.get(models.Job, job_id)
    if job is None or job.kind not in API_JOB_KINDS:
        raise _not_found()
    out = visible_job_out(session, job, user.id, user.is_admin)
    if out is None:
        raise _not_found()
    return out


def _validate_run(body: RunJobRequest) -> dict[str, Any]:
    params = dict(body.params or {})
    fields: list[dict[str, str]] = []
    if body.kind in registry.PROJECT_REQUIRED and body.project_id is None:
        fields.append({"path": "project_id", "message": "This job kind needs a project"})
    if body.kind == "build_deck" and "mode" in params and params["mode"] not in SLATE_MODES:
        fields.append({"path": "params.mode", "message": f"Must be one of: {', '.join(SLATE_MODES)}"})
    try:
        registry.validate_params(body.kind, params)
    except registry.JobParamsError as exc:
        fields.extend(f for f in exc.fields if f not in fields)
    if fields:
        raise validation_error(fields)
    return params


@router.post("/jobs/run", response_model=JobAccepted, status_code=202)
def run_job(body: RunJobRequest, user: UserDep) -> JobAccepted:
    """``202 { job }``; 403 ``forbidden`` for kinds a non-admin may not run."""
    if not user.is_admin and body.kind not in USER_RUNNABLE_KINDS:
        raise ApiError(403, "forbidden", "Only an administrator can run this job")
    params = _validate_run(body)
    if body.project_id is not None:
        with session_scope() as session:
            if user.is_admin:
                if session.get(models.Project, body.project_id) is None:
                    raise ApiError(404, "not_found", "Project not found")
            else:
                get_project_or_404(session, body.project_id, user.id)
    # No session is open here: submit_job writes in its own short write session (strict-mode rule).
    try:
        job_id = submit_job(body.kind, project_id=body.project_id, params=params, owner_id=user.id)
    except ValueError as exc:  # e.g. NaN/Infinity, which Python's JSON parser accepts
        raise validation_error(
            [{"path": "params", "message": "Parameters must be plain JSON values"}]
        ) from exc
    with session_scope() as session:
        job = session.get(models.Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise _not_found()
        out = job_to_api(job)
        if body.kind == "build_deck":
            requested = params.get("mode", "daily")
            existing = dict(job.params_json or {}).get("mode", "daily")
            if existing != requested:
                raise ApiError(
                    409,
                    "conflict",
                    "A deck build with a different mode is already queued or running",
                    {"job": out},
                )
    return JobAccepted(job=out)
