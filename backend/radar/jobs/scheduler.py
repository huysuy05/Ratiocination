"""Daily scheduler: APScheduler 3.x BackgroundScheduler (SPEC §16). Owner: jobs.

* One cron job, id ``daily``: ``CronTrigger.from_crontab(RADAR_DAILY_CRON,
  timezone=ZoneInfo(RADAR_TIMEZONE))`` with ``misfire_grace_time=6 h``, ``coalesce=True`` and
  ``max_instances=1``; it calls :func:`submit_daily`, which only queues the ``daily`` job (deduplicated
  against an active one) and returns.
* ``RADAR_DAILY_CRON`` has crontab semantics. APScheduler 3 numbers weekdays from Monday = 0 (crontab:
  Sunday = 0 or 7), so the day-of-week field is translated to day names first (``1-5`` ->
  ``mon,tue,wed,thu,fri``). Like every APScheduler trigger, day-of-month AND day-of-week must both
  match when both are restricted (crontab would OR them).
* Enabled state: the global ``app_settings`` key ``scheduler_enabled`` wins once set;
  ``RADAR_SCHEDULER`` only seeds it (``init_db`` writes the seed; :func:`is_enabled` falls back to it
  when the key is missing).
* :func:`set_enabled` validates the cron expression, persists the preference and starts/stops the
  scheduler. It never blocks on the SQLite write lock of its own thread: called while the thread
  holds a write transaction, the preference is written by the background writer right after that
  transaction ends.
* :func:`next_run_at` is the live job's next fire time in UTC (``None`` while not running).
* A fresh ``BackgroundScheduler`` is built on every start (APScheduler forgets memory-store jobs on
  shutdown); :func:`shutdown` calls ``scheduler.shutdown(wait=False)``.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from radar import db
from radar.config import get_settings
from radar.store.app_settings import get_setting, set_setting

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

JOB_ID = "daily"
MISFIRE_GRACE_SECONDS = 6 * 3600

_DOW_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

_lock = threading.RLock()
_scheduler: BackgroundScheduler | None = None


# ------------------------------------------------------------------------------------------------
# Trigger
# ------------------------------------------------------------------------------------------------


def _dow_number(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        value = int(token)
        return value if 0 <= value <= 7 else None
    return _DOW_NAMES.index(token) if token in _DOW_NAMES else None


def crontab_day_of_week(field: str) -> str:
    """Translate a crontab day-of-week field (0 or 7 = Sunday) into APScheduler 3 day names.

    ``*`` stays ``*``; anything else becomes an explicit, comma-separated list of day names
    (``1-5`` -> ``mon,tue,wed,thu,fri``; ``*/2`` -> ``sun,tue,thu,sat``; ``5-7`` -> ``sun,fri,sat``).
    A field that cannot be parsed is returned unchanged (APScheduler then reports the error).
    """
    field = field.strip()
    if field == "*":
        return field
    days: set[int] = set()
    for part in field.split(","):
        base, slash, step_text = part.strip().partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                return field
            step = int(step_text)
        if base == "*":
            start, end = 0, 7
        elif "-" in base:
            first, last = base.split("-", 1)
            start_n, end_n = _dow_number(first), _dow_number(last)
            if start_n is None or end_n is None or start_n > end_n:
                return field
            start, end = start_n, end_n
        else:
            single = _dow_number(base)
            if single is None:
                return field
            start, end = single, (7 if slash else single)
        days.update(day % 7 for day in range(start, end + 1, step))
    if not days:
        return field
    return ",".join(_DOW_NAMES[day] for day in sorted(days))


def daily_trigger() -> CronTrigger:
    """``CronTrigger.from_crontab(RADAR_DAILY_CRON, timezone=ZoneInfo(RADAR_TIMEZONE))`` with the
    day-of-week field translated to crontab semantics. Raises ``ValueError`` for an invalid expression."""
    from apscheduler.triggers.cron import CronTrigger

    settings = get_settings()
    fields = settings.daily_cron.split()
    if len(fields) == 5:
        fields[4] = crontab_day_of_week(fields[4])
    try:
        return CronTrigger.from_crontab(" ".join(fields), timezone=ZoneInfo(settings.timezone))
    except ValueError as exc:
        raise ValueError(f"RADAR_DAILY_CRON is not a valid crontab expression: {exc}") from exc


def build_scheduler() -> BackgroundScheduler:
    """A new (not started) BackgroundScheduler holding the ``daily`` cron job."""
    from apscheduler.schedulers.background import BackgroundScheduler

    trigger = daily_trigger()
    scheduler = BackgroundScheduler(timezone=ZoneInfo(get_settings().timezone))
    scheduler.add_job(
        submit_daily,
        trigger,
        id=JOB_ID,
        name="daily pipeline",
        replace_existing=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


# ------------------------------------------------------------------------------------------------
# State
# ------------------------------------------------------------------------------------------------


def is_enabled() -> bool:
    """The effective scheduler preference: the stored value once set, else ``RADAR_SCHEDULER``."""
    with db.session_scope() as session:
        value = get_setting(session, "scheduler_enabled")
    if isinstance(value, bool):
        return value
    return bool(get_settings().scheduler)


def running() -> bool:
    """True while the scheduler of this process is running."""
    with _lock:
        return _scheduler is not None and bool(_scheduler.running)


def get_scheduler() -> BackgroundScheduler | None:
    """The running scheduler, if any (inspection and tests)."""
    with _lock:
        return _scheduler


def _start() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        scheduler = build_scheduler()
        scheduler.start()
        _scheduler = scheduler
    log.info("daily scheduler started")


def _stop() -> None:
    global _scheduler
    with _lock:
        scheduler, _scheduler = _scheduler, None
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("daily scheduler stopped")


def _persist(enabled: bool) -> None:
    def apply(session: Session) -> None:
        set_setting(session, "scheduler_enabled", enabled)

    if db.holding_write_transaction():
        db.enqueue_write(apply)  # applied after this thread's transaction ends (no self-deadlock)
        return
    with db.session_scope(write=True) as session:
        apply(session)


def start_if_enabled() -> None:
    """Start the scheduler when the ``scheduler_enabled`` setting is true."""
    if is_enabled():
        _start()


def set_enabled(enabled: bool) -> None:
    """Persist ``scheduler_enabled`` and start or stop the daily job."""
    enabled = bool(enabled)
    if enabled:
        daily_trigger()  # validate before persisting: an invalid cron must not be saved as enabled
    _persist(enabled)
    if enabled:
        _start()
    else:
        _stop()


def next_run_at() -> datetime | None:
    """Next fire time of the daily job (aware UTC), or ``None`` when the scheduler is not running."""
    with _lock:
        scheduler = _scheduler
        if scheduler is None or not scheduler.running:
            return None
        job = scheduler.get_job(JOB_ID)
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.astimezone(UTC)


def shutdown() -> None:
    """``scheduler.shutdown(wait=False)``."""
    _stop()


def submit_daily() -> int | None:
    """Scheduler callback: submit the ``daily`` job (deduplicated); returns its id, ``None`` on error."""
    from radar.jobs.runner import submit_job

    try:
        job_id = submit_job("daily")
    except Exception:
        log.exception("the scheduled daily run could not be queued")
        return None
    log.info("scheduled daily run queued as job %s", job_id)
    return job_id


__all__ = [
    "JOB_ID",
    "MISFIRE_GRACE_SECONDS",
    "build_scheduler",
    "crontab_day_of_week",
    "daily_trigger",
    "get_scheduler",
    "is_enabled",
    "next_run_at",
    "running",
    "set_enabled",
    "shutdown",
    "start_if_enabled",
    "submit_daily",
]
