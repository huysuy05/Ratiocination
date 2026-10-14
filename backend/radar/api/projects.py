"""Projects CRUD (docs/API.md "Projects"). Owner: core.

* Every route acts on the signed-in user's projects; another user's project is a 404.
* ``keywords`` (≤ 12 phrases, validated and de-duplicated by ``schemas.Keywords``) replace the stored
  list as a whole; like the question, a change applies from the next deck (no rebuild is queued).
* ``settings`` in requests is ``Partial<ProjectSettings>``: keys are merged over the stored (or
  default) settings and the merged result is validated (422 paths ``settings.<key>``).
* Creating an active project with categories, activating a project, or changing its categories or
  window queues ``build_deck {mode: "daily", prepare: true}`` in the same transaction (dispatched
  after commit), so the first deck is being built while the user adds seeds and drafts.
* ``DELETE`` calls ``radar.documents.service.delete_project_private_data`` before deleting the row
  (FK cascades remove seeds, documents, interactions, states and slates), commits, then checkpoints
  the SQLite WAL.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Response
from pydantic import ValidationError
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from radar import models
from radar.api.deps import OwnerId, ReadSession, WriteSession, get_project_or_404
from radar.api.serializers import project_keywords
from radar.config import get_settings
from radar.db import checkpoint_wal, session_scope
from radar.errors import ApiError, validation_error, validation_fields
from radar.jobs.runner import submit_job
from radar.schemas import (
    Project,
    ProjectCreate,
    ProjectSettings,
    ProjectSettingsPatch,
    ProjectStats,
    ProjectUpdate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["projects"])

STAT_STATES = ("relevant", "saved", "not_relevant")


# ------------------------------------------------------------------------------------------------
# Settings helpers
# ------------------------------------------------------------------------------------------------


def stored_settings(project: models.Project) -> ProjectSettings:
    """The project's settings, validated; invalid stored keys fall back to their defaults."""
    stored = dict(project.settings_json or {})
    try:
        return ProjectSettings.from_stored(stored)
    except ValidationError:
        log.warning("project %s has invalid stored settings; using defaults for invalid keys", project.id)
    merged = ProjectSettings().model_dump()
    for key, value in stored.items():
        if key not in merged:
            continue
        candidate = {**merged, key: value}
        try:
            ProjectSettings.model_validate(candidate)
        except ValidationError:
            continue
        merged = candidate
    return ProjectSettings.model_validate(merged)


def merge_settings(current: ProjectSettings, patch: ProjectSettingsPatch | None) -> ProjectSettings:
    """Merge ``patch`` over ``current`` key by key and validate the result (422 on failure)."""
    values = patch.model_dump(exclude_unset=True) if patch is not None else {}
    merged = {**current.model_dump(), **values}
    try:
        return ProjectSettings.model_validate(merged)
    except ValidationError as exc:
        raise validation_error(
            validation_fields(exc.errors(include_input=False), prefix="settings")
        ) from None


def queue_prepare_build(session: Session, project: models.Project) -> int | None:
    """Queue the first/refreshed deck of an active project with categories (dispatched after commit)."""
    if not project.active or not stored_settings(project).arxiv_categories:
        return None
    return submit_job(
        "build_deck",
        project_id=project.id,
        params={"mode": "daily", "prepare": True},
        session=session,
        owner_id=project.owner_id,
    )


# ------------------------------------------------------------------------------------------------
# Stats and serialization
# ------------------------------------------------------------------------------------------------


def project_stats(session: Session, projects: Sequence[models.Project]) -> dict[int, ProjectStats]:
    """ProjectStats for several projects with a fixed number of grouped queries."""
    ids = [p.id for p in projects]
    if not ids:
        return {}
    counts: dict[int, dict[str, int]] = {pid: defaultdict(int) for pid in ids}

    pps = models.ProjectPaperState
    for pid, n in session.execute(
        select(models.ProjectSeed.project_id, func.count())
        .where(models.ProjectSeed.project_id.in_(ids))
        .group_by(models.ProjectSeed.project_id)
    ):
        counts[pid]["seeds"] = int(n)
    for pid, n in session.execute(
        select(models.Document.project_id, func.count())
        .where(models.Document.project_id.in_(ids))
        .group_by(models.Document.project_id)
    ):
        counts[pid]["documents"] = int(n)
    for pid, state, n in session.execute(
        select(pps.project_id, pps.state, func.count())
        .where(pps.project_id.in_(ids), pps.state.is_not(None))
        .group_by(pps.project_id, pps.state)
    ):
        if state in STAT_STATES:
            counts[pid][state] += int(n)

    # latest slate per project and its items without a state
    slates = models.RecommendationSlate
    ranked = (
        select(
            slates.id.label("slate_id"),
            slates.project_id.label("project_id"),
            slates.generated_at.label("generated_at"),
            func.row_number()
            .over(partition_by=slates.project_id, order_by=(slates.generated_at.desc(), slates.id.desc()))
            .label("rn"),
        )
        .where(slates.project_id.in_(ids))
        .subquery()
    )
    latest: dict[int, tuple[int, Any]] = {}
    for slate_id, pid, generated_at in session.execute(
        select(ranked.c.slate_id, ranked.c.project_id, ranked.c.generated_at).where(ranked.c.rn == 1)
    ):
        latest[pid] = (slate_id, generated_at)
    remaining: dict[int, int] = {}
    if latest:
        items = models.RecommendationItem
        has_state = exists().where(
            and_(pps.project_id == slates.project_id, pps.paper_id == items.paper_id, pps.state.is_not(None))
        )
        for slate_id, n in session.execute(
            select(items.slate_id, func.count())
            .join(slates, slates.id == items.slate_id)
            .where(items.slate_id.in_([v[0] for v in latest.values()]), ~has_state)
            .group_by(items.slate_id)
        ):
            remaining[slate_id] = int(n)

    out: dict[int, ProjectStats] = {}
    for pid in ids:
        c = counts[pid]
        slate = latest.get(pid)
        out[pid] = ProjectStats(
            seeds=c["seeds"],
            documents=c["documents"],
            relevant=c["relevant"],
            saved=c["saved"],
            not_relevant=c["not_relevant"],
            remaining_in_deck=remaining.get(slate[0], 0) if slate else 0,
            last_deck_at=slate[1] if slate else None,
        )
    return out


