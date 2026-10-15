"""Job runner (SPEC §16): dedupe, dispatch after commit, inline/executor modes, error mapping, retries,
stats, JobContext, run_job_sync, recovery."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

from radar import models
from radar.db import flush_writes, new_session, session_scope
from radar.errors import RetryableError, UnresolvableIdentifier
from radar.jobs import registry, runner
from radar.net import http as net_http

# ------------------------------------------------------------------------------------------------
# Helpers and fixtures
# ------------------------------------------------------------------------------------------------

Register = Callable[[str, Callable[..., Any]], None]


@pytest.fixture
def kinds() -> Iterator[Register]:
    """Register fake job functions for this test only."""
    names: list[str] = []

    def _register(kind: str, fn: Callable[..., Any]) -> None:
        registry.register(kind, fn)
        names.append(kind)

    yield _register
    for name in names:
        registry.unregister(name)


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    """An event blocking fake jobs; always released at teardown so no worker hangs."""
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture
def executor_mode(set_env: Callable[..., None], gate: threading.Event) -> Iterator[None]:
    """RADAR_JOBS_INLINE=0 with a started executor; waits for every dispatched job at teardown."""
    set_env(RADAR_JOBS_INLINE="0")
    runner.start()
    yield
    gate.set()
    assert runner.wait_idle(timeout=15), "jobs still running at teardown"
    runner.shutdown()


@pytest.fixture
def sleeps() -> Iterator[list[float]]:
    recorded: list[float] = []
    runner.set_retry_sleep(recorded.append)
    yield recorded
    runner.set_retry_sleep(None)


def job_row(job_id: int) -> models.Job:
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        assert job is not None
        return job


def job_count(**where: Any) -> int:
    with session_scope() as s:
        stmt = select(func.count()).select_from(models.Job)
        for name, value in where.items():
            stmt = stmt.where(getattr(models.Job, name) == value)
        return int(s.scalar(stmt) or 0)


def insert_job(
    kind: str, *, key: str, status: str = "queued", worker_pid: int | None = None, **values: Any
) -> int:
    with session_scope(write=True) as s:
        job = models.Job(kind=kind, dedupe_key=key, status=status, worker_pid=worker_pid, **values)
        s.add(job)
        s.flush()
        return job.id


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def process_starts(monkeypatch: pytest.MonkeyPatch) -> dict[int, datetime]:
    """Patch ``runner._process_started_at``: ``{pid: start time}``; pids left out are unknown (``None``).

    Real start times are only readable on Linux (``/proc``), so every test about the alive/dead decision
    injects them and the real reader is checked separately.
    """
    starts: dict[int, datetime] = {}
    monkeypatch.setattr(runner, "_process_started_at", lambda pid: starts.get(pid))
    return starts


def echo(ctx: runner.JobContext, **params: Any) -> dict[str, Any]:
    return {"params": params}


# ------------------------------------------------------------------------------------------------
# Dedupe
# ------------------------------------------------------------------------------------------------


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_dedupe_key_is_sha256_of_canonical_json() -> None:
    a = runner.dedupe_key("enrich", None, {"limit": 5, "paper_ids": [1, 2]})
    b = runner.dedupe_key("enrich", None, {"paper_ids": [1, 2], "limit": 5})
    assert a == b == _sha(["enrich", None, {"limit": 5, "paper_ids": [1, 2]}])
    assert len(a) == 64
    assert runner.dedupe_key("enrich", 1, {"limit": 5, "paper_ids": [1, 2]}) != a
    assert runner.dedupe_key("enrich", None, {"limit": 6, "paper_ids": [1, 2]}) != a
    assert runner.dedupe_key("enrich", None, None) == _sha(["enrich", None, {}])
    # build_deck: [kind, project_id] only - every mode shares one key per project
    daily = runner.dedupe_key("build_deck", 3, {"mode": "daily"})
    assert daily == runner.dedupe_key("build_deck", 3, {"mode": "initial"}) == _sha(["build_deck", 3])
    assert daily != runner.dedupe_key("build_deck", 4, {"mode": "daily"})


def test_same_key_returns_the_active_job(executor_mode: None, kinds: Register, gate: threading.Event) -> None:
    calls: list[dict[str, Any]] = []

    def blocking(ctx: runner.JobContext, **params: Any) -> dict[str, Any]:
        calls.append(params)
        assert gate.wait(10)
        return {"ok": True}

    kinds("test_block", blocking)
    first = runner.submit_job("test_block", params={"x": 1})
    again = runner.submit_job("test_block", params={"x": 1})
    other = runner.submit_job("test_block", params={"x": 2})
    assert first == again
    assert other != first
    assert job_count(kind="test_block") == 2
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert job_row(first).status == "succeeded" and job_row(other).status == "succeeded"
    assert sorted(c["x"] for c in calls) == [1, 2]


def test_build_deck_modes_share_the_key(
    executor_mode: None, kinds: Register, gate: threading.Event, make_project: Callable[..., int]
) -> None:
    kinds("build_deck", lambda ctx, mode="daily": {"mode": mode, "ok": gate.wait(10)})
    p1, p2 = make_project(), make_project()
    daily = runner.submit_job("build_deck", project_id=p1, params={"mode": "daily"})
    initial = runner.submit_job("build_deck", project_id=p1, params={"mode": "initial"})
    elsewhere = runner.submit_job("build_deck", project_id=p2, params={"mode": "initial"})
    assert initial == daily
    assert elsewhere != daily
    assert job_row(daily).params_json == {"mode": "daily"}
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert job_row(daily).stats_json["mode"] == "daily"


def test_finished_jobs_do_not_block(kinds: Register) -> None:
    kinds("test_echo", echo)
    first = runner.submit_job("test_echo", params={"v": 1})
    assert job_row(first).status == "succeeded"
    second = runner.submit_job("test_echo", params={"v": 1})
    assert second != first
    assert job_row(second).status == "succeeded"
    assert job_row(first).dedupe_key == job_row(second).dedupe_key
    failed_key = runner.dedupe_key("test_echo", None, {"v": 2})
    failed = insert_job("test_echo", key=failed_key, status="failed")
    assert runner.submit_job("test_echo", params={"v": 2}) != failed


def test_dedupe_false_always_creates_a_job(
    executor_mode: None, kinds: Register, gate: threading.Event
) -> None:
    kinds("test_block", lambda ctx, **p: {"ok": gate.wait(10)})
    a = runner.submit_job("test_block", params={"x": 1}, dedupe=False)
    b = runner.submit_job("test_block", params={"x": 1}, dedupe=False)
    assert a != b
    keys = {job_row(a).dedupe_key, job_row(b).dedupe_key}
    assert len(keys) == 2 and runner.dedupe_key("test_block", None, {"x": 1}) not in keys
    # a deduped submit does not attach to them (their keys are unique)
    c = runner.submit_job("test_block", params={"x": 1})
    assert c not in (a, b)


def test_concurrent_submits_yield_one_active_job(
    executor_mode: None, kinds: Register, gate: threading.Event
) -> None:
    kinds("test_block", lambda ctx, **p: {"ok": gate.wait(10)})
    barrier = threading.Barrier(4)
    results: list[int] = []
    errors: list[BaseException] = []

    def submit() -> None:
        try:
            barrier.wait(5)
            results.append(runner.submit_job("test_block", params={"race": True}))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert len(results) == 4 and len(set(results)) == 1
    with session_scope() as s:
        active = s.scalar(
            select(func.count())
            .select_from(models.Job)
            .where(models.Job.kind == "test_block", models.Job.status.in_(("queued", "running")))
        )
    assert active == 1
    assert job_count(kind="test_block") == 1


def test_lost_insert_race_returns_the_winner(monkeypatch: pytest.MonkeyPatch, kinds: Register) -> None:
    """The partial unique index rejects the second active row; the runner returns the winner's id."""
    calls: list[int] = []
    kinds("test_echo", lambda ctx, **p: calls.append(1) or {})
    key = runner.dedupe_key("test_echo", None, {"v": 1})
    winner = insert_job("test_echo", key=key, status="running", worker_pid=os.getpid(), params_json={"v": 1})
    real = runner._find_active
    lookups = {"n": 0}

    def miss_first(session: Any, k: str) -> models.Job | None:
        lookups["n"] += 1
        return None if lookups["n"] == 1 else real(session, k)

    monkeypatch.setattr(runner, "_find_active", miss_first)
    assert runner.submit_job("test_echo", params={"v": 1}) == winner
    assert lookups["n"] == 2

    # same race inside a caller's transaction: only the SAVEPOINT is rolled back
    lookups["n"] = 0
    with session_scope(write=True) as s:
        assert runner.submit_job("test_echo", params={"v": 1}, session=s) == winner
        s.add(models.Project(name="Test project race", research_question="Test question?"))
    assert job_count(kind="test_echo") == 1
    assert calls == []
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 1


