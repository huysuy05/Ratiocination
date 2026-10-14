"""Job runner (SPEC §16). Owner: jobs.

Submitting
----------
``submit_job(kind, project_id=, params=, dedupe=True, session=None) -> job id``

* ``dedupe_key`` = sha256 of the canonical JSON ``[kind, project_id, params]`` (``build_deck``:
  ``[kind, project_id]``, so one deck build per project is active whatever its mode). With
  ``dedupe=True`` an active (queued/running) job with the same key is returned instead of a new one;
  the partial unique index ``uq_jobs_active`` makes this race-free (a losing insert's
  ``IntegrityError`` is caught inside a SAVEPOINT and the winner's id is returned). ``dedupe=False``
  adds a random nonce to the key (``jobs.dedupe_key`` is NOT NULL). An active duplicate whose
  ``worker_pid`` is another process that no longer exists (e.g. a killed CLI run) is stale: it is
  marked failed ``interrupted`` and a new job is queued.
* ``session=``: the row is inserted in the caller's session and dispatched only after that session's
  outermost transaction commits (session ``after_commit`` / ``after_transaction_end`` hooks; a
  SAVEPOINT release, a rollback or a close never dispatches). Without ``session`` the row is written in
  a short own write session; in strict mode that raises ``RuntimeError("holding write transaction:
  submit_job")`` when the calling thread holds a write transaction.
* ``worker_pid`` is the submitting process at insert (its executor runs the job) and the worker's pid
  once the job starts.

Running
-------
* ``ThreadPoolExecutor(max_workers=2)`` created by :func:`start` (and lazily by the first dispatch).
  ``RADAR_JOBS_INLINE=1`` (tests) runs a job synchronously in the dispatching thread right after the
  commit instead - unless that thread still holds another write transaction, in which case the
  executor is used (running inline would wait on its own SQLite write lock).
* A job is claimed atomically (``queued`` -> ``running`` + ``worker_pid`` + ``started_at``), so it
  never runs twice. The function runs inside ``radar.net.http.call_context(job_id, project_id)`` as
  ``fn(ctx, **params)``; stats = ``{**logged, **returned}`` of the final attempt, made JSON-safe;
  success sets ``progress`` to 1.0; ``finished_at`` is set on success and failure.
* Errors: ``RetryableError`` is retried up to 3 times after 2, 4 and 8 s (sleep injectable with
  :func:`set_retry_sleep`); ``SourceUnavailable`` -> ``source_unavailable``; ``BudgetExceeded`` ->
  ``budget_exceeded``; ``EgressBlocked`` -> ``egress_blocked``; ``NotImplementedError`` ->
  ``not_implemented``; ``ApiError`` -> its own code and message; a ``RetryableError`` still failing
  after the last retry -> the code of its ``__cause__`` when that maps, else ``retries_exhausted``;
  anything else -> ``internal_error`` with ``"internal error (job {id})"`` (traceback logged with the
  job id). Radar exception messages are stored as-is. KeyboardInterrupt/SystemExit mark the job
  ``interrupted`` and propagate.
* Progress (:meth:`JobContext.progress`) goes through the background writer and only touches a
  ``running`` job; pending progress writes are flushed before the final status is written.

Other entry points: :func:`run_job_sync` (CLI: create the row, run it in the calling thread, return
its stats or raise :class:`JobFailed`), :func:`recover_interrupted` (startup), :func:`active_job`,
:func:`wait_idle` (wait for jobs this process dispatched), :func:`dedupe_key`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction

from radar import db
from radar.config import get_settings
from radar.errors import ApiError, RetryableError
from radar.jobs import registry
from radar.models import Job, utcnow

log = logging.getLogger(__name__)

ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running")
MAX_WORKERS = 2
RETRY_DELAYS: tuple[float, ...] = (2.0, 4.0, 8.0)
INTERRUPTED_MESSAGE = "The job was interrupted before it finished"
NOT_IMPLEMENTED_MESSAGE = "This feature is not implemented yet"
RETRIES_EXHAUSTED_MESSAGE = "The job kept failing after 3 retries"

_MAX_TEXT = 1000
_INSERT_ATTEMPTS = 3
_PENDING_KEY = "radar.jobs.pending"
_COMMITTED_KEY = "radar.jobs.committed"


class JobFailed(RuntimeError):
    """A job run by :func:`run_job_sync` did not succeed (``error_code``/``error_message`` as stored)."""

    def __init__(
        self, job_id: int, error_code: str, error_message: str, stats: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"job {job_id} failed: {error_code}: {error_message}")
        self.job_id = job_id
        self.error_code = error_code
        self.error_message = error_message
        self.stats: dict[str, Any] = dict(stats or {})


class JobConflict(JobFailed):
    """:func:`run_job_sync` found an identical job (same dedupe key) already queued or running."""


# ------------------------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------------------------


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def dedupe_key(kind: str, project_id: int | None, params: Mapping[str, Any] | None = None) -> str:
    """sha256 hex of the canonical JSON ``[kind, project_id, params]`` (``build_deck``: ``[kind,
    project_id]``) - the key :func:`submit_job` deduplicates active jobs on (SPEC §16)."""
    payload: list[Any] = (
        [kind, project_id] if kind == "build_deck" else [kind, project_id, dict(params or {})]
    )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _unique_key(kind: str, project_id: int | None, params: Mapping[str, Any]) -> str:
    payload = [kind, project_id, dict(params), {"nonce": uuid.uuid4().hex}]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """A JSON-serializable copy of ``value`` (non-finite floats -> None, unknown types -> str)."""
    if _depth > 50:
        return str(value)
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, _depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_safe(v, _depth + 1) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        text = value.astimezone(UTC).isoformat()
        return text[:-6] + "Z" if text.endswith("+00:00") else text
    if isinstance(value, date):
        return value.isoformat()
    tolist = getattr(value, "tolist", None)  # numpy scalars and arrays
    if callable(tolist):
        try:
            return _json_safe(tolist(), _depth + 1)
        except Exception:
            pass
    return str(value)


def _clamp_fraction(fraction: float | None) -> float | None:
    if fraction is None:
        return None
    value = float(fraction)
    if not math.isfinite(value):
        return None
    return min(1.0, max(0.0, value))


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid exists (signal 0 probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


# ------------------------------------------------------------------------------------------------
# JobContext
# ------------------------------------------------------------------------------------------------


class JobContext:
    """What a job function receives: ``fn(ctx, **job.params_json)``.

    * ``job_id``/``project_id``/``owner_id``/``params`` of the running job (``project_id`` and
      ``owner_id`` are never in params; ``owner_id`` is the requesting user, ``None`` for system jobs);
    * :meth:`session` - a short ``radar.db.session_scope`` (write by default);
    * :meth:`progress` - progress fraction (0..1 or ``None`` = unknown) and an optional message
      (``None`` keeps the current one), written through the background writer;
    * :meth:`log_stat` - a stat merged into ``jobs.stats_json`` (returned stats win on equal keys);
    * :meth:`child` - a context for an inline sub-step of a composite job: same ``job_id``, same stats
      (its ``log_stat`` lands in this job's stats) and progress row, ``project_id`` inherited unless
      given, empty ``params``.

    A context built directly (``JobContext()``, e.g. in unit tests of a job function) has no job row:
    progress is only kept in :attr:`last_progress`.
    """

    def __init__(
        self,
        job_id: int | None = None,
        project_id: int | None = None,
        params: dict[str, Any] | None = None,
        owner_id: int | None = None,
    ) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.owner_id = owner_id
        self.params: dict[str, Any] = dict(params or {})
        self.last_progress: tuple[float | None, str | None] | None = None
        self._stats: dict[str, Any] = {}
        self._stats_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"JobContext(job_id={self.job_id!r}, project_id={self.project_id!r})"

    @property
    def stats(self) -> dict[str, Any]:
        """A copy of the stats logged so far (shared with children)."""
        with self._stats_lock:
            return dict(self._stats)

    def session(self, write: bool = True) -> AbstractContextManager[Session]:
        """A short session (``radar.db.session_scope``): commits on success, rolls back on error."""
        return db.session_scope(write=write)

    def progress(self, fraction: float | None, message: str | None = None) -> None:
        """Report progress through the background writer (applied only while the job is running)."""
        value = _clamp_fraction(fraction)
        text = None if message is None else _truncate(str(message))
        self.last_progress = (value, text)
        if self.job_id is None:
            return
        db.enqueue_write(_progress_writer(self.job_id, value, text))

    def log_stat(self, key: str, value: object) -> None:
        """Record a stat (merged into ``jobs.stats_json``; JSON-safe copies are stored)."""
        with self._stats_lock:
            self._stats[str(key)] = value

    def child(self, project_id: int | None = None) -> JobContext:
        """Context for an inline sub-step of a composite job (same job id and stats)."""
        sub = JobContext(
            self.job_id, self.project_id if project_id is None else project_id, owner_id=self.owner_id
        )
        sub._stats = self._stats
        sub._stats_lock = self._stats_lock
        return sub


def _progress_writer(job_id: int, fraction: float | None, message: str | None) -> Callable[[Session], None]:
    values: dict[str, Any] = {"progress": fraction}
    if message is not None:
        values["message"] = message

    def apply(session: Session) -> None:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    return apply


def _retry_writer(job_id: int, retries: int) -> Callable[[Session], None]:
    message = f"Retrying after a temporary error (retry {retries} of {len(RETRY_DELAYS)})"

    def apply(session: Session) -> None:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(retry_count=retries, message=message)
            .execution_options(synchronize_session=False)
        )

    return apply


# ------------------------------------------------------------------------------------------------
# In-flight tracking, executor, lifecycle
# ------------------------------------------------------------------------------------------------

# References this process holds on job ids, so recover_interrupted() never fails a job that is alive
# here: "pending" = inserted in a session that has not committed yet; "running" = dispatched (queued in
# the executor, running on a worker, inline, or in run_job_sync). wait_idle() waits for "running" only.
_state = threading.Condition()  # guards _executor and both tables; notified when a dispatched job ends
_executor: ThreadPoolExecutor | None = None
_pending: dict[int, int] = {}
_running: dict[int, int] = {}

_sleep: Callable[[float], None] = time.sleep


def set_retry_sleep(fn: Callable[[float], None] | None) -> None:
    """Install the sleep used between retries (tests); ``None`` restores ``time.sleep``."""
    global _sleep
    _sleep = fn if fn is not None else time.sleep


def _ref(table: dict[int, int], job_id: int) -> None:
    table[job_id] = table.get(job_id, 0) + 1


def _unref(table: dict[int, int], job_id: int) -> None:
    remaining = table.get(job_id, 0) - 1
    if remaining > 0:
        table[job_id] = remaining
    else:
        table.pop(job_id, None)


def _track_pending(job_id: int) -> None:
    with _state:
        _ref(_pending, job_id)


def _drop_pending(job_id: int) -> None:
    with _state:
        _unref(_pending, job_id)


def _promote(job_id: int) -> None:
    """pending -> running, atomically (a committed job is never untracked on its way to a worker)."""
    with _state:
        _unref(_pending, job_id)
        _ref(_running, job_id)


def _track_running(job_id: int) -> None:
    with _state:
        _ref(_running, job_id)


def _release(job_id: int) -> None:
    with _state:
        _unref(_running, job_id)
        _state.notify_all()


def _live_here() -> set[int]:
    with _state:
        return set(_pending) | set(_running)


def _new_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="radar-job")


def start() -> None:
    """Create the executor (``ThreadPoolExecutor(max_workers=2)``) if it is not running."""
    global _executor
    with _state:
        if _executor is None:
            _executor = _new_executor()


def shutdown() -> None:
    """``executor.shutdown(wait=False, cancel_futures=True)``.

    Queued jobs whose futures are cancelled stay ``queued`` in the database and are marked
    ``interrupted`` by :func:`recover_interrupted` at the next start. A later dispatch creates a new
    executor.
    """
    global _executor
    with _state:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def wait_idle(timeout: float | None = None) -> bool:
    """Block until no job dispatched by this process is queued or running here.

    Returns ``False`` when ``timeout`` seconds pass first. Jobs still pending in an uncommitted
    session are not waited for. Never call it from inside a job.
    """
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    with _state:
        while _running:
            if deadline is None:
                _state.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _state.wait(remaining)
    return True


def _submit_running(job_id: int) -> None:
    """Hand a committed job (already counted as running) to the executor; the future releases it."""
    global _executor
    try:
        with _state:
            if _executor is None:
                _executor = _new_executor()
            executor = _executor
        future = executor.submit(_execute, job_id)
    except Exception:  # shut down concurrently, or interpreter exit
        _release(job_id)
        log.warning("job %s stays queued: the job runner is not accepting work", job_id)
        return
    future.add_done_callback(lambda _future: _release(job_id))


def _thread_holds_write() -> bool:
    """True when the dispatching thread still holds a write transaction (inline would self-deadlock)."""
    return db.holding_write_transaction()


def _dispatch_committed(job_id: int) -> None:
    """Run (inline) or schedule a job whose row just committed; takes over its pending reference."""
    _promote(job_id)
    try:
        inline = get_settings().jobs_inline
        holding = _thread_holds_write()
    except Exception:
        inline, holding = False, False
    if inline and not holding:
        try:
            _execute(job_id)
        finally:
            _release(job_id)
        return
    if inline:
        log.warning("job %s: dispatching thread holds a write transaction; using the executor", job_id)
    _submit_running(job_id)


# ------------------------------------------------------------------------------------------------
# Dispatch after commit (session hooks)
# ------------------------------------------------------------------------------------------------


def _defer_dispatch(session: Session, job_id: int) -> None:
    pending: list[int] = session.info.setdefault(_PENDING_KEY, [])
    if job_id in pending:
        return
    pending.append(job_id)
    _track_pending(job_id)


def _on_after_commit(session: Session) -> None:
    # Fires for SAVEPOINT releases too: only the outermost commit counts.
    if session.info.get(_PENDING_KEY) and not session.in_nested_transaction():
        session.info[_COMMITTED_KEY] = True


def _on_after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is not None:
        return
    pending: list[int] | None = session.info.pop(_PENDING_KEY, None)
    committed = bool(session.info.pop(_COMMITTED_KEY, False))
    if not pending:
        return
    remaining = list(pending)
    try:
        while remaining:
            job_id = remaining.pop(0)
            if not committed:
                _drop_pending(job_id)  # rolled back or closed: the row does not exist
                continue
            try:
                _dispatch_committed(job_id)
            except Exception:
                log.exception("job %s could not be dispatched; it stays queued", job_id)
    finally:
        for job_id in remaining:  # only after KeyboardInterrupt/SystemExit in an inline job
            _drop_pending(job_id)  # left queued: recover_interrupted() may now fail it


def _install_session_hooks() -> None:
    hooks: tuple[tuple[str, Callable[..., None]], ...] = (
        ("after_commit", _on_after_commit),
        ("after_transaction_end", _on_after_transaction_end),
    )
    for name, fn in hooks:
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)


_install_session_hooks()


# ------------------------------------------------------------------------------------------------
# Submitting
# ------------------------------------------------------------------------------------------------


def _prepare(kind: str, project_id: int | None, params: Mapping[str, Any] | None) -> dict[str, Any]:
    if not registry.is_known(kind):
        raise registry.UnknownJobKind(kind)
    if project_id is not None and (isinstance(project_id, bool) or not isinstance(project_id, int)):
        raise TypeError("project_id must be an int or None")
    clean = dict(params or {})
    if any(not isinstance(k, str) for k in clean):
        raise TypeError("job params keys must be strings")
    if "project_id" in clean:
        raise ValueError("project_id is a job field: pass submit_job(..., project_id=...), not params")
    try:
        normalized = json.loads(_canonical_json(clean))
    except (TypeError, ValueError) as exc:
        raise ValueError("job params must be plain JSON values") from exc
    return cast(dict[str, Any], normalized)


def _find_active(session: Session, key: str) -> Job | None:
    """The active (queued/running) job with this dedupe key, if any."""
    return session.scalars(
        select(Job).where(Job.dedupe_key == key, Job.status.in_(ACTIVE_STATUSES)).order_by(Job.id).limit(1)
    ).first()


def _is_stale(job: Job) -> bool:
    """An active job owned by ANOTHER process that no longer exists."""
    pid = job.worker_pid
    return pid is not None and pid != os.getpid() and not _pid_alive(pid)


def _mark_interrupted(job: Job) -> None:
    job.status = "failed"
    job.error_code = "interrupted"
    job.error_message = INTERRUPTED_MESSAGE
    job.finished_at = utcnow()


def _insert_job(
    session: Session,
    kind: str,
    project_id: int | None,
    params: dict[str, Any],
    key: str,
    *,
    dedupe: bool,
    owner_id: int | None = None,
) -> tuple[int, bool]:
    """Insert a queued job (or return the active duplicate). Returns ``(job_id, created)``."""
    for _attempt in range(_INSERT_ATTEMPTS):
        if dedupe:
            existing = _find_active(session, key)
            if existing is not None:
                if not _is_stale(existing):
                    return existing.id, False
                log.warning("job %s (%s) belonged to a dead process; marked interrupted", existing.id, kind)
                _mark_interrupted(existing)
                session.flush()
        job = Job(
            kind=kind,
            status="queued",
            project_id=project_id,
            owner_id=owner_id,
            params_json=dict(params),
            dedupe_key=key,
            worker_pid=os.getpid(),
            stats_json={},
            retry_count=0,
        )
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            if not dedupe:
                raise
            log.info("a concurrent %s job won the dedupe race; returning it", kind)
            continue
        return job.id, True
    raise RuntimeError(f"could not queue a {kind} job (dedupe race not settled)")


def submit_job(
    kind: str,
    *,
    project_id: int | None = None,
    params: dict[str, Any] | None = None,
    dedupe: bool = True,
    session: Session | None = None,
    owner_id: int | None = None,
) -> int:
    """Queue a job (deduplicated against active jobs) and return its id (SPEC §15).

    ``owner_id`` is the requesting user (``None`` = a system job); it is not part of the dedupe key, so
    jobs that must be per user carry the user in ``params`` or their project.

    With ``session`` the row is added to the caller's transaction and dispatched after it commits;
    otherwise a short own write session is used (strict mode: never while holding a write
    transaction). Raises ``KeyError`` (:class:`~radar.jobs.registry.UnknownJobKind`) for unknown
    kinds and ``ValueError``/``TypeError`` for params that are not plain JSON or contain
    ``project_id``.
    """
    clean = _prepare(kind, project_id, params)
    key = dedupe_key(kind, project_id, clean) if dedupe else _unique_key(kind, project_id, clean)
    if session is not None:
        job_id, created = _insert_job(session, kind, project_id, clean, key, dedupe=dedupe, owner_id=owner_id)
        if created:
            _defer_dispatch(session, job_id)
        return job_id
    db.check_not_holding_write("submit_job")
    with db.session_scope(write=True) as own:
        job_id, created = _insert_job(own, kind, project_id, clean, key, dedupe=dedupe, owner_id=owner_id)
        if created:
            _defer_dispatch(own, job_id)  # dispatched when this block commits
    return job_id


def active_job(session: Session, kind: str, project_id: int | None) -> Job | None:
    """The queued/running job of ``kind`` for ``project_id`` (``None`` = no project), newest first."""
    stmt = select(Job).where(Job.kind == kind, Job.status.in_(ACTIVE_STATUSES))
    if project_id is None:
        stmt = stmt.where(Job.project_id.is_(None))
    else:
        stmt = stmt.where(Job.project_id == project_id)
    return session.scalars(stmt.order_by(Job.id.desc()).limit(1)).first()


# ------------------------------------------------------------------------------------------------
# Executing
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Claimed:
    job_id: int
    kind: str
    project_id: int | None
    params: dict[str, Any]
    owner_id: int | None = None


@dataclass
class _Outcome:
    status: str
    stats: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class _Attempts:
    retries: int = 0
    ctx: JobContext | None = None


def _claim(job_id: int) -> _Claimed | None:
    """Atomically move a queued job to running; ``None`` when it is not queued (any more)."""
    with db.session_scope(write=True) as session:
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="running", worker_pid=os.getpid(), started_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            return None
        job = session.get(Job, job_id)
        if job is None:  # pragma: no cover - just updated
            return None
        return _Claimed(job.id, job.kind, job.project_id, dict(job.params_json or {}), job.owner_id)


def _message(exc: BaseException, default: str) -> str:
    text = str(exc).strip()
    return _truncate(text) if text else default


def _classify(exc: BaseException, job_id: int) -> tuple[str, str]:
    """``(error_code, error_message)`` for a job failure (SPEC §16)."""
    from radar.net.http import BudgetExceeded, EgressBlocked, SourceUnavailable

    if isinstance(exc, SourceUnavailable):
        return "source_unavailable", _message(exc, "A data source is unavailable")
    if isinstance(exc, BudgetExceeded):
        return "budget_exceeded", _message(exc, "A data source's daily budget is used up")
    if isinstance(exc, EgressBlocked):
        return "egress_blocked", _message(exc, "Outbound network access is disabled")
    if isinstance(exc, NotImplementedError):
        return "not_implemented", NOT_IMPLEMENTED_MESSAGE
    if isinstance(exc, ApiError):
        return exc.code, _truncate(exc.message) or "The job failed"
    if isinstance(exc, RetryableError):
        cause = exc.__cause__
        if cause is not None and not isinstance(cause, RetryableError):
            code, message = _classify(cause, job_id)
            if code != "internal_error":
                return code, message
        return "retries_exhausted", _message(exc, RETRIES_EXHAUSTED_MESSAGE)
    return "internal_error", f"internal error (job {job_id})"


def _returned_stats(returned: object) -> dict[str, Any]:
    if returned is None:
        return {}
    if isinstance(returned, Mapping):
        return {str(k): v for k, v in returned.items()}
    return {"result": returned}


def _failure(job: _Claimed, exc: Exception, ctx: JobContext, retries: int) -> _Outcome:
    code, message = _classify(exc, job.job_id)
    if code == "internal_error":
        log.error("job %s (%s) failed with an unexpected error", job.job_id, job.kind, exc_info=exc)
    else:
        log.warning("job %s (%s) failed: %s", job.job_id, job.kind, code)
    return _Outcome("failed", ctx.stats, retries, code, message)


def _attempt_loop(job: _Claimed, state: _Attempts) -> _Outcome:
    while True:
        ctx = JobContext(job.job_id, job.project_id, job.params, owner_id=job.owner_id)
        state.ctx = ctx
        try:
            fn = registry.resolve(job.kind)
            returned = fn(ctx, **job.params)
        except RetryableError as exc:
            if state.retries < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[state.retries]
                state.retries += 1
                log.warning(
                    "job %s (%s): retryable error, retry %d of %d in %.0f s",
                    job.job_id,
                    job.kind,
                    state.retries,
                    len(RETRY_DELAYS),
                    delay,
                )
                db.enqueue_write(_retry_writer(job.job_id, state.retries))
                _sleep(delay)
                continue
            return _failure(job, exc, ctx, state.retries)
        except Exception as exc:
            return _failure(job, exc, ctx, state.retries)
        return _Outcome("succeeded", {**ctx.stats, **_returned_stats(returned)}, state.retries)


def _record(job_id: int, outcome: _Outcome) -> None:
    """Write the final status (after flushing pending progress writes)."""
    values: dict[str, Any] = {
        "status": outcome.status,
        "stats_json": _json_safe(outcome.stats),
        "retry_count": outcome.retry_count,
        "error_code": outcome.error_code,
        "error_message": outcome.error_message,
        "finished_at": utcnow(),
    }
    if outcome.status == "succeeded":
        values["progress"] = 1.0
    if _thread_holds_write():
        # The job function left a write transaction open on this thread: a direct write here would
        # wait on our own lock. Hand the outcome to the background writer (ordered after progress).
        log.error("job %s left a write transaction open; recording its outcome in the background", job_id)
        db.enqueue_write(_outcome_writer(job_id, values))
        return
    try:
        db.flush_writes()
    except Exception:
        log.warning("job %s: pending progress writes were not flushed before finishing", job_id)
    try:
        _update_job(job_id, values)
    except Exception:
        log.exception("job %s: could not store its outcome; retrying without stats", job_id)
        _update_job(job_id, {**values, "stats_json": {}})


def _outcome_writer(job_id: int, values: dict[str, Any]) -> Callable[[Session], None]:
    def apply(session: Session) -> None:
        session.execute(
            update(Job).where(Job.id == job_id).values(**values).execution_options(synchronize_session=False)
        )

    return apply


def _update_job(job_id: int, values: dict[str, Any]) -> None:
    with db.session_scope(write=True) as session:
        session.execute(
            update(Job).where(Job.id == job_id).values(**values).execution_options(synchronize_session=False)
        )


def _run_claimed(job: _Claimed) -> None:
    started = time.monotonic()
    state = _Attempts()
    outcome: _Outcome
    try:
        from radar.net.http import call_context

        with call_context(job.job_id, job.project_id):
            outcome = _attempt_loop(job, state)
    except Exception as exc:  # the runner itself failed (outside the job function)
        log.error("job %s (%s): runner error", job.job_id, job.kind, exc_info=exc)
        stats = state.ctx.stats if state.ctx is not None else {}
        outcome = _Outcome(
            "failed", stats, state.retries, "internal_error", f"internal error (job {job.job_id})"
        )
    except BaseException:  # KeyboardInterrupt / SystemExit: record, then propagate
        stats = state.ctx.stats if state.ctx is not None else {}
        try:
            _record(job.job_id, _Outcome("failed", stats, state.retries, "interrupted", INTERRUPTED_MESSAGE))
        except Exception:
            log.exception("job %s: could not record the interruption", job.job_id)
        raise
    _record(job.job_id, outcome)
    log.info(
        "job %s (%s) %s in %.1f s%s",
        job.job_id,
        job.kind,
        outcome.status,
        time.monotonic() - started,
        f" ({outcome.error_code})" if outcome.error_code else "",
    )


def _execute(job_id: int) -> None:
    """Claim and run one job; never raises ``Exception`` (failures are recorded and logged)."""
    try:
        claimed = _claim(job_id)
    except Exception:
        log.exception("job %s could not be started", job_id)
        return
    if claimed is None:
        log.debug("job %s is not queued any more; skipped", job_id)
        return
    try:
        _run_claimed(claimed)
    except Exception:
        # Recording the outcome failed twice: a job left "running" would block its dedupe key while
        # this process lives, so let the background writer mark it failed when it can.
        log.exception("job %s: its outcome could not be recorded; retrying in the background", job_id)
        db.enqueue_write(_abandoned_writer(job_id))


def _abandoned_writer(job_id: int) -> Callable[[Session], None]:
    def apply(session: Session) -> None:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(
                status="failed",
                error_code="internal_error",
                error_message=f"internal error (job {job_id})",
                finished_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )

    return apply


def run_job_sync(
    kind: str,
    *,
    project_id: int | None = None,
    params: dict[str, Any] | None = None,
    owner_id: int | None = None,
) -> dict[str, Any]:
    """Run a job function in the calling thread (CLI) and return its stats.

    A ``jobs`` row is created (visible in ``GET /api/jobs``; HTTP calls are attributed to it) and run
    exactly like an executor job. Raises :class:`JobFailed` (``error_code``/``error_message``) when the
    job fails and :class:`JobConflict` when an identical job is already queued or running in a live
    process. Must not be called while holding a write transaction (strict mode raises).
    """
    clean = _prepare(kind, project_id, params)
    db.check_not_holding_write("run_job_sync")
    key = dedupe_key(kind, project_id, clean)
    tracked: int | None = None
    try:
        with db.session_scope(write=True) as session:
            job_id, created = _insert_job(
                session, kind, project_id, clean, key, dedupe=True, owner_id=owner_id
            )
            if created:
                _track_running(job_id)  # before the commit: recovery must never see it untracked
                tracked = job_id
        if not created:
            raise JobConflict(
                job_id, "conflict", f"An identical {kind} job (job {job_id}) is already queued or running"
            )
        _execute(job_id)
    finally:
        if tracked is not None:
            _release(tracked)
    with db.session_scope() as session:
        row = session.get(Job, job_id)
        if row is None:  # pragma: no cover - the row was just created
            raise JobFailed(job_id, "internal_error", f"internal error (job {job_id})")
        status, code, message = row.status, row.error_code, row.error_message
        stats = dict(row.stats_json or {})
    if status != "succeeded":
        raise JobFailed(
            job_id, code or "internal_error", message or f"job {job_id} did not finish ({status})", stats
        )
    return stats


# ------------------------------------------------------------------------------------------------
# Startup recovery
# ------------------------------------------------------------------------------------------------


def recover_interrupted() -> int:
    """Mark queued/running jobs of dead workers failed ``interrupted``; returns the count.

    A job is dead when its ``worker_pid`` is null, is not a live process, or is this process while
    this process holds no reference to it (left over from an earlier process with the same pid).
    """
    db.check_not_holding_write("recover_interrupted")
    me = os.getpid()
    with db.session_scope(write=True) as session:
        rows = session.execute(select(Job.id, Job.worker_pid).where(Job.status.in_(ACTIVE_STATUSES))).all()
        live_here = _live_here()  # after the SELECT: every row it saw was tracked before its commit
        dead: list[int] = []
        for job_id, pid in rows:
            if pid == me:
                if job_id in live_here:
                    continue
            elif pid is not None and _pid_alive(pid):
                continue
            dead.append(job_id)
        if not dead:
            return 0
        result = session.execute(
            update(Job)
            .where(Job.id.in_(dead), Job.status.in_(ACTIVE_STATUSES))
            .values(
                status="failed",
                error_code="interrupted",
                error_message=INTERRUPTED_MESSAGE,
                finished_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        count = int(cast(CursorResult[Any], result).rowcount or 0)
    if count:
        log.warning("marked %d interrupted job(s) as failed", count)
    return count


__all__ = [
    "ACTIVE_STATUSES",
    "MAX_WORKERS",
    "RETRY_DELAYS",
    "JobConflict",
    "JobContext",
    "JobFailed",
    "active_job",
    "dedupe_key",
    "recover_interrupted",
    "run_job_sync",
    "set_retry_sleep",
    "shutdown",
    "start",
    "submit_job",
    "wait_idle",
]