def serialize_projects(session: Session, projects: Sequence[models.Project]) -> list[Project]:
    """API ``Project`` objects (with stats) in the given order."""
    stats = project_stats(session, projects)
    return [
        Project(
            id=p.id,
            name=p.name,
            research_question=p.research_question,
            description=p.description,
            keywords=project_keywords(p),
            settings=stored_settings(p),
            active=p.active,
            created_at=p.created_at,
            updated_at=p.updated_at,
            stats=stats[p.id],
        )
        for p in projects
    ]


# ------------------------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------------------------


@router.get("/projects", response_model=list[Project])
def list_projects(session: ReadSession, owner_id: OwnerId) -> list[Project]:
    """``GET /api/projects`` -> ``200 Project[]`` (newest first)."""
    projects = session.scalars(
        select(models.Project)
        .where(models.Project.owner_id == owner_id)
        .order_by(models.Project.created_at.desc(), models.Project.id.desc())
    ).all()
    return serialize_projects(session, projects)


@router.post("/projects", response_model=Project, status_code=201)
def create_project(body: ProjectCreate, session: WriteSession, owner_id: OwnerId) -> Project:
    """``POST /api/projects`` -> ``201 Project``; 429 ``quota_exceeded`` beyond RADAR_MAX_PROJECTS."""
    settings = merge_settings(ProjectSettings(), body.settings)
    limit = get_settings().max_projects
    owned = session.scalar(select(func.count()).where(models.Project.owner_id == owner_id)) or 0
    if owned >= limit:
        raise ApiError(429, "quota_exceeded", f"You can have at most {limit} projects")
    project = models.Project(
        owner_id=owner_id,
        name=body.name,
        research_question=body.research_question,
        description=body.description,
        keywords_json=list(body.keywords),
        settings_json=settings.model_dump(mode="json"),
        active=body.active,
    )
    session.add(project)
    session.flush()
    queue_prepare_build(session, project)
    log.info("project %s created", project.id)
    return serialize_projects(session, [project])[0]


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: int, session: ReadSession, owner_id: OwnerId) -> Project:
    """``GET /api/projects/{id}`` -> ``200 Project`` / 404."""
    project = get_project_or_404(session, project_id, owner_id)
    return serialize_projects(session, [project])[0]


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: int, body: ProjectUpdate, session: WriteSession, owner_id: OwnerId) -> Project:
    """``PATCH /api/projects/{id}``: settings merged key by key, then the merged result is validated."""
    project = get_project_or_404(session, project_id, owner_id)
    fields_set = body.model_fields_set
    current = stored_settings(project)
    was_active = project.active
    rebuild = False
    if "settings" in fields_set and body.settings is not None:
        merged = merge_settings(current, body.settings)
        project.settings_json = merged.model_dump(mode="json")
        rebuild = (merged.arxiv_categories, merged.time_window_days) != (
            current.arxiv_categories,
            current.time_window_days,
        )
    for name in ("name", "research_question", "description", "active"):
        if name in fields_set:
            setattr(project, name, getattr(body, name))
    if "keywords" in fields_set and body.keywords is not None:
        project.keywords_json = list(body.keywords)
    if project.active and (rebuild or not was_active):
        queue_prepare_build(session, project)
    session.flush()
    session.refresh(project)
    return serialize_projects(session, [project])[0]


@router.delete("/projects/{project_id}", status_code=204, response_class=Response)
def delete_project(project_id: int, owner_id: OwnerId) -> Response:
    """``DELETE /api/projects/{id}`` -> 204; deletes all private project data (SPEC §8)."""
    from radar.documents import service as documents_service

    with session_scope(write=True) as session:
        project = get_project_or_404(session, project_id, owner_id)
        counts = documents_service.delete_project_private_data(session, project_id)
        session.delete(project)
    checkpoint_wal()
    log.info("project %s deleted (%s)", project_id, _count_summary(counts))
    return Response(status_code=204)


def _count_summary(counts: Any) -> str:
    if isinstance(counts, dict):
        return ", ".join(f"{k}={v}" for k, v in counts.items() if isinstance(v, (int, float, bool)))
    return "no counts"