def test_stale_active_job_of_a_dead_process_is_replaced(kinds: Register) -> None:
    kinds("test_echo", echo)
    key = runner.dedupe_key("test_echo", None, {})
    stale = insert_job("test_echo", key=key, status="running", worker_pid=dead_pid())
    fresh = runner.submit_job("test_echo")
    assert fresh != stale
    old = job_row(stale)
    assert (old.status, old.error_code) == ("failed", "interrupted")
    assert old.finished_at is not None
    assert job_row(fresh).status == "succeeded"
    # an active job of a live process (or of this one) is still returned
    live_key = runner.dedupe_key("test_echo", None, {"live": True})
    live = insert_job("test_echo", key=live_key, status="running", worker_pid=os.getppid())
    assert runner.submit_job("test_echo", params={"live": True}) == live
    unknown_key = runner.dedupe_key("test_echo", None, {"pid": None})
    unknown = insert_job("test_echo", key=unknown_key, status="queued", worker_pid=None)
    assert runner.submit_job("test_echo", params={"pid": None}) == unknown


# ------------------------------------------------------------------------------------------------
# Dispatch: after commit, inline, executor
# ------------------------------------------------------------------------------------------------


def test_session_dispatch_happens_only_after_commit(kinds: Register) -> None:
    calls: list[int | None] = []
    kinds("test_echo", lambda ctx, **p: calls.append(ctx.job_id) or {})
    with session_scope(write=True) as s:
        job_id = runner.submit_job("test_echo", session=s)
        assert calls == []
        row = s.get(models.Job, job_id)
        assert row is not None and row.status == "queued"
        assert row.worker_pid == os.getpid()
    assert calls == [job_id]
    assert job_row(job_id).status == "succeeded"


