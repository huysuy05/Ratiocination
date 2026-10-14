"""System settings and the model download (docs/API.md "Jobs, settings, health"). Owner: library.

Everyone sees the public part (offline flag, privacy disclosure, embedding model, AI-summary
availability); admins also get ``admin`` (scheduler, connector status, user count, last daily run)
and may change the scheduler and start the model download (SPEC §5.9).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import models
from radar.api.deps import AdminUser, UserDep
from radar.api.serializers import job_out
from radar.config import get_settings
from radar.db import session_scope
from radar.errors import ApiError
from radar.jobs.runner import ACTIVE_STATUSES, submit_job
from radar.schemas import (
    AdminSettings,
    AiSummarySettings,
    BreakerStatus,
    BudgetInfo,
    ConnectorsStatus,
    EmbeddingSettings,
    JobAccepted,
    OAuthStatus,
    OpenAlexStatus,
    SchedulerSettings,
    SemanticScholarStatus,
    SettingsUpdate,
    SmtpStatus,
    SystemSettings,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])

PRIVACY_DISCLOSURE = (
    "Your projects, research questions, keywords, uploaded drafts, ratings and reading history are stored on "
    "this server and visible only to you; they are never sent to any other service. To find and "
    "enrich public papers, this server asks arXiv, OpenAlex and Semantic Scholar about specific "
    "papers (the seeds you add, papers you expand or analyze), which tells those services which "
    "public papers were looked up. When an AI summary is requested, the titles and abstracts of the "
    "papers you liked (public text) are sent to Anthropic; your drafts and questions never are."
)


def _embedding(session: Session) -> EmbeddingSettings:
    from radar.embeddings.registry import available_embedders, fallback_active, get_embedder, model_cached

    download = session.scalars(
        select(models.Job)
        .where(models.Job.kind == "download_model")
        .order_by(models.Job.created_at.desc(), models.Job.id.desc())
        .limit(1)
    ).first()
    return EmbeddingSettings(
        default_model=get_settings().embedder,
        active_model=get_embedder().model_id,
        fallback_active=fallback_active(),
        model_cached=model_cached(),
        download_job=job_out(download) if download is not None else None,
        available=available_embedders(),
    )


def _admin(session: Session) -> AdminSettings:
    from radar.jobs import scheduler
    from radar.net import ledger
    from radar.net.http import ARXIV_API_HOST, SEMANTIC_SCHOLAR, get_http

    settings = get_settings()
    budget = ledger.budget_status("openalex")
    http = get_http()
    last_daily = session.scalars(
        select(models.Job)
        .where(models.Job.kind == "daily")
        .order_by(models.Job.created_at.desc(), models.Job.id.desc())
        .limit(1)
    ).first()
    users = session.scalar(select(func.count()).where(models.User.email.is_not(None))) or 0
    return AdminSettings(
        scheduler=SchedulerSettings(
            enabled=scheduler.is_enabled(),
            next_run_at=scheduler.next_run_at(),
            cron=settings.daily_cron,
            timezone=settings.timezone,
        ),
        connectors=ConnectorsStatus(
            openalex=OpenAlexStatus(
                has_key=bool(settings.openalex_api_key),
                budget=BudgetInfo(
                    provider=budget.provider,
                    daily_budget_usd=budget.daily_budget_usd,
                    spent_today_usd=budget.spent_today_usd,
                    remaining_today_usd=budget.remaining_today_usd,
                    stop_fraction=budget.stop_fraction,
                    stopped=budget.stopped,
                    source=budget.source,
                ),
            ),
            semantic_scholar=SemanticScholarStatus(
                has_key=bool(settings.s2_api_key), breaker_until=http.breaker_until(SEMANTIC_SCHOLAR)
            ),
            arxiv=BreakerStatus(breaker_until=http.breaker_until(ARXIV_API_HOST)),
            smtp=SmtpStatus(configured=settings.smtp_configured),
            oauth=OAuthStatus(google=settings.google_enabled, github=settings.github_enabled),
        ),
        users=int(users) or (1 if settings.auth == "local" else 0),
        last_daily=job_out(last_daily) if last_daily is not None else None,
    )


def _settings_out(is_admin: bool) -> SystemSettings:
    settings = get_settings()
    with session_scope() as session:
        return SystemSettings(
            offline=settings.offline,
            privacy_disclosure=PRIVACY_DISCLOSURE,
            embedding=_embedding(session),
            ai_summary=AiSummarySettings(
                configured=settings.ai_summary_configured,
                model=settings.summary_model if settings.ai_summary_configured else None,
            ),
            admin=_admin(session) if is_admin else None,
            version=settings.version,
        )


@router.get("/settings", response_model=SystemSettings)
def get_system_settings(user: UserDep) -> SystemSettings:
    """``200 SystemSettings`` (``admin`` only for admins)."""
    return _settings_out(user.is_admin)


@router.patch("/settings", response_model=SystemSettings)
def update_system_settings(body: SettingsUpdate, user: AdminUser) -> SystemSettings:
    """``200 SystemSettings`` (admin)."""
    if body.scheduler_enabled is not None:
        from radar.jobs import scheduler

        try:
            scheduler.set_enabled(body.scheduler_enabled)
        except ValueError as exc:
            raise ApiError(409, "conflict", "RADAR_DAILY_CRON is not a valid schedule") from exc
        log.info("scheduler %s by user %s", "enabled" if body.scheduler_enabled else "disabled", user.id)
    return _settings_out(True)


@router.post("/settings/embedding/download", response_model=JobAccepted, status_code=202)
def download_embedding_model(user: AdminUser) -> JobAccepted:
    """``202 { job }`` (kind ``download_model``; admin); 409 ``conflict`` when offline."""
    if get_settings().offline:
        raise ApiError(
            409, "conflict", "The server is offline (RADAR_OFFLINE=1); the model cannot be downloaded"
        )
    with session_scope() as session:
        active = session.scalars(
            select(models.Job).where(
                models.Job.kind == "download_model", models.Job.status.in_(ACTIVE_STATUSES)
            )
        ).first()
        if active is not None:
            return JobAccepted(job=job_out(active))
    job_id = submit_job("download_model", owner_id=user.id)
    with session_scope() as session:
        job = session.get(models.Job, job_id)
        if job is None:  # pragma: no cover - just created
            raise ApiError(404, "not_found", "Job not found")
        return JobAccepted(job=job_out(job))
