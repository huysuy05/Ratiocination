"""store.states (SPEC §11): interactions -> states, training labels, anchors, undo."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from sqlalchemy import select

from radar import models
from radar.db import session_scope
from radar.errors import ApiError
from radar.store.states import (
    ACTION_TO_STATE,
    FEEDBACK_ACTIONS,
    PaperLabel,
    get_states,
    label_summary,
    record_interaction,
    undo_interaction,
)

# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


def make_slate(project_id: int, paper_ids: list[int]) -> int:
    """A stored slate whose items get ranks 1..n in the given order."""
    with session_scope(write=True) as session:
        slate = models.RecommendationSlate(
            project_id=project_id,
            mode="daily",
            ranker_version="test-ranker",
            embedding_model="local/hashing-v1",
            candidate_set_hash="0" * 64,
            pool_size=len(paper_ids),
        )
        session.add(slate)
        session.flush()
        for rank, paper_id in enumerate(paper_ids, start=1):
            session.add(models.RecommendationItem(slate_id=slate.id, paper_id=paper_id, rank=rank, score=0.5))
        return slate.id


def act(
    project_id: int, paper_id: int, action: str, *, slate_id: int | None = None, context: Any = None
) -> models.Interaction:
    with session_scope(write=True) as session:
        interaction = record_interaction(
            session, project_id, paper_id, action, slate_id=slate_id, context=context
        )
        session.expunge(interaction)
        return interaction


def undo(project_id: int, interaction_id: int | None = None) -> models.Interaction:
    with session_scope(write=True) as session:
        target = undo_interaction(session, project_id, interaction_id)
        session.expunge(target)
        return target


def state_of(project_id: int, paper_id: int) -> str | None:
    with session_scope() as session:
        row = get_states(session, project_id, [paper_id]).get(paper_id)
        return row.state if row is not None else None


def labels(project_id: int) -> dict[int, PaperLabel]:
    with session_scope() as session:
        return label_summary(session, project_id)


# ------------------------------------------------------------------------------------------------
# Recording
# ------------------------------------------------------------------------------------------------


def test_vocabulary() -> None:
    assert ACTION_TO_STATE == {"relevant": "relevant", "not_relevant": "not_relevant", "save": "saved"}
    assert FEEDBACK_ACTIONS == ("relevant", "not_relevant", "save", "open")


@pytest.mark.parametrize(
    ("action", "state"), [("relevant", "relevant"), ("not_relevant", "not_relevant"), ("save", "saved")]
)
def test_actions_set_states(make_project: Any, make_paper: Any, action: str, state: str) -> None:
    project, paper = make_project(), make_paper()
    interaction = act(project, paper, action)
    assert interaction.owner_id == models.LOCAL_USER_ID and interaction.action == action
    with session_scope() as session:
        row = get_states(session, project, [paper])[paper]
        assert (row.state, row.last_interaction_id) == (state, interaction.id)
        assert row.updated_at == interaction.timestamp
    assert state_of(project, paper) == state


def test_open_is_passive(make_project: Any, make_paper: Any) -> None:
    project, paper = make_project(), make_paper()
    act(project, paper, "open", context={"source": "deck"})
    assert state_of(project, paper) is None
    saved = act(project, paper, "save")
    act(project, paper, "open")
    with session_scope() as session:
        row = get_states(session, project, [paper])[paper]
        assert (row.state, row.last_interaction_id) == ("saved", saved.id)  # open does not move it


def test_latest_action_wins(make_project: Any, make_paper: Any) -> None:
    project, paper = make_project(), make_paper()
    act(project, paper, "save")
    act(project, paper, "not_relevant")
    assert state_of(project, paper) == "not_relevant"
    act(project, paper, "relevant")
    assert state_of(project, paper) == "relevant"


def test_rank_comes_from_the_projects_own_slate(make_project: Any, make_paper: Any) -> None:
    project, other = make_project(), make_project()
    a, b = make_paper(), make_paper()
    slate = make_slate(project, [a, b])
    foreign = make_slate(other, [b])
    on_deck = act(project, b, "relevant", slate_id=slate)
    assert (on_deck.slate_id, on_deck.rank) == (slate, 2)
    not_on_deck = act(project, make_paper(), "relevant", slate_id=slate)  # slate kept, no rank
    assert (not_on_deck.slate_id, not_on_deck.rank) == (slate, None)
    elsewhere = act(project, a, "save", slate_id=foreign)  # another project's slate is ignored
    assert (elsewhere.slate_id, elsewhere.rank) == (None, None)


def test_context_keeps_only_a_known_source(make_project: Any, make_paper: Any) -> None:
    project, paper = make_project(), make_paper()
    assert act(project, paper, "open", context={"source": "dashboard", "note": "private"}).context_json == {
        "source": "dashboard"
    }
    assert act(project, paper, "open", context={"source": "elsewhere"}).context_json == {}
    assert act(project, paper, "open", context="not a mapping").context_json == {}


def test_record_errors(make_project: Any, make_paper: Any) -> None:
    project, paper = make_project(), make_paper()
    with pytest.raises(ApiError) as info:
        act(project, paper, "pin")  # removed in v0.3
    assert (info.value.status, info.value.code) == (422, "validation_error")
    for project_id, paper_id in ((987654, paper), (project, 987654)):
        with pytest.raises(ApiError) as info:
            act(project_id, paper_id, "relevant")
        assert info.value.status == 404


# ------------------------------------------------------------------------------------------------
# Undo
# ------------------------------------------------------------------------------------------------


def test_undo_latest_recomputes_the_state(make_project: Any, make_paper: Any) -> None:
    project, a, b = make_project(), make_paper(), make_paper()
    first = act(project, a, "save")
    act(project, a, "open")
    second = act(project, b, "relevant")
    act(project, b, "open")
    target = undo(project)  # skips the open events
    assert target.id == second.id and target.undone_at is not None
    assert state_of(project, b) is None
    with session_scope() as session:
        row = session.scalar(
            select(models.ProjectPaperState).where(
                models.ProjectPaperState.project_id == project, models.ProjectPaperState.paper_id == b
            )
        )
        assert row is not None and row.state is None and row.last_interaction_id is None  # row kept, no state
    assert undo(project).id == first.id and state_of(project, a) is None
    with pytest.raises(ApiError) as info:
        undo(project)
    assert (info.value.status, info.value.message) == (404, "Nothing to undo")


def test_undo_restores_the_previous_action(make_project: Any, make_paper: Any) -> None:
    project, paper = make_project(), make_paper()
    saved = act(project, paper, "save")
    rejected = act(project, paper, "not_relevant")
    undo(project, rejected.id)
    with session_scope() as session:
        row = get_states(session, project, [paper])[paper]
        assert (row.state, row.last_interaction_id, row.updated_at) == ("saved", saved.id, saved.timestamp)


def test_undo_errors(make_project: Any, make_paper: Any) -> None:
    project, other, paper = make_project(), make_project(), make_paper()
    opened = act(project, paper, "open")
    done = act(project, paper, "relevant")
    undo(project, done.id)
    for project_id, interaction_id in (
        (project, opened.id),
        (project, done.id),
        (other, done.id),
        (project, 987654),
    ):
        with pytest.raises(ApiError) as info:
            undo(project_id, interaction_id)
        assert info.value.status == 404


# ------------------------------------------------------------------------------------------------
# Labels and reads
# ------------------------------------------------------------------------------------------------


def test_labels_follow_states(make_project: Any, make_paper: Any) -> None:
    project = make_project()
    relevant, saved, rejected, opened, undone = (make_paper() for _ in range(5))
    act(project, relevant, "relevant")
    act(project, saved, "save")
    act(project, rejected, "not_relevant")
    act(project, opened, "open")
    act(project, undone, "relevant")
    undo(project)
    summary = labels(project)
    assert set(summary) == {relevant, saved, rejected}
    assert (summary[relevant].train_label, summary[relevant].anchor_weight) == ((1, 1.0), 1.0)
    assert (summary[saved].train_label, summary[saved].anchor_weight) == ((1, 0.5), 0.8)
    assert (summary[rejected].train_label, summary[rejected].anchor_weight) == ((0, 1.0), None)
    assert summary[saved].state == "saved" and summary[saved].last_label_at is not None


def test_get_states_is_per_project(make_project: Any, make_paper: Any) -> None:
    project, other = make_project(), make_project()
    a, b, c = make_paper(), make_paper(), make_paper()
    act(project, a, "relevant")
    act(project, b, "save")
    act(project, c, "open")
    act(other, c, "not_relevant")
    with session_scope() as session:
        states = get_states(session, project)
        assert {pid: row.state for pid, row in states.items()} == {a: "relevant", b: "saved"}
        assert set(get_states(session, project, [b, c])) == {b}
        assert set(get_states(session, project, iter([a]))) == {a}
        assert get_states(session, project, []) == {}
        assert {pid: row.state for pid, row in get_states(session, other).items()} == {c: "not_relevant"}
    assert set(labels(project)) == {a, b}


def test_store_states_stays_db_only() -> None:
    code = (
        "import sys, radar.store.states, radar.store.papers, radar.store.graph, radar.api.serializers\n"
        "bad = [m for m in sys.modules if m.split('.')[:2] in (['radar', 'jobs'], ['radar', 'graph'],"
        " ['radar', 'ranking'], ['radar', 'net'], ['radar', 'connectors'], ['radar', 'auth'])]\n"
        "print(','.join(sorted(bad)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