def test_session_rollback_never_dispatches(kinds: Register) -> None:
    calls: list[int | None] = []
    kinds("test_echo", lambda ctx, **p: calls.append(ctx.job_id) or {})
    with pytest.raises(RuntimeError, match="boom"):
        with session_scope(write=True) as s:
            runner.submit_job("test_echo", session=s)
            raise RuntimeError("boom")
    assert calls == []
    assert job_count(kind="test_echo") == 0
    assert runner.wait_idle(timeout=0)  # no in-flight reference leaked

    s = new_session(write=True)
    try:
        runner.submit_job("test_echo", session=s)
    finally:
        s.close()  # closed without commit
    assert calls == []
    assert job_count(kind="test_echo") == 0
    assert runner.wait_idle(timeout=0)


def test_pending_job_is_protected_but_not_awaited_until_commit(kinds: Register) -> None:
    kinds("test_echo", echo)
    s = new_session(write=True)
    try:
        job_id = runner.submit_job("test_echo", session=s)
        assert job_id in runner._live_here()  # recover_interrupted() would leave it alone
        assert runner.wait_idle(timeout=0)  # an uncommitted session never blocks wait_idle()
        s.commit()
    finally:
        s.close()
    assert job_id not in runner._live_here()
    assert job_row(job_id).status == "succeeded"

    s = new_session(write=True)
    try:
        runner.submit_job("test_echo", params={"n": 2}, session=s)
        s.rollback()
    finally:
        s.close()
    assert runner._live_here() == set()


def test_savepoint_release_does_not_dispatch(kinds: Register) -> None:
    calls: list[int | None] = []
    kinds("test_echo", lambda ctx, **p: calls.append(ctx.job_id) or {})
    with session_scope(write=True) as s:
        with s.begin_nested():
            job_id = runner.submit_job("test_echo", session=s)
        assert calls == []
        s.execute(text("SELECT 1"))
        assert calls == []
    assert calls == [job_id]


def test_multiple_jobs_in_one_transaction_dispatch_in_order(kinds: Register) -> None:
    calls: list[int | None] = []
    kinds("test_echo", lambda ctx, **p: calls.append(ctx.job_id) or {})
    with session_scope(write=True) as s:
        a = runner.submit_job("test_echo", params={"n": 1}, session=s)
        b = runner.submit_job("test_echo", params={"n": 2}, session=s)
        assert runner.submit_job("test_echo", params={"n": 1}, session=s) == a
    assert calls == [a, b]


def test_inline_mode_runs_in_the_calling_thread_after_commit(kinds: Register) -> None:
    seen: list[int] = []
    kinds("test_echo", lambda ctx, **p: seen.append(threading.get_ident()) or {"n": 1})
    job_id = runner.submit_job("test_echo")
    assert seen == [threading.get_ident()]
    job = job_row(job_id)
    assert job.status == "succeeded" and job.stats_json == {"n": 1}


def test_inline_mode_uses_the_executor_when_the_thread_holds_a_write_transaction(
    monkeypatch: pytest.MonkeyPatch, kinds: Register
) -> None:
    seen: list[str] = []
    kinds("test_echo", lambda ctx, **p: seen.append(threading.current_thread().name) or {})
    monkeypatch.setattr(runner, "_thread_holds_write", lambda: True)
    job_id = runner.submit_job("test_echo")
    assert runner.wait_idle(timeout=10)
    runner.shutdown()
    assert seen and seen[0].startswith("radar-job")
    assert job_row(job_id).status == "succeeded"


def test_executor_mode_runs_jobs_on_worker_threads(executor_mode: None, kinds: Register) -> None:
    seen: list[str] = []
    kinds("test_echo", lambda ctx, **p: seen.append(threading.current_thread().name) or {"ok": 1})
    job_id = runner.submit_job("test_echo")
    assert runner.wait_idle(timeout=10)
    assert seen and seen[0].startswith("radar-job")
    assert job_row(job_id).status == "succeeded"
    assert runner.MAX_WORKERS == 2
    assert runner._executor is not None and runner._executor._max_workers == 2


def test_executor_is_recreated_lazily_after_shutdown(executor_mode: None, kinds: Register) -> None:
    kinds("test_echo", echo)
    runner.shutdown()
    job_id = runner.submit_job("test_echo")
    assert runner.wait_idle(timeout=10)
    assert job_row(job_id).status == "succeeded"


