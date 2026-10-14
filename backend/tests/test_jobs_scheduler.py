"""Daily scheduler wiring (SPEC §15) - inspected without waiting for real time."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from radar import models
from radar.db import flush_writes, session_scope
from radar.jobs import registry, runner, scheduler
from radar.store.app_settings import get_setting, set_setting


@pytest.fixture(autouse=True)
def _stop_scheduler() -> Iterator[None]:
    yield
    scheduler.shutdown()


@pytest.fixture
def daily_calls() -> Iterator[list[int | None]]:
    """A fake ``daily`` job function (in case a test ever fires the real trigger)."""
    calls: list[int | None] = []
    registry.register("daily", lambda ctx: calls.append(ctx.job_id) or {"fake": True})
    yield calls
    registry.unregister("daily")


def stored_pref() -> Any:
    """The global ``scheduler_enabled`` setting (``"missing"`` when never stored)."""
    with session_scope() as s:
        return get_setting(s, "scheduler_enabled", "missing")


def set_pref(value: Any) -> None:
    with session_scope(write=True) as s:
        if value == "missing":
            s.execute(delete(models.AppSetting).where(models.AppSetting.key == "scheduler_enabled"))
        else:
            set_setting(s, "scheduler_enabled", value)


def daily_job() -> Any:
    sched = scheduler.get_scheduler()
    assert sched is not None and sched.running
    job = sched.get_job(scheduler.JOB_ID)
    assert job is not None
    return job


def fields(trigger: CronTrigger) -> dict[str, str]:
    return {f.name: str(f) for f in trigger.fields}


# ------------------------------------------------------------------------------------------------


def test_disabled_by_default(daily_calls: list[int | None]) -> None:
    assert stored_pref() is False  # seeded from RADAR_SCHEDULER (unset in tests)
    assert scheduler.is_enabled() is False
    scheduler.start_if_enabled()
    assert not scheduler.running()
    assert scheduler.next_run_at() is None


def test_start_if_enabled_wires_the_daily_cron_job(daily_calls: list[int | None]) -> None:
    set_pref(True)
    scheduler.start_if_enabled()
    assert scheduler.running()
    job = daily_job()
    assert job.id == "daily"
    assert job.func is scheduler.submit_daily
    assert isinstance(job.trigger, CronTrigger)
    assert fields(job.trigger) == {
        "year": "*",
        "month": "*",
        "day": "*",
        "week": "*",
        "day_of_week": "*",
        "hour": "0",
        "minute": "30",
        "second": "0",
    }
    assert job.trigger.timezone == ZoneInfo("America/New_York")
    assert job.misfire_grace_time == 6 * 3600
    assert job.coalesce is True
    assert job.max_instances == 1

    nxt = scheduler.next_run_at()
    assert nxt is not None and nxt.utcoffset() == timedelta(0) and nxt.tzinfo is not None
    now = datetime.now(UTC)
    assert now < nxt <= now + timedelta(days=1, hours=1)
    local = nxt.astimezone(ZoneInfo("America/New_York"))
    assert (local.hour, local.minute, local.second) == (0, 30, 0)
    assert daily_calls == []


def test_cron_and_timezone_come_from_settings(
    set_env: Callable[..., None], daily_calls: list[int | None]
) -> None:
    set_env(RADAR_DAILY_CRON="15 6 * * 1-5", RADAR_TIMEZONE="Europe/Berlin")
    set_pref(True)
    scheduler.start_if_enabled()
    job = daily_job()
    got = fields(job.trigger)
    assert (got["hour"], got["minute"], got["day_of_week"]) == ("6", "15", "mon,tue,wed,thu,fri")
    assert job.trigger.timezone == ZoneInfo("Europe/Berlin")
    nxt = scheduler.next_run_at()
    assert nxt is not None
    local = nxt.astimezone(ZoneInfo("Europe/Berlin"))
    assert (local.hour, local.minute) == (6, 15)
    assert local.weekday() < 5  # Monday..Friday, as in crontab


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("*", "*"),
        ("1-5", "mon,tue,wed,thu,fri"),
        ("0", "sun"),
        ("7", "sun"),
        ("5-7", "sun,fri,sat"),
        ("0,6", "sun,sat"),
        ("*/2", "sun,tue,thu,sat"),
        ("1-5/2", "mon,wed,fri"),
        ("mon-fri", "mon,tue,wed,thu,fri"),
        ("sun", "sun"),
        ("SAT", "sat"),
        ("8", "8"),  # invalid: left for APScheduler to reject
        ("fri-mon", "fri-mon"),
    ],
)
def test_crontab_day_of_week_translation(field: str, expected: str) -> None:
    assert scheduler.crontab_day_of_week(field) == expected


@pytest.mark.parametrize(
    ("expr", "weekdays"),
    [("0 7 * * 1-5", {0, 1, 2, 3, 4}), ("0 7 * * 0", {6}), ("0 7 * * 7", {6}), ("0 7 * * 6,0", {5, 6})],
)
def test_daily_trigger_uses_crontab_weekdays(
    set_env: Callable[..., None], expr: str, weekdays: set[int]
) -> None:
    set_env(RADAR_DAILY_CRON=expr, RADAR_TIMEZONE="UTC")
    trigger = scheduler.daily_trigger()
    fire, seen = None, set()
    now = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    for _ in range(14):
        fire = trigger.get_next_fire_time(fire, now)
        assert fire is not None
        seen.add(fire.weekday())
        now = fire + timedelta(minutes=1)
    assert seen == weekdays


def test_invalid_cron_is_rejected_before_persisting(set_env: Callable[..., None]) -> None:
    set_env(RADAR_DAILY_CRON="0 7 * * 9")
    with pytest.raises(ValueError, match="RADAR_DAILY_CRON"):
        scheduler.set_enabled(True)
    assert stored_pref() is False
    assert not scheduler.running()


def test_stored_preference_wins_over_the_environment(
    set_env: Callable[..., None], daily_calls: list[int | None]
) -> None:
    set_env(RADAR_SCHEDULER="1")
    set_pref(False)
    scheduler.start_if_enabled()
    assert not scheduler.running()
    set_pref("missing")  # never set: RADAR_SCHEDULER seeds it
    assert scheduler.is_enabled() is True
    scheduler.start_if_enabled()
    assert scheduler.running()
    scheduler.shutdown()
    set_env(RADAR_SCHEDULER="0")
    set_pref(True)
    scheduler.start_if_enabled()
    assert scheduler.running()


def test_set_enabled_persists_and_toggles(daily_calls: list[int | None]) -> None:
    scheduler.set_enabled(True)
    assert stored_pref() is True
    assert scheduler.running() and scheduler.next_run_at() is not None
    first = scheduler.get_scheduler()
    scheduler.set_enabled(True)  # idempotent: same scheduler keeps running
    assert scheduler.get_scheduler() is first
    scheduler.set_enabled(False)
    assert stored_pref() is False
    assert not scheduler.running()
    assert scheduler.next_run_at() is None
    scheduler.set_enabled(False)
    scheduler.set_enabled(True)
    assert scheduler.running()
    assert daily_job().id == "daily"  # a fresh scheduler carries the job again
    with session_scope() as s:
        user = s.get(models.User, models.LOCAL_USER_ID)
        assert user is not None
        assert user.preferences_json["auto_expand"] is True  # user preferences are separate


def test_set_enabled_inside_a_write_transaction_does_not_block(daily_calls: list[int | None]) -> None:
    with session_scope(write=True) as s:
        project = models.Project(name="Test project sched", research_question="Test question?")
        s.add(project)
        s.flush()
        scheduler.set_enabled(True)  # would self-deadlock on BEGIN IMMEDIATE without the writer
        assert scheduler.running()
    flush_writes()
    assert stored_pref() is True


def test_submit_daily_queues_the_daily_job(daily_calls: list[int | None]) -> None:
    job_id = scheduler.submit_daily()
    assert job_id is not None
    assert daily_calls == [job_id]  # RADAR_JOBS_INLINE=1 in tests
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        assert job is not None and (job.kind, job.project_id, job.status) == ("daily", None, "succeeded")


def test_submit_daily_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runner, "submit_job", broken)
    assert scheduler.submit_daily() is None


def test_lifespan_starts_and_stops_the_scheduler(daily_calls: list[int | None]) -> None:
    from fastapi.testclient import TestClient

    from radar.main import create_app

    set_pref(True)
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as c:
        assert c.get("/api/health").status_code == 200
        assert scheduler.running()
    assert not scheduler.running()
