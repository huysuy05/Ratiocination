"""Per-user dashboard (API.md "Dashboard", SPEC §14). Owner: dashboard.

Every view is scoped to the signed-in user's projects (one with ``project_id``, else all), a period
of ``days`` ending today in ``tz`` (else the user's preference, else ``RADAR_TIMEZONE``) and the
previous period of equal length. The GET views are computed locally from the database; only
``POST /summary`` reaches out (to Anthropic, in a job).
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, Query

from radar.api.serializers import job_out
from radar.config import get_settings
from radar.dashboard import summary
from radar.dashboard.activity import activity
from radar.dashboard.common import DAYS, make_scope
from radar.dashboard.findings import findings
from radar.dashboard.topics import topics
from radar.dashboard.work import work
from radar.db import session_scope
from radar.errors import ApiError
from radar.jobs.runner import submit_job
from radar.models import Job
from radar.api.deps import OwnerId
from radar.schemas import (
    DashboardActivity,
    DashboardFindings,
    DashboardTopics,
    DashboardWork,
    JobAccepted,
    SummaryRequest,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Query strings are not coerced into Literal[7, 30, ...]; make_scope checks the value (422).
Days = Query(30, json_schema_extra={"enum": list(DAYS)})
Zone = Query(None, max_length=64)


@router.get("/activity", response_model=DashboardActivity)
def get_activity(
    owner_id: OwnerId, days: int = Days, project_id: int | None = None, tz: str | None = Zone
) -> DashboardActivity:
    """``200 DashboardActivity``; 404 for a project that is not the user's; 422 for an unknown zone."""
    with session_scope() as session:
        return activity(session, make_scope(session, owner_id, days, project_id, tz))


@router.get("/topics", response_model=DashboardTopics)
def get_topics(
    owner_id: OwnerId, days: int = Days, project_id: int | None = None, tz: str | None = Zone
) -> DashboardTopics:
    """``200 DashboardTopics``."""
    with session_scope() as session:
        return topics(session, make_scope(session, owner_id, days, project_id, tz))


@router.get("/findings", response_model=DashboardFindings)
def get_findings(
    owner_id: OwnerId, days: int = Days, project_id: int | None = None, tz: str | None = Zone
) -> DashboardFindings:
    """``200 DashboardFindings``: quoted findings by topic, the latest AI summary and its status."""
    with session_scope() as session:
        scope = make_scope(session, owner_id, days, project_id, tz)
        found = findings(session, scope)
        current = [pid for pid, _, _ in summary.inputs(session, scope, found.liked)]
        latest = summary.latest(session, scope, current)
        return DashboardFindings(
            period=scope.period(),
            n_papers=found.n_papers,
            n_with_findings=found.n_with_findings,
            groups=found.groups,
            summary=latest,
            ai=summary.ai_status(session, scope, latest.created_at if latest else None),
        )


@router.get("/work", response_model=DashboardWork)
def get_work(
    owner_id: OwnerId, days: int = Days, project_id: int | None = None, tz: str | None = Zone
) -> DashboardWork:
    """``200 DashboardWork``."""
    with session_scope() as session:
        return work(session, make_scope(session, owner_id, days, project_id, tz))


# Quota check + job insert run one request at a time (one worker process). The write transaction
# alone serializes them on SQLite (BEGIN IMMEDIATE); the lock keeps that true on PostgreSQL too.
_summary_quota_lock = threading.Lock()


@router.post("/summary", response_model=JobAccepted, status_code=202)
def request_summary(body: SummaryRequest, owner_id: OwnerId) -> JobAccepted:
    """``202 { job }`` (a running identical request is returned); 409 ``not_configured`` /
    ``not_enough_papers``; 429 ``quota_exceeded`` (``RADAR_SUMMARIES_PER_DAY`` for the user,
    ``RADAR_SUMMARIES_GLOBAL_PER_DAY`` for the server)."""
    settings = get_settings()
    if not settings.ai_summary_configured:
        raise ApiError(409, "not_configured", summary.NOT_CONFIGURED)
    with session_scope() as session:
        scope = make_scope(session, owner_id, body.days, body.project_id, body.tz)
        active = summary.active_job(session, scope)
        if active is not None:
            return JobAccepted(job=job_out(active))
        summary.check_quota(session, owner_id)  # early answer; checked again below, atomically
        n = len(summary.inputs(session, scope))
        if n < summary.MIN_PAPERS:
            raise summary.not_enough(n)
    params: dict[str, object] = {"user_id": owner_id, "days": body.days}
    if body.tz:
        params["tz"] = body.tz
    # Count and insert in ONE short write transaction so parallel requests cannot both pass a cap; the
    # job is dispatched after the commit (submit_job with session=, the strict-mode rule).
    with _summary_quota_lock, session_scope(write=True) as session:
        active = summary.active_job(session, scope)
        if active is not None:
            job_id = active.id
        else:
            summary.check_quota(session, owner_id)
            job_id = submit_job(
                "summarize_findings",
                project_id=body.project_id,
                params=params,
                owner_id=owner_id,
                session=session,
            )
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise ApiError(404, "not_found", "Job not found")
        return JobAccepted(job=job_out(job))