def test_wait_idle_times_out_while_a_job_runs(
    executor_mode: None, kinds: Register, gate: threading.Event
) -> None:
    kinds("test_block", lambda ctx, **p: {"ok": gate.wait(10)})
    job_id = runner.submit_job("test_block")
    assert runner.wait_idle(timeout=0.05) is False
    gate.set()
    assert runner.wait_idle(timeout=10) is True
    assert job_row(job_id).status == "succeeded"


def test_shutdown_cancels_queued_jobs_and_recovery_marks_them(
    executor_mode: None, kinds: Register, gate: threading.Event
) -> None:
    started = threading.Semaphore(0)

    def blocking(ctx: runner.JobContext, **p: Any) -> dict[str, Any]:
        started.release()
        assert gate.wait(10)
        return {}

    kinds("test_block", blocking)
    running = [runner.submit_job("test_block", params={"n": n}) for n in (1, 2)]
    assert started.acquire(timeout=5) and started.acquire(timeout=5)  # both workers busy
    waiting = runner.submit_job("test_block", params={"n": 3})
    runner.shutdown()  # cancels the queued future; running jobs continue
    assert job_row(waiting).status == "queued"
    assert runner.recover_interrupted() == 1
    assert (job_row(waiting).status, job_row(waiting).error_code) == ("failed", "interrupted")
    assert all(job_row(j).status == "running" for j in running)
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert all(job_row(j).status == "succeeded" for j in running)


def test_submit_in_strict_mode_refuses_while_holding_a_write_transaction(kinds: Register) -> None:
    kinds("test_echo", echo)
    with session_scope(write=True) as s:
        s.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="holding write transaction"):
            runner.submit_job("test_echo")
    assert job_count(kind="test_echo") == 0


def test_submit_validates_kind_and_params(kinds: Register) -> None:
    with pytest.raises(KeyError):
        runner.submit_job("no_such_kind")
    kinds("test_echo", echo)
    with pytest.raises(ValueError, match="project_id"):
        runner.submit_job("test_echo", params={"project_id": 3})
    with pytest.raises(ValueError):
        runner.submit_job("test_echo", params={"x": float("nan")})
    with pytest.raises(ValueError):
        runner.submit_job("test_echo", params={"x": object()})
    with pytest.raises(TypeError):
        runner.submit_job("test_echo", project_id="3")  # type: ignore[arg-type]
    assert job_count() == 0


def test_params_are_passed_as_keyword_arguments_and_stored_as_json(kinds: Register) -> None:
    received: list[tuple[Any, ...]] = []

    def fn(ctx: runner.JobContext, paper_ids: list[int] | None = None, limit: int = 300) -> dict[str, Any]:
        received.append((ctx.project_id, ctx.params, paper_ids, limit))
        return {}

    kinds("test_kw", fn)
    job_id = runner.submit_job("test_kw", project_id=None, params={"paper_ids": (3, 1), "limit": 7})
    assert received == [(None, {"limit": 7, "paper_ids": [3, 1]}, [3, 1], 7)]
    assert job_row(job_id).params_json == {"limit": 7, "paper_ids": [3, 1]}


# ------------------------------------------------------------------------------------------------
# Execution: status fields, call context, errors, retries, stats
# ------------------------------------------------------------------------------------------------


def test_status_fields_and_call_context(
    monkeypatch: pytest.MonkeyPatch, kinds: Register, make_project: Callable[..., int]
) -> None:
    entered: list[tuple[int | None, int | None]] = []
    inside: list[bool] = []
    active = {"on": False}

    @contextmanager
    def fake_call_context(job_id: int | None, project_id: int | None) -> Iterator[None]:
        entered.append((job_id, project_id))
        active["on"] = True
        try:
            yield
        finally:
            active["on"] = False

    monkeypatch.setattr(net_http, "call_context", fake_call_context)
    kinds("test_echo", lambda ctx, **p: inside.append(active["on"]) or {})
    project_id = make_project()
    before = datetime.now(UTC)
    job_id = runner.submit_job("test_echo", project_id=project_id)
    job = job_row(job_id)
    assert entered == [(job_id, project_id)] and inside == [True]
    assert job.status == "succeeded" and job.worker_pid == os.getpid()
    assert job.started_at is not None and job.finished_at is not None
    assert before <= job.created_at <= job.started_at <= job.finished_at
    assert (job.error_code, job.error_message, job.retry_count, job.progress) == (None, None, 0, 1.0)


@pytest.mark.parametrize(
    ("make_exc", "code", "keeps_message"),
    [
        (lambda: net_http.SourceUnavailable("openalex", 503), "source_unavailable", True),
        (lambda: net_http.BudgetExceeded("openalex"), "budget_exceeded", True),
        (lambda: net_http.EgressBlocked("outbound HTTP is disabled (offline mode)"), "egress_blocked", True),
        (lambda: UnresolvableIdentifier(), "unresolvable_identifier", True),
    ],
)
def test_error_codes_of_radar_exceptions(
    kinds: Register, make_exc: Callable[[], Exception], code: str, keeps_message: bool
) -> None:
    exc = make_exc()

    def fail(ctx: runner.JobContext, **p: Any) -> None:
        ctx.log_stat("before_failure", 1)
        raise exc

    kinds("test_fail", fail)
    job = job_row(runner.submit_job("test_fail"))
    assert job.status == "failed" and job.error_code == code
    assert job.error_message == str(exc) and job.error_message
    assert job.stats_json == {"before_failure": 1}
    assert job.finished_at is not None and job.progress is None


