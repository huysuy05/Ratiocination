"""Interactions -> states, training labels, anchors and undo (SPEC §11). Owner: store.

DB-only: never imports ``radar.jobs``, ``radar.graph``, ``radar.ranking`` or ``radar.net``.

Semantics (single source of truth for the ranking, library, feed and dashboard code):

* actions: ``relevant`` -> state ``relevant``; ``not_relevant`` -> ``not_relevant``; ``save`` ->
  ``saved``; ``open`` is a passive event that never changes anything;
* the **state** of a paper in a project is the state of its latest non-undone, non-``open``
  interaction; ``project_paper_states`` caches it with ``last_interaction_id`` and ``updated_at`` =
  that interaction's timestamp. A row whose interactions were all undone keeps ``state = NULL``;
  :func:`get_states` never returns such rows;
* **training labels** follow the state: relevant -> ``(1, 1.0)``, saved -> ``(1, 0.5)``,
  not_relevant -> ``(0, 1.0)``; **anchors**: relevant 1.0, saved 0.8 (seeds are anchors too, with
  weight 1.0, but they live in ``project_seeds``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from radar.errors import ApiError, validation_error
from radar.models import (
    Interaction,
    Paper,
    Project,
    ProjectPaperState,
    RecommendationItem,
    RecommendationSlate,
    utcnow,
)

log = logging.getLogger(__name__)

__all__ = [
    "ACTION_TO_STATE",
    "ANCHOR_WEIGHT_BY_STATE",
    "CONTEXT_SOURCES",
    "FEEDBACK_ACTIONS",
    "LIBRARY_STATES",
    "TRAIN_LABEL_BY_STATE",
    "PaperLabel",
    "get_states",
    "label_summary",
    "record_interaction",
    "undo_interaction",
]

ACTION_TO_STATE: dict[str, str] = {"relevant": "relevant", "not_relevant": "not_relevant", "save": "saved"}
"""Feedback action -> paper state; ``open`` changes nothing."""

FEEDBACK_ACTIONS: tuple[str, ...] = (*ACTION_TO_STATE, "open")
TRAIN_LABEL_BY_STATE: dict[str, tuple[int, float]] = {
    "relevant": (1, 1.0),
    "saved": (1, 0.5),
    "not_relevant": (0, 1.0),
}
ANCHOR_WEIGHT_BY_STATE: dict[str, float] = {"relevant": 1.0, "saved": 0.8}
LIBRARY_STATES: tuple[str, ...] = ("relevant", "saved")
CONTEXT_SOURCES: tuple[str, ...] = ("deck", "detail", "library", "graph", "dashboard")
_CHUNK = 500


@dataclass(frozen=True)
class PaperLabel:
    """Per-paper label summary for one project (SPEC §10, §11)."""

    paper_id: int
    state: str
    train_label: tuple[int, float]
    anchor_weight: float | None
    last_interaction_id: int
    last_label_at: datetime


def _label(paper_id: int, state: str, interaction_id: int, timestamp: datetime) -> PaperLabel:
    return PaperLabel(
        paper_id=paper_id,
        state=state,
        train_label=TRAIN_LABEL_BY_STATE[state],
        anchor_weight=ANCHOR_WEIGHT_BY_STATE.get(state),
        last_interaction_id=interaction_id,
        last_label_at=timestamp,
    )


# ------------------------------------------------------------------------------------------------
# Writes
# ------------------------------------------------------------------------------------------------


def _clean_context(context: Any) -> dict[str, Any]:
    """Keep only a known ``source`` (context_json never holds evidence or text)."""
    if context is not None and hasattr(context, "model_dump"):
        context = context.model_dump(exclude_none=True)
    if not isinstance(context, Mapping):
        return {}
    source = context.get("source")
    return {"source": source} if source in CONTEXT_SOURCES else {}


def _latest_active(session: Session, project_id: int, paper_id: int) -> Interaction | None:
    """The latest non-undone, non-open interaction of the paper in the project."""
    return session.scalar(
        select(Interaction)
        .where(
            Interaction.project_id == project_id,
            Interaction.paper_id == paper_id,
            Interaction.undone_at.is_(None),
            Interaction.action != "open",
        )
        .order_by(Interaction.id.desc())
        .limit(1)
    )


def _write_state(session: Session, project_id: int, paper_id: int, now: datetime) -> str | None:
    """Recompute and store the paper's state from its interactions; returns the new state."""
    latest = _latest_active(session, project_id, paper_id)
    row = session.scalar(
        select(ProjectPaperState).where(
            ProjectPaperState.project_id == project_id, ProjectPaperState.paper_id == paper_id
        )
    )
    if latest is None:
        if row is not None and (row.state is not None or row.last_interaction_id is not None):
            row.state = None
            row.last_interaction_id = None
            row.updated_at = now
        return None
    state = ACTION_TO_STATE[latest.action]
    if row is None:
        session.add(
            ProjectPaperState(
                project_id=project_id,
                paper_id=paper_id,
                state=state,
                last_interaction_id=latest.id,
                updated_at=latest.timestamp,
            )
        )
    elif (row.state, row.last_interaction_id, row.updated_at) != (state, latest.id, latest.timestamp):
        row.state = state
        row.last_interaction_id = latest.id
        row.updated_at = latest.timestamp
    return state


