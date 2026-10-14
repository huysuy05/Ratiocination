"""Auto-expansion trigger (SPEC §11, §12): radar.jobs.triggers.maybe_queue_expansion."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from radar import models
from radar.db import session_scope
from radar.jobs import registry, runner, triggers

NOW = datetime(2026, 10, 12, 0, 30, tzinfo=UTC)


@pytest.fixture
def expand_calls() -> Iterator[list[dict[str, Any]]]:
    """A fake ``expand`` job function recording its calls."""
    calls: list[dict[str, Any]] = []

    def fake_expand(
        ctx: runner.JobContext, paper_id: int, force: bool = False, auto: bool = False
    ) -> dict[str, Any]:
        calls.append({"job_id": ctx.job_id, "project_id": ctx.project_id, "paper_id": paper_id, "auto": auto})
        return {}

    registry.register("expand", fake_expand)
    yield calls
    registry.unregister("expand")


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(triggers, "_now", lambda: NOW)
    return NOW


@pytest.fixture
def project_paper(make_project: Callable[..., int], make_paper: Callable[..., int]) -> tuple[int, int]:
    return make_project(), make_paper()


def trigger(project_id: int, paper_id: int, action: str = "relevant") -> int | None:
    with session_scope(write=True) as s:
        return triggers.maybe_queue_expansion(s, project_id, paper_id, action)


def set_pref(**values: Any) -> None:
    with session_scope(write=True) as s:
        user = s.get(models.User, models.LOCAL_USER_ID)
        assert user is not None
        prefs = {**user.preferences_json, **values}
        for key in [k for k, v in values.items() if v is None]:
            prefs.pop(key)
        user.preferences_json = prefs


def add_job(paper_id: int, *, status: str, auto: bool | None, created_at: datetime, key: str) -> int:
    params: dict[str, Any] = {"paper_id": paper_id}
    if auto is not None:
        params["auto"] = auto
    with session_scope(write=True) as s:
        job = models.Job(
            kind="expand", status=status, params_json=params, dedupe_key=key, created_at=created_at
        )
        s.add(job)
        s.flush()
        return job.id


def expand_jobs() -> list[models.Job]:
    with session_scope() as s:
        return list(s.scalars(select(models.Job).where(models.Job.kind == "expand").order_by(models.Job.id)))


# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["relevant", "save"])
def test_relevant_and_save_queue_an_auto_expansion(
    action: str, project_paper: tuple[int, int], expand_calls: list[dict[str, Any]]
) -> None:
    project_id, paper_id = project_paper
    job_id = trigger(project_id, paper_id, action)
    assert job_id is not None
    job = expand_jobs()[-1]
    assert job.id == job_id
    assert (job.project_id, job.params_json) == (project_id, {"auto": True, "paper_id": paper_id})
    assert job.owner_id == models.LOCAL_USER_ID  # the project's owner
    assert expand_calls == [{"job_id": job_id, "project_id": project_id, "paper_id": paper_id, "auto": True}]


@pytest.mark.parametrize("action", ["not_relevant", "open", "pin", "already_read", "hide_topic", "bogus"])
def test_other_actions_never_queue(
    action: str, project_paper: tuple[int, int], expand_calls: list[dict[str, Any]]
) -> None:
    assert trigger(*project_paper, action) is None
    assert expand_jobs() == [] and expand_calls == []


def test_the_job_is_dispatched_only_after_the_callers_commit(
    project_paper: tuple[int, int], expand_calls: list[dict[str, Any]]
) -> None:
    project_id, paper_id = project_paper
    with session_scope(write=True) as s:
        job_id = triggers.maybe_queue_expansion(s, project_id, paper_id, "save")
        assert job_id is not None and expand_calls == []
    assert [c["job_id"] for c in expand_calls] == [job_id]

    other_paper = paper_id + 1000
    with pytest.raises(RuntimeError, match="rollback"):
        with session_scope(write=True) as s:
            assert triggers.maybe_queue_expansion(s, project_id, other_paper, "relevant") is not None
            raise RuntimeError("rollback")
    assert len(expand_calls) == 1
    assert [j.params_json["paper_id"] for j in expand_jobs()] == [paper_id]


def test_auto_expand_preference(project_paper: tuple[int, int], expand_calls: list[dict[str, Any]]) -> None:
    project_id, paper_id = project_paper
    set_pref(auto_expand=False)
    assert trigger(project_id, paper_id) is None
    assert expand_jobs() == []
    set_pref(auto_expand=None)  # key missing -> default true (API default)
    assert trigger(project_id, paper_id) is not None


def test_unknown_project_never_queues(
    make_paper: Callable[..., int], expand_calls: list[dict[str, Any]]
) -> None:
    assert trigger(424242, make_paper()) is None
    assert expand_jobs() == []


def test_recently_expanded_paper_is_skipped(
    fixed_now: datetime,
    make_project: Callable[..., int],
    make_paper: Callable[..., int],
    expand_calls: list[dict[str, Any]],
) -> None:
    project_id = make_project()
    recent, old, never = make_paper(), make_paper(), make_paper()
    with session_scope(write=True) as s:
        s.add(
            models.PaperExpansion(
                paper_id=recent, status="done", last_expanded_at=fixed_now - timedelta(days=6)
            )
        )
        s.add(
            models.PaperExpansion(paper_id=old, status="done", last_expanded_at=fixed_now - timedelta(days=8))
        )
        s.add(models.PaperExpansion(paper_id=never, status="failed", last_expanded_at=None))
    assert trigger(project_id, recent) is None
    assert trigger(project_id, old) is not None
    assert trigger(project_id, never) is not None


def test_active_expand_job_for_the_paper_is_skipped(
    fixed_now: datetime,
    make_project: Callable[..., int],
    make_paper: Callable[..., int],
    expand_calls: list[dict[str, Any]],
) -> None:
    project_id = make_project()
    busy, other, finished = make_paper(), make_paper(), make_paper()
    add_job(busy, status="queued", auto=None, created_at=fixed_now, key="manual-busy")  # manual, any project
    add_job(9999, status="running", auto=True, created_at=fixed_now, key="other-paper")
    add_job(finished, status="succeeded", auto=True, created_at=fixed_now, key="finished")
    assert trigger(project_id, busy) is None
    assert trigger(project_id, other) is not None
    assert trigger(project_id, finished) is not None


def test_daily_cap_counts_only_auto_jobs_created_today_utc(
    set_env: Callable[..., None],
    fixed_now: datetime,
    make_project: Callable[..., int],
    make_paper: Callable[..., int],
    expand_calls: list[dict[str, Any]],
) -> None:
    set_env(RADAR_MAX_EXPANSIONS_PER_DAY="2")
    project_id = make_project()
    today = fixed_now.replace(hour=0, minute=0, second=1)
    yesterday = fixed_now.replace(hour=0, minute=0, second=0) - timedelta(seconds=1)
    add_job(1001, status="succeeded", auto=True, created_at=today, key="auto-today")
    for n in range(3):  # manual expansions never count
        add_job(1100 + n, status="succeeded", auto=None, created_at=today, key=f"manual-{n}")
    add_job(1200, status="succeeded", auto=False, created_at=today, key="auto-false")
    for n in range(5):  # yesterday (UTC) does not count
        add_job(1300 + n, status="failed", auto=True, created_at=yesterday, key=f"auto-yesterday-{n}")
    with session_scope() as s:
        assert triggers.auto_expansions_today(s, fixed_now) == 1

    first = trigger(project_id, make_paper())  # 1 < 2: allowed (created now, i.e. today)
    assert first is not None
    assert trigger(project_id, make_paper()) is None  # 2 auto jobs today: capped
    assert trigger(project_id, make_paper(), "save") is None
    with session_scope() as s:
        assert triggers.auto_expansions_today(s, fixed_now) == 2
        total = s.scalar(select(func.count()).select_from(models.Job).where(models.Job.kind == "expand"))
    assert total == 11


def test_cap_zero_disables_auto_expansion(
    set_env: Callable[..., None], project_paper: tuple[int, int], expand_calls: list[dict[str, Any]]
) -> None:
    set_env(RADAR_MAX_EXPANSIONS_PER_DAY="0")
    assert trigger(*project_paper) is None
    assert expand_jobs() == []