def test_not_implemented_maps_to_not_implemented(kinds: Register) -> None:
    def stub(ctx: runner.JobContext) -> None:
        raise NotImplementedError("owned by rank")

    kinds("test_stub", stub)
    job = job_row(runner.submit_job("test_stub"))
    assert (job.status, job.error_code) == ("failed", "not_implemented")
    assert job.error_message == runner.NOT_IMPLEMENTED_MESSAGE


def test_unexpected_errors_are_internal_and_never_store_the_message(kinds: Register) -> None:
    def leaky(ctx: runner.JobContext) -> None:
        raise ValueError("zqcanary private title https://example.invalid/?api_key=zqsecret")

    kinds("test_leak", leaky)
    job_id = runner.submit_job("test_leak")
    job = job_row(job_id)
    assert (job.status, job.error_code) == ("failed", "internal_error")
    assert job.error_message == f"internal error (job {job_id})"
    assert "zq" not in json.dumps(job.stats_json)


def test_unknown_kind_at_run_time_is_an_internal_error(kinds: Register) -> None:
    kinds("test_gone", echo)
    with session_scope(write=True) as s:
        job_id = runner.submit_job("test_gone", session=s)
        registry.unregister("test_gone")  # disappears before the job runs
    job = job_row(job_id)
    assert (job.status, job.error_code) == ("failed", "internal_error")


def test_retryable_error_is_retried_with_backoff(kinds: Register, sleeps: list[float]) -> None:
    attempts: list[int] = []

    def flaky(ctx: runner.JobContext) -> dict[str, Any]:
        attempts.append(len(attempts))
        ctx.log_stat(f"attempt_{len(attempts)}", True)
        if len(attempts) < 3:
            raise RetryableError("temporarily unavailable")
        return {"done": True}

    kinds("test_flaky", flaky)
    job = job_row(runner.submit_job("test_flaky"))
    assert len(attempts) == 3
    assert sleeps == [2.0, 4.0]
    assert (job.status, job.retry_count) == ("succeeded", 2)
    assert job.stats_json == {"attempt_3": True, "done": True}  # stats of the final attempt only


def test_retries_are_bounded(kinds: Register, sleeps: list[float]) -> None:
    attempts: list[int] = []

    def always(ctx: runner.JobContext) -> None:
        attempts.append(1)
        raise RetryableError("still unavailable")

    kinds("test_always", always)
    job = job_row(runner.submit_job("test_always"))
    assert len(attempts) == 4
    assert sleeps == [2.0, 4.0, 8.0] == list(runner.RETRY_DELAYS)
    assert (job.status, job.retry_count, job.error_code) == ("failed", 3, "retries_exhausted")
    assert job.error_message == "still unavailable"


def test_exhausted_retry_reports_the_cause(kinds: Register, sleeps: list[float]) -> None:
    def always(ctx: runner.JobContext) -> None:
        try:
            raise net_http.SourceUnavailable("crossref", 503)
        except net_http.SourceUnavailable as exc:
            raise RetryableError("crossref is busy") from exc

    kinds("test_cause", always)
    job = job_row(runner.submit_job("test_cause"))
    assert (job.status, job.error_code, job.retry_count) == ("failed", "source_unavailable", 3)
    assert job.error_message == str(net_http.SourceUnavailable("crossref", 503))


def test_stats_merge_logged_and_returned(kinds: Register) -> None:
    def fn(ctx: runner.JobContext) -> dict[str, Any]:
        ctx.log_stat("a", 1)
        ctx.log_stat("b", 2)
        ctx.log_stat("nan", float("nan"))
        ctx.log_stat("ids", {3, 1, 2})
        ctx.log_stat("when", datetime(2026, 10, 12, 12, 0, tzinfo=UTC))
        return {"b": 3, "c": 4}

    kinds("test_stats", fn)
    job = job_row(runner.submit_job("test_stats"))
    assert job.stats_json == {
        "a": 1,
        "b": 3,
        "c": 4,
        "nan": None,
        "ids": [1, 2, 3],
        "when": "2026-10-12T12:00:00Z",
    }


def test_non_dict_results_are_wrapped(kinds: Register) -> None:
    kinds("test_int", lambda ctx: 42)
    kinds("test_none", lambda ctx: None)
    assert job_row(runner.submit_job("test_int")).stats_json == {"result": 42}
    assert job_row(runner.submit_job("test_none")).stats_json == {}


