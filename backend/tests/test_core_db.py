"""Database layer: UTCDateTime, JSON defaults, strict transactions, background writer, partial unique
index, init_db, pragmas and SQLite URL handling (SPEC §2.1, §4)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from radar import db, models
from radar.config import get_settings
from radar.db import (
    StrictTransactionError,
    check_not_holding_write,
    enqueue_write,
    flush_writes,
    holding_write_transaction,
    session_scope,
)
from radar.store.app_settings import get_setting, set_setting

# ------------------------------------------------------------------------------------------------
# UTCDateTime and JSON columns
# ------------------------------------------------------------------------------------------------


def test_utc_datetime_round_trip(make_project: Any) -> None:
    pid = make_project()
    eastern = timezone(timedelta(hours=-4))
    moment = datetime(2026, 10, 12, 8, 30, 15, 123456, tzinfo=eastern)
    with session_scope(write=True) as s:
        job = models.Job(kind="enrich", dedupe_key="k-utc", project_id=pid, started_at=moment)
        s.add(job)
        s.flush()
        job_id = job.id
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        assert job is not None
        assert job.started_at is not None and job.created_at is not None
        assert job.started_at.tzinfo is not None and job.started_at.utcoffset() == timedelta(0)
        assert job.started_at == moment
        assert job.started_at.hour == 12  # stored as UTC
        assert job.created_at.utcoffset() == timedelta(0)
        # comparisons with aware values work in SQL
        found = s.scalar(select(models.Job.id).where(models.Job.started_at >= moment - timedelta(seconds=1)))
        assert found == job_id


def test_utc_datetime_rejects_naive() -> None:
    with pytest.raises(StatementError) as info:
        with session_scope(write=True) as s:
            s.add(models.Job(kind="enrich", dedupe_key="k-naive", started_at=datetime(2026, 10, 12, 8, 0)))
            s.flush()
    assert isinstance(info.value.orig, ValueError)
    assert not holding_write_transaction()


def test_json_defaults_are_independent(make_paper: Any) -> None:
    with session_scope(write=True) as s:
        a = models.Project(name="Test project A", research_question="q")
        b = models.Project(name="Test project B", research_question="q")
        s.add_all([a, b])
        s.flush()
        assert a.settings_json == {} and b.settings_json == {}
        assert a.settings_json is not b.settings_json
        a.settings_json = {**a.settings_json, "slate_size": 10}
        s.flush()
        ids = (a.id, b.id)
    with session_scope() as s:
        assert s.get(models.Project, ids[0]).settings_json == {"slate_size": 10}  # type: ignore[union-attr]
        assert s.get(models.Project, ids[1]).settings_json == {}  # type: ignore[union-attr]
    p1, p2 = make_paper(categories=[]), make_paper(categories=[])
    with session_scope() as s:
        x, y = s.get(models.Paper, p1), s.get(models.Paper, p2)
        assert x is not None and y is not None
        assert x.field_provenance_json == {} and x.field_provenance_json is not y.field_provenance_json


def test_json_none_is_sql_null() -> None:
    with session_scope(write=True) as s:
        s.add(
            models.GraphEdge(
                src_type="work", src_id=1, relation="CITES", dst_type="work", dst_id=2, provenance="s2"
            )
        )
    with session_scope() as s:
        n = s.scalar(
            select(func.count()).select_from(models.GraphEdge).where(models.GraphEdge.evidence_json.is_(None))
        )
        assert n == 1
        raw = s.execute(text("SELECT evidence_json IS NULL FROM graph_edges")).scalar()
        assert raw == 1


# ------------------------------------------------------------------------------------------------
# Strict mode and the write-holder flag
# ------------------------------------------------------------------------------------------------


def test_strict_mode_is_on_in_tests() -> None:
    assert get_settings().strict_tx is True


def test_write_in_read_session_raises() -> None:
    with pytest.raises(StrictTransactionError):
        with session_scope() as s:
            s.add(models.Project(name="Test project", research_question="q"))
            s.flush()
    for statement in (
        "INSERT INTO users (id, auth_subject, created_at, preferences_json) VALUES (5, 'x', '2026-01-01', '{}')",
        "UPDATE users SET auth_subject = 'y'",
        "  delete from users",
        "REPLACE INTO users (id, auth_subject, created_at, preferences_json) VALUES (5, 'x', '2026-01-01', '{}')",
    ):
        with pytest.raises(RuntimeError, match="read session"):
            with session_scope() as s:
                s.execute(text(statement))
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.User)) == 1


def test_holding_flag_set_inside_write_session_and_cleared_after() -> None:
    assert not holding_write_transaction()
    with session_scope(write=True) as s:
        s.execute(select(models.User.id)).all()
        assert holding_write_transaction()
        with pytest.raises(RuntimeError, match="holding write transaction"):
            check_not_holding_write("test operation")
    assert not holding_write_transaction()
    check_not_holding_write("test operation")

    with pytest.raises(ZeroDivisionError):
        with session_scope(write=True) as s:
            s.execute(select(models.User.id)).all()
            assert holding_write_transaction()
            raise ZeroDivisionError
    assert not holding_write_transaction()

    with session_scope() as s:
        s.execute(select(models.User.id)).all()
        assert not holding_write_transaction()


def test_holding_flag_is_per_thread() -> None:
    seen: list[bool] = []
    with session_scope(write=True) as s:
        s.execute(select(models.User.id)).all()
        t = threading.Thread(target=lambda: seen.append(holding_write_transaction()))
        t.start()
        t.join()
    assert seen == [False]


def test_write_engine_uses_begin_immediate_and_read_engine_begin() -> None:
    statements: dict[str, list[str]] = {"read": [], "write": []}

    def recorder(kind: str) -> Any:
        def _rec(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            statements[kind].append(statement.strip().upper())

        return _rec

    read_engine, write_engine = db.get_engine(), db.get_engine(write=True)
    read_listener, write_listener = recorder("read"), recorder("write")
    event.listen(read_engine, "before_cursor_execute", read_listener)
    event.listen(write_engine, "before_cursor_execute", write_listener)
    try:
        with session_scope() as s:
            s.execute(select(models.User.id)).all()
        with session_scope(write=True) as s:
            s.execute(select(models.User.id)).all()
    finally:
        event.remove(read_engine, "before_cursor_execute", read_listener)
        event.remove(write_engine, "before_cursor_execute", write_listener)
    assert "BEGIN" in statements["read"]
    assert "BEGIN IMMEDIATE" not in statements["read"]
    assert "BEGIN IMMEDIATE" in statements["write"]


def test_sqlite_pragmas_on_both_engines() -> None:
    for write in (False, True):
        with db.get_engine(write=write).connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
            assert conn.exec_driver_sql("PRAGMA secure_delete").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1  # NORMAL
            conn.rollback()


def test_foreign_key_cascade(make_project: Any, make_paper: Any) -> None:
    pid, paper = make_project(), make_paper()
    with session_scope(write=True) as s:
        s.add(models.ProjectSeed(project_id=pid, paper_id=paper, added_via="doi"))
    with session_scope(write=True) as s:
        s.delete(s.get(models.Project, pid))
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.ProjectSeed)) == 0
    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            s.add(models.ProjectSeed(project_id=424242, paper_id=paper, added_via="doi"))


def test_session_scope_commits_and_rolls_back() -> None:
    with session_scope(write=True) as s:
        s.add(
            models.Concept(
                canonical_label="Test concept", ontology_source="user", external_id="user:test concept"
            )
        )
    with pytest.raises(KeyError):
        with session_scope(write=True) as s:
            s.add(models.Concept(canonical_label="Other", ontology_source="user", external_id="user:other"))
            s.flush()
            raise KeyError("boom")
    with session_scope() as s:
        labels = s.scalars(select(models.Concept.canonical_label)).all()
    assert labels == ["Test concept"]


# ------------------------------------------------------------------------------------------------
# Background writer
# ------------------------------------------------------------------------------------------------


def test_background_writer_flush() -> None:
    threads: list[int] = []

    def make(i: int) -> Any:
        def write(session: Any) -> None:
            threads.append(threading.get_ident())
            session.add(
                models.CostLedger(
                    provider="arxiv", call_type="feed", endpoint=f"/atom/test{i}", status_code=200
                )
            )

        return write

    for i in range(25):
        enqueue_write(make(i))
    flush_writes()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.CostLedger)) == 25
    assert threads and threading.get_ident() not in set(threads)


def test_background_writer_isolates_failures() -> None:
    def bad(session: Any) -> None:
        session.add(models.Job(kind="enrich", dedupe_key=None))  # NOT NULL violation

    def good(session: Any) -> None:
        session.add(
            models.CostLedger(provider="openalex", call_type="lookup", endpoint="/works/W1", status_code=200)
        )

    enqueue_write(good)
    enqueue_write(bad)
    enqueue_write(good)
    flush_writes()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.CostLedger)) == 2
        assert s.scalar(select(func.count()).select_from(models.Job)) == 0


def test_flush_while_holding_write_raises_in_strict_mode() -> None:
    gate = threading.Event()

    def slow(session: Any) -> None:
        gate.wait(5)

    enqueue_write(slow)
    try:
        with session_scope(write=True) as s:
            s.execute(select(models.User.id)).all()
            with pytest.raises(RuntimeError, match="holding write transaction"):
                flush_writes(timeout=0.1)
    finally:
        gate.set()
    flush_writes()


def test_shutdown_writer_restarts_lazily() -> None:
    db.shutdown_writer()
    enqueue_write(
        lambda s: s.add(models.CostLedger(provider="crossref", call_type="lookup", endpoint="/works/x"))
    )
    flush_writes()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.CostLedger)) == 1


# ------------------------------------------------------------------------------------------------
# Partial unique index on active jobs
# ------------------------------------------------------------------------------------------------


def _add_job(key: str, status: str) -> int:
    with session_scope(write=True) as s:
        job = models.Job(kind="build_deck", dedupe_key=key, status=status, project_id=1)
        s.add(job)
        s.flush()
        return job.id


def _set_status(job_id: int, status: str) -> None:
    with session_scope(write=True) as s:
        job = s.get(models.Job, job_id)
        assert job is not None
        job.status = status


@pytest.mark.parametrize("active", ["queued", "running"])
def test_uq_jobs_active_blocks_second_active_job(active: str) -> None:
    first = _add_job("k" * 64, active)
    for status in ("queued", "running"):
        with pytest.raises(IntegrityError):
            _add_job("k" * 64, status)
    # finished jobs never block
    _set_status(first, "succeeded")
    second = _add_job("k" * 64, "queued")
    _set_status(second, "failed")
    _add_job("k" * 64, "running")
    _add_job("k" * 64, "failed")
    _add_job("other", "queued")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Job)) == 5
    assert not holding_write_transaction()


# ------------------------------------------------------------------------------------------------
# init_db, reset_engines, URLs
# ------------------------------------------------------------------------------------------------


def test_init_db_creates_local_user_and_is_idempotent(radar_data_dir: Path) -> None:
    with session_scope() as s:
        user = s.get(models.User, 1)
        assert user is not None
        assert (user.email, user.display_name, user.password_hash, user.is_admin) == (
            None,
            "You",
            None,
            False,
        )
        assert user.preferences_json == {"auto_expand": True}
        assert get_setting(s, "scheduler_enabled") is False
    with session_scope(write=True) as s:
        user = s.get(models.User, 1)
        assert user is not None
        user.preferences_json = {**user.preferences_json, "auto_expand": False}
    db.init_db()
    db.init_db()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.User)) == 1
        assert s.get(models.User, 1).preferences_json["auto_expand"] is False  # type: ignore[union-attr]
    for sub in ("uploads", "models", "logs"):
        assert (radar_data_dir / sub).is_dir()


def test_init_db_seeds_the_scheduler_flag_from_env_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("RADAR_SCHEDULER", "1")
    get_settings.cache_clear()
    db.reset_engines()
    db.init_db()
    with session_scope() as s:
        assert s.get(models.User, 1).preferences_json == {"auto_expand": True}  # type: ignore[union-attr]
        assert get_setting(s, "scheduler_enabled") is True
    with session_scope(write=True) as s:
        set_setting(s, "scheduler_enabled", False)
    db.init_db()  # the stored value wins over the environment
    with session_scope() as s:
        assert get_setting(s, "scheduler_enabled") is False


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_in_memory_sqlite_works(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DATABASE_URL", url)
    get_settings.cache_clear()
    db.reset_engines()
    db.init_db()
    with session_scope(write=True) as s:
        s.add(models.Project(name="Test project", research_question="q"))
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 1
        assert s.get(models.User, 1) is not None
    db.reset_engines()
    db.init_db()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 0


def test_reset_engines_switches_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_project: Any
) -> None:
    make_project()
    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'nested' / 'other.db'}")
    get_settings.cache_clear()
    db.reset_engines()
    db.init_db()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 0
    assert (tmp_path / "nested" / "other.db").is_file()


def test_checkpoint_wal_truncates(radar_data_dir: Path, make_project: Any) -> None:
    for _ in range(3):
        make_project()
    wal = radar_data_dir / "radar.db-wal"
    assert wal.exists() and wal.stat().st_size > 0
    db.checkpoint_wal()
    assert wal.stat().st_size == 0
    with session_scope(write=True) as s:
        s.execute(select(models.User.id)).all()
        with pytest.raises(RuntimeError, match="holding write transaction"):
            db.checkpoint_wal()


def test_new_session_is_unmanaged() -> None:
    session = db.new_session(write=True)
    try:
        session.add(
            models.Concept(canonical_label="Test", ontology_source="openalex_topic", external_id="T1")
        )
        session.commit()
    finally:
        session.close()
    assert not holding_write_transaction()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Concept)) == 1


def test_all_spec_tables_exist() -> None:
    expected = {
        "users", "user_identities", "auth_sessions", "auth_tokens", "oauth_states", "app_settings",
        "projects", "project_seeds", "concepts", "papers", "paper_identifiers", "paper_versions", "authors",
        "paper_authors", "scholars", "documents", "document_sections", "document_chunks", "embeddings",
        "graph_edges", "paper_expansions", "interactions", "project_paper_states", "paper_reads",
        "recommendation_slates", "recommendation_items", "findings_summaries", "jobs", "ingestion_watermarks",
        "source_records", "duplicate_candidates", "cost_ledger",
    }  # fmt: skip
    assert set(models.Base.metadata.tables) == expected
    with session_scope() as s:
        names = set(s.scalars(text("SELECT name FROM sqlite_master WHERE type = 'table'")))
    assert expected <= names


def test_default_timestamps_are_aware(make_paper: Any) -> None:
    pid = make_paper()
    with session_scope() as s:
        paper = s.get(models.Paper, pid)
        assert paper is not None
        for value in (paper.created_at, paper.updated_at, paper.first_seen_at):
            assert value.tzinfo is not None and value.utcoffset() == timedelta(0)
        assert abs(datetime.now(UTC) - paper.created_at) < timedelta(minutes=5)