def _slate_rank(
    session: Session, project_id: int, paper_id: int, slate_id: int
) -> tuple[int | None, int | None]:
    """``(slate_id, rank)`` when the slate belongs to the project, else ``(None, None)``."""
    row = session.execute(
        select(RecommendationSlate.id, RecommendationItem.rank)
        .outerjoin(
            RecommendationItem,
            and_(
                RecommendationItem.slate_id == RecommendationSlate.id, RecommendationItem.paper_id == paper_id
            ),
        )
        .where(RecommendationSlate.id == slate_id, RecommendationSlate.project_id == project_id)
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return int(row[0]), row[1]


def record_interaction(
    session: Session,
    project_id: int,
    paper_id: int,
    action: str,
    *,
    slate_id: int | None = None,
    context: Any = None,
) -> Interaction:
    """Insert an interaction and update the project state (write session; SPEC §11).

    ``rank`` comes from the ``(slate_id, paper_id)`` item; a slate of another project is ignored.
    ``context_json`` keeps only ``source``. Raises ``ApiError`` 422 for an unknown action and 404 for
    an unknown project or paper. Ownership is the caller's job (the router checks the project owner).
    """
    action = str(action or "").strip()
    if action not in FEEDBACK_ACTIONS:
        raise validation_error(
            [{"path": "action", "message": "Unknown feedback action"}], "Unknown feedback action"
        )
    project = session.get(Project, project_id)
    if project is None:
        raise ApiError(404, "not_found", "Project not found")
    if session.get(Paper, paper_id) is None:
        raise ApiError(404, "not_found", "Paper not found")

    stored_slate_id, rank = (None, None)
    if slate_id is not None:
        stored_slate_id, rank = _slate_rank(session, project_id, paper_id, int(slate_id))
    now = utcnow()
    interaction = Interaction(
        owner_id=project.owner_id,
        project_id=project_id,
        paper_id=paper_id,
        action=action,
        timestamp=now,
        slate_id=stored_slate_id,
        rank=rank,
        context_json=_clean_context(context),
    )
    session.add(interaction)
    session.flush()
    if action != "open":
        _write_state(session, project_id, paper_id, now)
        session.flush()
    log.info("project %s paper %s: interaction %s (%s)", project_id, paper_id, interaction.id, action)
    return interaction


def undo_interaction(session: Session, project_id: int, interaction_id: int | None = None) -> Interaction:
    """Undo the given (default: latest non-undone, non-open) interaction and recompute the state.

    Raises ``ApiError`` 404 ``"Nothing to undo"`` when there is no such interaction (unknown id,
    another project, already undone, or an ``open``). Write session.
    """
    target: Interaction | None
    if interaction_id is None:
        target = session.scalar(
            select(Interaction)
            .where(
                Interaction.project_id == project_id,
                Interaction.undone_at.is_(None),
                Interaction.action != "open",
            )
            .order_by(Interaction.id.desc())
            .limit(1)
        )
    else:
        target = session.get(Interaction, interaction_id)
        if (
            target is None
            or target.project_id != project_id
            or target.undone_at is not None
            or target.action == "open"
        ):
            target = None
    if target is None:
        raise ApiError(404, "not_found", "Nothing to undo")

    now = utcnow()
    target.undone_at = now
    session.flush()
    _write_state(session, project_id, target.paper_id, now)
    session.flush()
    log.info("project %s paper %s: interaction %s undone", project_id, target.paper_id, target.id)
    return target


# ------------------------------------------------------------------------------------------------
# Reads
# ------------------------------------------------------------------------------------------------


def get_states(
    session: Session, project_id: int, paper_ids: Iterable[int] | None = None
) -> dict[int, ProjectPaperState]:
    """Current state rows by paper id (all papers of the project when ``paper_ids`` is None).

    Only rows with a state are returned (a row left behind by a full undo means "no state").
    """
    query = select(ProjectPaperState).where(
        ProjectPaperState.project_id == project_id, ProjectPaperState.state.is_not(None)
    )
    if paper_ids is None:
        return {row.paper_id: row for row in session.scalars(query)}
    ids = sorted({int(i) for i in paper_ids})
    out: dict[int, ProjectPaperState] = {}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        out.update(
            {row.paper_id: row for row in session.scalars(query.where(ProjectPaperState.paper_id.in_(chunk)))}
        )
    return out


def label_summary(session: Session, project_id: int) -> dict[int, PaperLabel]:
    """Training labels and anchor weights of every paper with a state in the project (one query)."""
    rows = session.execute(
        select(
            ProjectPaperState.paper_id,
            ProjectPaperState.state,
            ProjectPaperState.last_interaction_id,
            ProjectPaperState.updated_at,
        ).where(
            ProjectPaperState.project_id == project_id,
            ProjectPaperState.state.is_not(None),
            ProjectPaperState.last_interaction_id.is_not(None),
        )
    ).all()
    return {
        paper_id: _label(paper_id, state, interaction_id, updated_at)
        for paper_id, state, interaction_id, updated_at in rows
        if state in TRAIN_LABEL_BY_STATE
    }