def test_outcome_of_a_job_that_leaks_a_write_transaction_is_still_recorded(kinds: Register) -> None:
    leaked: list[Any] = []

    def leaky(ctx: runner.JobContext) -> dict[str, Any]:
        s = new_session(write=True)
        s.execute(text("SELECT 1"))  # BEGIN IMMEDIATE, never committed by the job
        leaked.append(s)
        return {"ok": True}

    kinds("test_leaky_tx", leaky)
    job_id = runner.submit_job("test_leaky_tx")  # returns instead of waiting 30 s on its own lock
    assert job_row(job_id).status == "running"
    leaked[0].rollback()
    leaked[0].close()
    flush_writes()
    job = job_row(job_id)
    assert (job.status, job.stats_json, job.progress) == ("succeeded", {"ok": True}, 1.0)


def test_a_job_whose_outcome_cannot_be_written_does_not_stay_running(
    monkeypatch: pytest.MonkeyPatch, kinds: Register
) -> None:
    kinds("test_echo", echo)

    def broken_update(job_id: int, values: dict[str, Any]) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(runner, "_update_job", broken_update)
    job_id = runner.submit_job("test_echo")
    flush_writes()
    job = job_row(job_id)
    assert (job.status, job.error_code, job.error_message) == (
        "failed",
        "internal_error",
        f"internal error (job {job_id})",
    )
    assert job.finished_at is not None


def test_keyboard_interrupt_marks_the_job_interrupted(kinds: Register) -> None:
    def interrupted(ctx: runner.JobContext) -> None:
        raise KeyboardInterrupt

    kinds("test_kbd", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.run_job_sync("test_kbd")
    with session_scope() as s:
        job = s.scalars(select(models.Job).where(models.Job.kind == "test_kbd")).one()
        assert (job.status, job.error_code) == ("failed", "interrupted")
    assert runner.wait_idle(timeout=0)


# ------------------------------------------------------------------------------------------------
# JobContext
# ------------------------------------------------------------------------------------------------


def test_child_context_shares_job_id_stats_and_progress(
    kinds: Register, make_project: Callable[..., int]
) -> None:
    p1, p2 = make_project(), make_project()
    observed: dict[str, Any] = {}

    def composite(ctx: runner.JobContext, mode: str = "daily") -> dict[str, Any]:
        child = ctx.child()
        other = ctx.child(project_id=p2)
        observed.update(
            child=(child.job_id, child.project_id, child.params),
            other=(other.job_id, other.project_id),
            grandchild=other.child().project_id,
        )
        child.log_stat("from_child", 1)
        other.log_stat("from_other", 2)
        child.progress(0.25, "Sub-step running")
        flush_writes()
        row = job_row(ctx.job_id or 0)
        observed["progress"] = (row.progress, row.message)
        return {"mode": mode}

    kinds("test_composite", composite)
    job_id = runner.submit_job("test_composite", project_id=p1, params={"mode": "initial"})
    assert observed["child"] == (job_id, p1, {})
    assert observed["other"] == (job_id, p2)
    assert observed["grandchild"] == p2
    assert observed["progress"] == (0.25, "Sub-step running")
    assert job_row(job_id).stats_json == {"from_child": 1, "from_other": 2, "mode": "initial"}


def test_progress_is_visible_after_flush(kinds: Register) -> None:
    seen: list[tuple[str, float | None, str | None]] = []

    def fn(ctx: runner.JobContext) -> dict[str, Any]:
        for fraction, message in ((0.5, "Halfway there"), (None, None), (2.0, None), (-1, "Clamped")):
            ctx.progress(fraction, message)
            flush_writes()
            row = job_row(ctx.job_id or 0)
            seen.append((row.status, row.progress, row.message))
        return {}

    kinds("test_progress", fn)
    job_id = runner.submit_job("test_progress")
    assert seen == [
        ("running", 0.5, "Halfway there"),
        ("running", None, "Halfway there"),  # None fraction = unknown; None message keeps the old one
        ("running", 1.0, "Halfway there"),
        ("running", 0.0, "Clamped"),
    ]
    job = job_row(job_id)
    assert (job.status, job.progress, job.message) == ("succeeded", 1.0, "Clamped")


def test_progress_never_touches_a_finished_job() -> None:
    job_id = insert_job("enrich", key="done", status="succeeded", progress=1.0, message="Done")
    runner.JobContext(job_id).progress(0.1, "late write")
    flush_writes()
    job = job_row(job_id)
    assert (job.progress, job.message) == (1.0, "Done")


def test_context_without_a_job_row() -> None:
    ctx = runner.JobContext(project_id=3, params={"a": 1})
    ctx.progress(0.4, "local only")
    ctx.log_stat("k", "v")
    assert ctx.last_progress == (0.4, "local only")
    assert ctx.stats == {"k": "v"}
    child = ctx.child()
    assert (child.job_id, child.project_id, child.params) == (None, 3, {})
    with ctx.session() as s:
        s.add(models.Project(name="Test project ctx", research_question="Test question?"))
    with ctx.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 1
    with pytest.raises(RuntimeError):  # a read session (strict mode refuses writes)
        with ctx.session(write=False) as s:
            s.execute(text("DELETE FROM projects"))


# ------------------------------------------------------------------------------------------------
# run_job_sync, active_job, recover_interrupted
# ------------------------------------------------------------------------------------------------


def test_run_job_sync_returns_stats_and_records_the_job(
    kinds: Register, make_project: Callable[..., int]
) -> None:
    project_id = make_project()
    seen: list[tuple[int | None, int | None, str]] = []

    def fn(ctx: runner.JobContext, mode: str = "daily") -> dict[str, Any]:
        seen.append((ctx.job_id, ctx.project_id, mode))
        ctx.log_stat("logged", True)
        return {"n": 3}

    kinds("test_sync", fn)
    stats = runner.run_job_sync("test_sync", project_id=project_id, params={"mode": "more"})
    assert stats == {"logged": True, "n": 3}
    with session_scope() as s:
        job = s.scalars(select(models.Job).where(models.Job.kind == "test_sync")).one()
    assert seen == [(job.id, project_id, "more")]
    assert (job.status, job.project_id, job.params_json) == ("succeeded", project_id, {"mode": "more"})
    assert seen[0][0] is not None


def test_run_job_sync_raises_job_failed(kinds: Register) -> None:
    def fn(ctx: runner.JobContext) -> None:
        ctx.log_stat("partial", 1)
        raise net_http.BudgetExceeded("openalex")

    kinds("test_sync_fail", fn)
    with pytest.raises(runner.JobFailed) as info:
        runner.run_job_sync("test_sync_fail")
    assert info.value.error_code == "budget_exceeded"
    assert info.value.error_message == str(net_http.BudgetExceeded("openalex"))
    assert info.value.stats == {"partial": 1}
    assert job_row(info.value.job_id).status == "failed"


def test_run_job_sync_refuses_an_identical_active_job(kinds: Register) -> None:
    calls: list[int] = []
    kinds("test_sync", lambda ctx: calls.append(1) or {})
    key = runner.dedupe_key("test_sync", None, {})
    other = insert_job("test_sync", key=key, status="running", worker_pid=os.getppid())
    with pytest.raises(runner.JobConflict) as info:
        runner.run_job_sync("test_sync")
    assert info.value.job_id == other and info.value.error_code == "conflict"
    assert calls == []


def test_run_job_sync_refuses_while_holding_a_write_transaction(kinds: Register) -> None:
    kinds("test_sync", echo)
    with session_scope(write=True) as s:
        s.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="holding write transaction"):
            runner.run_job_sync("test_sync")


