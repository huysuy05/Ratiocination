"""Database engines, sessions, strict transactions and the background writer (SPEC §2.1).

SQLite is single-writer, so there are two engines on the same URL:

* the **read** engine emits ``BEGIN`` (deferred) for every transaction;
* the **write** engine emits ``BEGIN IMMEDIATE`` so a writer takes the lock up front and never fails
  a read->write upgrade.

Every SQLite connection runs ``PRAGMA busy_timeout=30000; journal_mode=WAL; synchronous=NORMAL;
foreign_keys=ON; secure_delete=ON`` with the DB-API ``isolation_level = None`` (SQLAlchemy emits the
BEGIN itself). PostgreSQL engines behave normally.

Usage rules are in docs/CONVENTIONS.md ("Database rules"). In short:

* ``with session_scope(write=True) as s:`` for writes, ``with session_scope() as s:`` for reads;
* never write in a read session (strict mode raises :class:`StrictTransactionError`);
* never do network calls, embedding or ranking while holding a write transaction;
  ``check_not_holding_write("...")`` raises ``RuntimeError("holding write transaction")`` in strict
  mode when the current thread holds one;
* the cost ledger and job progress use :func:`enqueue_write` so they never write from the caller's
  thread.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from radar.config import MIGRATIONS_DIR, get_settings

log = logging.getLogger(__name__)

__all__ = [
    "StrictTransactionError",
    "check_not_holding_write",
    "checkpoint_wal",
    "enqueue_write",
    "flush_writes",
    "get_engine",
    "holding_write_transaction",
    "init_db",
    "new_session",
    "reset_engines",
    "session_scope",
    "shutdown_writer",
]

_WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE"})
_HOLDER_KEY = "radar_write_holder"


class StrictTransactionError(RuntimeError):
    """Raised in strict mode (``RADAR_STRICT_TX=1``) when the transaction rules are broken."""


# ------------------------------------------------------------------------------------------------
# Write-transaction holder tracking (thread-local semantics, tracked per connection so a commit that
# happens on another thread - e.g. FastAPI dependency teardown in a worker thread - still clears it).
# ------------------------------------------------------------------------------------------------

_holders_lock = threading.Lock()
_holders: dict[int, int] = {}


def _mark_write_begin(info: dict[Any, Any]) -> None:
    if info.get(_HOLDER_KEY) is not None:
        return
    ident = threading.get_ident()
    info[_HOLDER_KEY] = ident
    with _holders_lock:
        _holders[ident] = _holders.get(ident, 0) + 1


def _release_write(info: dict[Any, Any]) -> None:
    ident = info.pop(_HOLDER_KEY, None)
    if ident is None:
        return
    with _holders_lock:
        remaining = _holders.get(ident, 0) - 1
        if remaining > 0:
            _holders[ident] = remaining
        else:
            _holders.pop(ident, None)


def holding_write_transaction() -> bool:
    """True while the current thread has an open transaction on the write engine.

    Set by the write engine's ``begin`` event; cleared on commit, rollback or connection close.
    """
    return _holders.get(threading.get_ident(), 0) > 0


def check_not_holding_write(operation: str = "operation") -> None:
    """Strict-mode guard for slow or networked work (HttpClient.request, connectors, submit_job).

    Raises ``StrictTransactionError`` (a ``RuntimeError``) with the message
    ``"holding write transaction: <operation>"`` when ``RADAR_STRICT_TX=1`` and the current thread
    holds a write transaction. A no-op otherwise.
    """
    if holding_write_transaction() and get_settings().strict_tx:
        raise StrictTransactionError(f"holding write transaction: {operation}")


# ------------------------------------------------------------------------------------------------
# Engines
# ------------------------------------------------------------------------------------------------

_engine_lock = threading.RLock()
_read_engine: Engine | None = None
_write_engine: Engine | None = None
_read_factory: sessionmaker[Session] | None = None
_write_factory: sessionmaker[Session] | None = None
_effective_url: URL | None = None
_is_memory = False
_memory_keeper: Any = None  # raw DB-API connection keeping a shared in-memory SQLite DB alive


def _resolve_url(raw: str) -> tuple[URL, bool]:
    """Return (url, is_memory). In-memory SQLite becomes a named shared-cache URI so that the read
    and write engines (and the migration connection) see the same database."""
    url = make_url(raw)
    if url.get_backend_name() != "sqlite":
        return url, False
    database = url.database or ""
    if database in ("", ":memory:"):
        query = dict(url.query)
        query.update({"mode": "memory", "cache": "shared", "uri": "true"})
        return url.set(database=f"file:radar-mem-{uuid.uuid4().hex}", query=query), True
    if url.query.get("mode") == "memory":
        return url, True
    return url, False


def _sqlite_connect_args() -> dict[str, Any]:
    return {"check_same_thread": False, "timeout": 30}


def _install_sqlite_pragmas(engine: Engine, *, foreign_keys: bool = True) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:  # pragma: no cover - trivial
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
            cur.execute("PRAGMA secure_delete=ON")
        finally:
            cur.close()


def _first_keyword(statement: str) -> str:
    s = statement.lstrip()
    while s.startswith("--") or s.startswith("/*"):
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl < 0 else s[nl + 1 :].lstrip()
        else:
            end = s.find("*/")
            s = "" if end < 0 else s[end + 2 :].lstrip()
    head = s.split(None, 1)[0] if s else ""
    return head.upper()


def _make_engine(url: URL, *, write: bool, strict: bool, is_memory: bool = False) -> Engine:
    is_sqlite = url.get_backend_name() == "sqlite"
    # hide_parameters: a failing statement's error (and its log line) never shows the bound values,
    # which can be emails, research questions or document text (SPEC §16 logging).
    kwargs: dict[str, Any] = {"hide_parameters": True}
    if is_sqlite:
        kwargs["connect_args"] = _sqlite_connect_args()
        if is_memory:
            kwargs["poolclass"] = QueuePool
    engine = create_engine(url, **kwargs)
    if is_sqlite:
        _install_sqlite_pragmas(engine)

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        if is_sqlite:
            conn.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
        if write:
            _mark_write_begin(conn.info)

    if write:

        @event.listens_for(engine, "commit")
        def _on_commit(conn: Any) -> None:
            _release_write(conn.info)

        @event.listens_for(engine, "rollback")
        def _on_rollback(conn: Any) -> None:
            _release_write(conn.info)

        @event.listens_for(engine.pool, "checkin")
        def _on_checkin(_dbapi_conn: Any, record: Any) -> None:
            if record is not None:
                _release_write(record.info)

        @event.listens_for(engine.pool, "invalidate")
        def _on_invalidate(_dbapi_conn: Any, record: Any, _exc: Any) -> None:
            if record is not None:
                _release_write(record.info)

    elif strict:

        @event.listens_for(engine, "before_cursor_execute")
        def _guard_read_engine(
            _conn: Any, _cursor: Any, statement: str, _params: Any, _ctx: Any, _many: bool
        ) -> None:
            if _first_keyword(statement) in _WRITE_KEYWORDS:
                raise StrictTransactionError(
                    "write statement in a read session (use session_scope(write=True) / get_write_db)"
                )

    return engine


def _ensure_engines() -> tuple[Engine, Engine]:
    global _read_engine, _write_engine, _read_factory, _write_factory
    global _effective_url, _is_memory, _memory_keeper
    with _engine_lock:
        if _read_engine is not None and _write_engine is not None:
            return _read_engine, _write_engine
        settings = get_settings()
        url, is_memory = _resolve_url(settings.database_url)
        if url.get_backend_name() == "sqlite" and not is_memory and url.database:
            from pathlib import Path

            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        _effective_url, _is_memory = url, is_memory
        if is_memory:
            import sqlite3

            db = url.database or ""
            uri = f"{db}?mode=memory&cache=shared"
            _memory_keeper = sqlite3.connect(uri, uri=True, check_same_thread=False)
        _write_engine = _make_engine(url, write=True, strict=settings.strict_tx, is_memory=is_memory)
        _read_engine = _make_engine(url, write=False, strict=settings.strict_tx, is_memory=is_memory)
        _write_factory = sessionmaker(bind=_write_engine, expire_on_commit=False)
        _read_factory = sessionmaker(bind=_read_engine, expire_on_commit=False)
        return _read_engine, _write_engine


def get_engine(write: bool = False) -> Engine:
    """The read (default) or write engine for the configured database (created lazily)."""
    read, write_engine = _ensure_engines()
    return write_engine if write else read


def new_session(write: bool = False) -> Session:
    """A new, unmanaged Session on the read or write engine (``expire_on_commit=False``).

    Prefer :func:`session_scope`; the caller must commit/rollback and close this session.
    """
    _ensure_engines()
    factory = _write_factory if write else _read_factory
    assert factory is not None
    return factory()


@contextmanager
def session_scope(write: bool = False) -> Iterator[Session]:
    """Yield a Session; commit on success, roll back on exception, always close (SPEC §2.1)."""
    session = new_session(write=write)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def checkpoint_wal(mode: str = "TRUNCATE") -> None:
    """Run ``PRAGMA wal_checkpoint(<mode>)`` outside any transaction (SQLite only; no-op otherwise).

    Used after deletions of private data so deleted content does not linger in ``radar.db-wal``
    (SPEC §10). Must be called after the deleting transaction has committed.
    """
    mode = mode.upper()
    if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
        raise ValueError("invalid checkpoint mode")
    engine = get_engine(write=True)
    if engine.dialect.name != "sqlite":
        return
    if holding_write_transaction():
        raise StrictTransactionError("holding write transaction: checkpoint_wal")
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        try:
            cur.execute(f"PRAGMA wal_checkpoint({mode})")
            cur.fetchall()
        finally:
            cur.close()
    finally:
        raw.close()


def reset_engines() -> None:
    """Flush pending background writes, dispose both engines and forget them (tests).

    The next database access re-reads ``get_settings().database_url``.
    """
    global _read_engine, _write_engine, _read_factory, _write_factory
    global _effective_url, _is_memory, _memory_keeper
    try:
        _writer.flush(timeout=5.0, raise_on_hold=False)
    except Exception:  # pragma: no cover - best effort
        log.exception("flush before reset_engines failed")
    with _engine_lock:
        for engine in (_read_engine, _write_engine):
            if engine is not None:
                engine.dispose()
        _read_engine = _write_engine = None
        _read_factory = _write_factory = None
        _effective_url = None
        _is_memory = False
        if _memory_keeper is not None:
            try:
                _memory_keeper.close()
            except Exception:  # pragma: no cover
                pass
            _memory_keeper = None
    with _holders_lock:
        _holders.clear()


# ------------------------------------------------------------------------------------------------
# Background writer
# ------------------------------------------------------------------------------------------------

_STOP = object()
_MAX_BATCH = 200


class _BackgroundWriter:
    """Runs ``fn(session)`` callables on one daemon thread in short, batched write sessions."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()
        self._cond = threading.Condition()
        self._pending = 0
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="radar-db-writer", daemon=True)
                self._thread.start()

    def is_writer_thread(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def enqueue(self, fn: Callable[[Session], None]) -> None:
        with self._cond:
            self._pending += 1
        self._queue.put(fn)
        self._ensure_thread()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            batch = [item]
            stop_after = False
            while len(batch) < _MAX_BATCH:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    stop_after = True
                    break
                batch.append(nxt)
            try:
                self._process(batch)
            finally:
                with self._cond:
                    self._pending -= len(batch)
                    self._cond.notify_all()
            if stop_after:
                return

    @staticmethod
    def _process(batch: list[Callable[[Session], None]]) -> None:
        try:
            with session_scope(write=True) as session:
                for fn in batch:
                    fn(session)
            return
        except Exception:
            if len(batch) == 1:
                log.exception("background write failed")
                return
            log.warning("background write batch of %d failed; retrying items one by one", len(batch))
        for fn in batch:
            try:
                with session_scope(write=True) as session:
                    fn(session)
            except Exception:
                log.exception("background write failed")

    def flush(self, timeout: float = 5.0, *, raise_on_hold: bool = True) -> None:
        if self.is_writer_thread():
            return
        with self._cond:
            if self._pending <= 0:
                return
        if holding_write_transaction():
            # The writer would block on BEGIN IMMEDIATE until this thread's transaction ends.
            if raise_on_hold and get_settings().strict_tx:
                raise StrictTransactionError("holding write transaction: flush_writes")
            log.warning("flush_writes called while holding a write transaction; not waiting")
            return
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning("flush_writes timed out with %d pending writes", self._pending)
                    return
                self._cond.wait(remaining)

    def shutdown(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            return
        self.flush(timeout, raise_on_hold=False)
        self._queue.put(_STOP)
        thread.join(timeout)
        with self._thread_lock:
            if self._thread is thread:
                self._thread = None


_writer = _BackgroundWriter()


def enqueue_write(fn: Callable[[Session], None]) -> None:
    """Run ``fn(session)`` on the background writer thread inside a short write session (batched).

    ``fn`` must only touch the database (it may be re-run alone if its batch fails) and must not
    raise for expected conditions; failures are logged and dropped.
    """
    _writer.enqueue(fn)


def flush_writes(timeout: float = 5.0) -> None:
    """Block until every write enqueued so far has been applied (or ``timeout`` seconds pass)."""
    _writer.flush(timeout)


def shutdown_writer() -> None:
    """Flush and stop the background writer thread (it restarts lazily on the next enqueue)."""
    _writer.shutdown()


atexit.register(shutdown_writer)


# ------------------------------------------------------------------------------------------------
# Schema + bootstrap
# ------------------------------------------------------------------------------------------------


def _alembic_config() -> Any:
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _run_migrations() -> None:
    from alembic import command

    _ensure_engines()
    url = _effective_url
    assert url is not None
    is_sqlite = url.get_backend_name() == "sqlite"
    kwargs: dict[str, Any] = {"poolclass": NullPool, "hide_parameters": True}
    if is_sqlite:
        kwargs["connect_args"] = _sqlite_connect_args()
    engine = create_engine(url, **kwargs)
    if is_sqlite:
        # Foreign keys OFF while migrating (Alembic batch mode recreates tables on SQLite).
        _install_sqlite_pragmas(engine, foreign_keys=False)

        @event.listens_for(engine, "begin")
        def _begin_immediate(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    try:
        with engine.connect() as conn:
            cfg = _alembic_config()
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()


def init_db() -> None:
    """Create the data directories, run Alembic ``upgrade head`` programmatically, make sure user 1
    exists (SPEC §4: the local user, claimed by the first account in accounts mode) with seeded
    preferences (``auto_expand = true``), and seed the global ``scheduler_enabled`` setting from
    RADAR_SCHEDULER. Existing values are never overwritten (the DB wins)."""
    settings = get_settings()
    for directory in (settings.data_dir, settings.uploads_dir, settings.models_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _run_migrations()
    _ensure_local_user()


def _ensure_local_user() -> None:
    from radar.models import LOCAL_USER_ID, AppSetting, User

    settings = get_settings()
    defaults: dict[str, Any] = {"auto_expand": True}
    with session_scope(write=True) as session:
        user = session.get(User, LOCAL_USER_ID)
        if user is None:
            session.add(User(id=LOCAL_USER_ID, display_name="You", preferences_json=dict(defaults)))
        else:
            prefs = dict(user.preferences_json or {})
            missing = {k: v for k, v in defaults.items() if k not in prefs}
            if missing:
                user.preferences_json = {**prefs, **missing}
        if session.scalar(select(AppSetting.id).where(AppSetting.key == "scheduler_enabled")) is None:
            session.add(AppSetting(key="scheduler_enabled", value_json=bool(settings.scheduler)))