def test_active_job(make_project: Callable[..., int]) -> None:
    p1, p2 = make_project(), make_project()
    insert_job("build_deck", key="a", status="succeeded", project_id=p1)
    queued = insert_job("build_deck", key="b", status="queued", project_id=p1)
    insert_job("build_deck", key="c", status="failed", project_id=p2)
    expand = insert_job("expand", key="d", status="running", project_id=None)
    with session_scope() as s:
        found = runner.active_job(s, "build_deck", p1)
        assert found is not None and found.id == queued
        assert runner.active_job(s, "build_deck", p2) is None
        found_expand = runner.active_job(s, "expand", None)
        assert found_expand is not None and found_expand.id == expand
        assert runner.active_job(s, "expand", p1) is None
        assert runner.active_job(s, "daily", None) is None


def test_recover_interrupted_marks_jobs_of_dead_workers(
    executor_mode: None, kinds: Register, gate: threading.Event, process_starts: dict[int, datetime]
) -> None:
    kinds("test_block", lambda ctx, **p: {"ok": gate.wait(10)})
    process_starts[os.getppid()] = datetime(2020, 1, 1, tzinfo=UTC)  # long before any job below
    live_here = runner.submit_job("test_block")  # running in this process: untouched
    dead = insert_job("enrich", key="k1", status="running", worker_pid=dead_pid())
    no_pid = insert_job("enrich", key="k2", status="queued", worker_pid=None)
    ours_but_gone = insert_job("enrich", key="k3", status="running", worker_pid=os.getpid())
    other_live = insert_job("enrich", key="k4", status="running", worker_pid=os.getppid())
    finished = insert_job("enrich", key="k5", status="succeeded", worker_pid=dead_pid())

    assert runner.recover_interrupted() == 3
    for job_id in (dead, no_pid, ours_but_gone):
        job = job_row(job_id)
        assert (job.status, job.error_code, job.error_message) == (
            "failed",
            "interrupted",
            runner.INTERRUPTED_MESSAGE,
        )
        assert job.finished_at is not None
    assert job_row(other_live).status == "running"
    assert job_row(finished).status == "succeeded"
    assert job_row(live_here).status in ("queued", "running")
    assert runner.recover_interrupted() == 0
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert job_row(live_here).status == "succeeded"


def test_recover_interrupted_reclaims_a_job_whose_pid_was_reused(
    process_starts: dict[int, datetime],
) -> None:
    """Production 2026-10-14: after a machine restart an unrelated process held the worker pid."""
    pid = os.getppid()  # a live pid; only its start time decides
    started = datetime(2026, 10, 14, 23, 54, 25, tzinfo=UTC)
    job_id = insert_job("build_deck", key="k1", status="running", worker_pid=pid, started_at=started)
    process_starts[pid] = started + timedelta(minutes=8)  # the process booted after the job started

    assert runner._pid_alive(pid)  # the old "pid exists" test would have kept this job running
    assert runner.recover_interrupted() == 1
    assert (job_row(job_id).status, job_row(job_id).error_code) == ("failed", "interrupted")

    # A worker that started before the job it claimed stays alive.
    other = insert_job("build_deck", key="k2", status="running", worker_pid=pid, started_at=started)
    process_starts[pid] = started - timedelta(minutes=1)
    assert runner.recover_interrupted() == 0
    assert job_row(other).status == "running"


def test_recover_interrupted_reclaims_a_pid_without_a_readable_start_time(
    executor_mode: None, kinds: Register, gate: threading.Event, process_starts: dict[int, datetime]
) -> None:
    """No start time (no ``/proc``) = no proof of life: dead, unless this process still holds the job."""
    kinds("test_block", lambda ctx, **p: {"ok": gate.wait(10)})
    live_here = runner.submit_job("test_block")
    unknown = insert_job(
        "enrich", key="k1", status="running", worker_pid=os.getppid(), started_at=models.utcnow()
    )
    assert process_starts == {}  # every start time is unknown
    assert runner.recover_interrupted() == 1
    assert job_row(unknown).error_code == "interrupted"
    assert job_row(live_here).status in ("queued", "running")
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert job_row(live_here).status == "succeeded"


def test_submit_replaces_a_job_whose_pid_was_reused(
    kinds: Register, process_starts: dict[int, datetime]
) -> None:
    kinds("test_echo", echo)
    pid = os.getppid()
    started = models.utcnow() - timedelta(hours=2)
    reused_key = runner.dedupe_key("test_echo", None, {"v": 1})
    reused = insert_job("test_echo", key=reused_key, status="running", worker_pid=pid, started_at=started)
    process_starts[pid] = started + timedelta(hours=1)
    fresh = runner.submit_job("test_echo", params={"v": 1})
    assert fresh != reused and job_row(reused).error_code == "interrupted"
    assert job_row(fresh).status == "succeeded"

    # Unknown start time on the submit path: the live job is returned, never run a second time.
    unknown_key = runner.dedupe_key("test_echo", None, {"v": 2})
    unknown = insert_job(
        "test_echo", key=unknown_key, status="running", worker_pid=pid, started_at=models.utcnow()
    )
    process_starts.clear()
    assert runner.submit_job("test_echo", params={"v": 2}) == unknown
    assert job_row(unknown).status == "running"


def test_starttime_ticks_reads_field_22_of_a_proc_stat_line() -> None:
    """Real Linux layout: comm is parenthesized and may hold spaces and ')'."""
    line = (
        b"652 (radar serve (1)) S 1 652 652 0 -1 4194560 2951 0 0 0 83 21 0 0 20 0 9 0 8641523 "
        b"1234567 4096 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0\n"
    )
    assert runner._starttime_ticks(line) == 8641523.0
    assert runner._starttime_ticks(b"652 (radar) S 1 652\n") is None  # truncated
    assert runner._starttime_ticks(b"not a stat line") is None


def test_process_started_at_reads_proc_or_says_it_cannot() -> None:
    """The real reader: a time on Linux, ``None`` where there is no ``/proc`` (macOS development)."""
    started = runner._process_started_at(os.getpid())
    if Path("/proc/self/stat").exists():
        assert started is not None
        assert timedelta(0) <= models.utcnow() - started < timedelta(days=30)
        assert runner._worker_is_live(os.getpid(), models.utcnow())
    else:
        assert started is None
        assert not runner._worker_is_live(os.getpid(), models.utcnow())
    assert runner._process_started_at(-1) is None
    assert not runner._pid_reused(os.getpid(), None)  # no reference time: never "reused"


def test_lifecycle_hooks_are_idempotent() -> None:
    runner.start()
    runner.start()
    runner.shutdown()
    runner.shutdown()
    assert runner.recover_interrupted() == 0


def test_interrupt_during_dispatch_releases_the_other_pending_jobs(kinds: Register) -> None:
    def interrupted(ctx: runner.JobContext) -> None:
        raise KeyboardInterrupt

    kinds("test_kbd", interrupted)
    kinds("test_echo", echo)
    with pytest.raises(KeyboardInterrupt):
        with session_scope(write=True) as s:
            first = runner.submit_job("test_kbd", session=s)
            second = runner.submit_job("test_echo", session=s)
    assert job_row(first).error_code == "interrupted"
    assert job_row(second).status == "queued"  # never dispatched ...
    assert runner._live_here() == set()  # ... and no longer protected
    assert runner.recover_interrupted() == 1
    assert job_row(second).error_code == "interrupted"
