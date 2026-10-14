"""Shared pytest fixtures for the Ratiocination backend.

FROZEN after the core foundation phase: nobody edits this file later (docs/CONVENTIONS.md).

Environment (set when this file is imported, before any ``radar`` module reads settings):
``RADAR_OFFLINE=1``, ``RADAR_STRICT_TX=1``, ``RADAR_JOBS_INLINE=1``, ``RADAR_EMBEDDER=local/hashing-v1``,
``HF_HUB_OFFLINE=1``, ``RADAR_ENV_FILE=`` (the developer's ``.env`` is never read), a session temp
``RADAR_DATA_DIR``; every other SPEC §3 variable is removed (keys, contact email, token, ...). Tests that
need one use ``monkeypatch.setenv(...)`` followed by ``get_settings.cache_clear()``.

Autouse (every test): a fresh temp data dir + SQLite DB (``RADAR_DATA_DIR``/``RADAR_DATABASE_URL``),
``get_settings.cache_clear()``, ``reset_engines()``, ``init_db()``. Teardown: ``flush_writes()``,
``radar.net.http.set_http(None)``, ``VectorIndex.clear_all()``, ``radar.embeddings.registry.reset()``,
``radar.jobs.runner.shutdown()`` (each ignoring ``NotImplementedError`` from unfinished modules), then the
log handlers installed by ``create_app()`` are removed, the engines are disposed and the temp data dir is
removed (every step runs; the first unexpected error is re-raised). The autouse fixture is
``radar_data_dir`` (the data directory ``Path``); request it by name when a test needs the path.

Fixtures:

* ``client`` - ``fastapi.testclient.TestClient`` over ``create_app()`` with the lifespan running, base URL
  ``http://127.0.0.1:8000`` and default header ``X-Radar-Client: web``.
* ``http_mock`` - an :class:`HttpMock` router installed as ``HttpClient(transport=httpx.MockTransport(...),
  sleep=lambda s: None)`` through ``radar.net.http.set_http``; ``.add(method, url_or_regex, response,
  status=200, headers=None, *, times=None)``; ``.requests`` lists every request; unmatched requests get
  HTTP 599 and are also listed in ``.unmatched``.
* ``fixture_path(name) -> Path`` and ``load_fixture(name)`` (``.json`` parsed, anything else bytes) for
  ``tests/fixtures/``.
* ``make_project(**overrides) -> int`` and ``make_paper(**overrides) -> int`` factories (own committed
  write sessions; nothing stays open).
* ``set_env(NAME=value, OTHER=None)`` - set/remove environment variables for the test, then clear the
  settings cache and reset the engines.
* ``assert_no_open_write()`` - fails when the current thread still holds a write transaction.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

# ------------------------------------------------------------------------------------------------
# Environment - must happen before radar reads its settings
# ------------------------------------------------------------------------------------------------

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="radar-tests-"))

_CLEARED_ENV = (
    "RADAR_DATABASE_URL",
    "RADAR_HOST",
    "RADAR_PORT",
    "RADAR_AUTH",
    "RADAR_PUBLIC_URL",
    "RADAR_PASSWORD_LOGIN",
    "RADAR_GOOGLE_CLIENT_ID",
    "RADAR_GOOGLE_CLIENT_SECRET",
    "RADAR_GITHUB_CLIENT_ID",
    "RADAR_GITHUB_CLIENT_SECRET",
    "RADAR_ALLOWED_EMAILS",
    "RADAR_ADMIN_EMAILS",
    "RADAR_SESSION_DAYS",
    "RADAR_REQUIRE_EMAIL_VERIFICATION",
    "RADAR_SMTP_HOST",
    "RADAR_SMTP_PORT",
    "RADAR_SMTP_USERNAME",
    "RADAR_SMTP_PASSWORD",
    "RADAR_SMTP_FROM",
    "RADAR_SMTP_SECURITY",
    "ANTHROPIC_API_KEY",
    "RADAR_SUMMARY_MODEL",
    "RADAR_SUMMARIES_PER_DAY",
    "RADAR_SUMMARIES_GLOBAL_PER_DAY",
    "RADAR_BREAKTHROUGHS_PER_DAY",
    "RADAR_EXPANSIONS_PER_USER_PER_DAY",
    "RADAR_USER_STORAGE_MB",
    "RADAR_MAX_PROJECTS",
    "RADAR_TLS_CERT",
    "RADAR_TLS_KEY",
    "RADAR_BEHIND_TLS_PROXY",
    "RADAR_ALLOWED_HOSTS",
    "RADAR_DEV_ORIGINS",
    "RADAR_CLIENT_IP_HEADER",
    "RADAR_SCHEDULER",
    "RADAR_DAILY_CRON",
    "RADAR_TIMEZONE",
    "OPENALEX_API_KEY",
    "RADAR_OPENALEX_DAILY_BUDGET_USD",
    "RADAR_BUDGET_STOP_FRACTION",
    "S2_API_KEY",
    "RADAR_S2_BREAKER_MINUTES",
    "RADAR_ARXIV_BREAKER_MINUTES",
    "RADAR_CONTACT_EMAIL",
    "RADAR_MAX_EXPANSIONS_PER_DAY",
    "RADAR_MAX_UPLOAD_MB",
)
for _name in _CLEARED_ENV:
    os.environ.pop(_name, None)
os.environ.update(
    {
        "RADAR_ENV_FILE": "",
        "RADAR_OFFLINE": "1",
        "RADAR_AUTH": "local",
        "RADAR_STRICT_TX": "1",
        "RADAR_JOBS_INLINE": "1",
        "RADAR_EMBEDDER": "local/hashing-v1",
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "RADAR_DATA_DIR": str(_SESSION_DIR / "data"),
    }
)

from radar import db as radar_db  # noqa: E402
from radar.config import get_settings  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _run_cleanup(steps: list[Callable[[], Any]]) -> None:
    """Run every step; ignore NotImplementedError (unfinished modules); re-raise the first other error."""
    first_error: BaseException | None = None
    for step in steps:
        try:
            step()
        except NotImplementedError:
            pass
        except Exception as exc:  # keep cleaning up, report afterwards
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _set_http_none() -> None:
    from radar.net import http as net_http

    net_http.set_http(None)


def _clear_vector_index() -> None:
    from radar.embeddings.index import VectorIndex

    VectorIndex.clear_all()


def _reset_embedders() -> None:
    from radar.embeddings import registry

    registry.reset()


def _shutdown_runner() -> None:
    from radar.jobs import runner

    runner.shutdown()


def _reset_logging() -> None:
    from radar.logging_setup import reset_logging

    reset_logging()


def _reset_auth_throttle() -> None:
    from radar.auth import throttle

    throttle.reset()


# ------------------------------------------------------------------------------------------------
# Database per test
# ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _radar_template_db() -> Iterator[Path]:
    """A migrated database built once per session; each test starts from a copy of it."""
    template_dir = _SESSION_DIR / "template"
    template_dir.mkdir(parents=True, exist_ok=True)
    db_path = template_dir / "radar.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RADAR_DATA_DIR", str(template_dir))
        mp.setenv("RADAR_DATABASE_URL", f"sqlite:///{db_path}")
        get_settings.cache_clear()
        radar_db.reset_engines()
        radar_db.init_db()
        radar_db.checkpoint_wal("TRUNCATE")
        radar_db.reset_engines()
    get_settings.cache_clear()
    yield db_path
    radar_db.shutdown_writer()
    radar_db.reset_engines()
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def radar_data_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, _radar_template_db: Path
) -> Iterator[Path]:
    """Fresh temp data dir and database for every test (see module docstring)."""
    data_dir = tmp_path_factory.mktemp("radar-data")
    db_path = data_dir / "radar.db"
    monkeypatch.setenv("RADAR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    radar_db.reset_engines()
    shutil.copyfile(_radar_template_db, db_path)
    radar_db.init_db()
    yield data_dir
    _run_cleanup(
        [
            radar_db.flush_writes,
            _set_http_none,
            _clear_vector_index,
            _reset_embedders,
            _shutdown_runner,
            radar_db.flush_writes,
            _reset_logging,
            _reset_auth_throttle,
            radar_db.reset_engines,
            get_settings.cache_clear,
            lambda: shutil.rmtree(data_dir, ignore_errors=True),
        ]
    )


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """``set_env(OPENALEX_API_KEY="k", RADAR_OFFLINE="0", S2_API_KEY=None)``: set (``None`` removes)
    environment variables for this test, then ``get_settings.cache_clear()`` and ``reset_engines()``
    (the per-test database is kept unless ``RADAR_DATABASE_URL`` itself is changed)."""

    def _set(**values: str | None) -> None:
        for name, value in values.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, str(value))
        get_settings.cache_clear()
        radar_db.reset_engines()

    return _set


@pytest.fixture
def assert_no_open_write() -> Callable[[], None]:
    """``assert_no_open_write()`` fails if this thread still holds a write transaction."""

    def check() -> None:
        assert not radar_db.holding_write_transaction(), "a write transaction is still open on this thread"

    return check


# ------------------------------------------------------------------------------------------------
# HTTP app client
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[Any]:
    """TestClient over ``create_app()`` (lifespan on); sends ``X-Radar-Client: web`` by default."""
    from fastapi.testclient import TestClient

    from radar.main import create_app

    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}) as test_client:
        yield test_client


# ------------------------------------------------------------------------------------------------
# Outbound HTTP mock
# ------------------------------------------------------------------------------------------------

ResponseSpec = httpx.Response | dict[str, Any] | list[Any] | str | bytes | None


@dataclass
class MockRoute:
    method: str
    url: str | re.Pattern[str]
    response: ResponseSpec | Callable[[httpx.Request], ResponseSpec]
    status: int = 200
    headers: dict[str, str] | None = None
    times: int | None = None
    calls: int = 0

    def exhausted(self) -> bool:
        return self.times is not None and self.calls >= self.times

    def matches(self, request: httpx.Request) -> bool:
        if self.method != "*" and request.method.upper() != self.method:
            return False
        full = str(request.url)
        bare = full.split("?", 1)[0]
        if isinstance(self.url, re.Pattern):
            return self.url.search(full) is not None
        if self.url in (full, bare):
            return True
        try:
            return re.fullmatch(self.url, full) is not None or re.fullmatch(self.url, bare) is not None
        except re.error:
            return False


def _to_response(spec: ResponseSpec, status: int, headers: dict[str, str] | None) -> httpx.Response:
    if isinstance(spec, httpx.Response):
        # A fresh copy so one route can answer many requests.
        return httpx.Response(spec.status_code, headers=spec.headers, content=spec.content)
    if isinstance(spec, (dict, list)):
        return httpx.Response(status, json=spec, headers=headers)
    if isinstance(spec, str):
        return httpx.Response(status, text=spec, headers=headers)
    if isinstance(spec, (bytes, bytearray)):
        return httpx.Response(status, content=bytes(spec), headers=headers)
    if spec is None:
        return httpx.Response(status, headers=headers)
    raise TypeError(f"unsupported mock response type: {type(spec).__name__}")


class HttpMock:
    """Router for ``httpx.MockTransport``.

    ``add(method, url_or_regex, response, status=200, headers=None, *, times=None)``:

    * ``method``: HTTP method (case-insensitive) or ``"*"``;
    * ``url_or_regex``: a compiled ``re.Pattern`` (``search`` on the full URL incl. query), or a string
      that equals the full URL or the URL without its query string, or that ``re.fullmatch``-es either;
    * ``response``: ``httpx.Response`` (used as-is; ``status``/``headers`` ignored), ``dict``/``list``
      (JSON), ``str`` (text), ``bytes``, ``None`` (empty body), or a callable ``(request) -> any of those``;
    * ``times``: answer at most this many requests, then fall through to later routes.

    Routes are tried in the order they were added. Every request is appended to ``requests``; requests
    no route answers get HTTP 599 and are also appended to ``unmatched``.
    """

    def __init__(self) -> None:
        self.routes: list[MockRoute] = []
        self.requests: list[httpx.Request] = []
        self.unmatched: list[httpx.Request] = []

    def add(
        self,
        method: str,
        url_or_regex: str | re.Pattern[str],
        response: ResponseSpec | Callable[[httpx.Request], ResponseSpec],
        status: int = 200,
        headers: dict[str, str] | None = None,
        *,
        times: int | None = None,
    ) -> MockRoute:
        route = MockRoute(method.upper(), url_or_regex, response, status, headers, times)
        self.routes.append(route)
        return route

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for route in self.routes:
            if route.exhausted() or not route.matches(request):
                continue
            route.calls += 1
            spec = route.response
            if callable(spec) and not isinstance(spec, httpx.Response):
                spec = spec(request)
            return _to_response(spec, route.status, route.headers)
        self.unmatched.append(request)
        return httpx.Response(599, json={"error": "no mock route matched"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def calls(
        self, method: str | None = None, url_or_regex: str | re.Pattern[str] | None = None
    ) -> list[httpx.Request]:
        """Recorded requests filtered like ``add`` matches them."""
        probe = MockRoute(
            (method or "*").upper(), url_or_regex if url_or_regex is not None else re.compile(""), None
        )
        return [r for r in self.requests if probe.matches(r)]


@pytest.fixture
def http_mock() -> Iterator[HttpMock]:
    """Install a mock transport for all outbound HTTP (see :class:`HttpMock`)."""
    from radar.net import http as net_http

    mock = HttpMock()
    net_http.set_http(net_http.HttpClient(transport=httpx.MockTransport(mock.handler), sleep=lambda s: None))
    yield mock
    net_http.set_http(None)


# ------------------------------------------------------------------------------------------------
# Payload fixtures
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def fixture_path() -> Callable[[str], Path]:
    """``fixture_path("arxiv_feed_multi.xml") -> Path`` inside ``tests/fixtures``."""

    def _path(name: str) -> Path:
        path = FIXTURES_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"no fixture named {name!r} in {FIXTURES_DIR}")
        return path

    return _path


@pytest.fixture
def load_fixture(fixture_path: Callable[[str], Path]) -> Callable[[str], Any]:
    """``load_fixture(name)``: parsed JSON for ``.json`` files, raw bytes otherwise."""

    def _load(name: str) -> Any:
        path = fixture_path(name)
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        return path.read_bytes()

    return _load


# ------------------------------------------------------------------------------------------------
# Factories
# ------------------------------------------------------------------------------------------------

_project_counter = itertools.count(1)
_paper_counter = itertools.count(1)


def _fallback_normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title)
    text = re.sub(r"[{}$\\]", "", text).casefold()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())


def _fallback_surname(name: str) -> str:
    base = name.split(",", 1)[0] if "," in name else (name.split()[-1] if name.split() else "")
    base = "".join(c for c in unicodedata.normalize("NFKD", base) if not unicodedata.combining(c))
    return base.casefold().strip()


def _normalize_title(title: str) -> str:
    try:
        from radar.store.identifiers import normalize_title

        return normalize_title(title)
    except NotImplementedError:
        return _fallback_normalize_title(title)


def _surname(name: str) -> str:
    try:
        from radar.store.identifiers import surname_norm

        return surname_norm(name)
    except NotImplementedError:
        return _fallback_surname(name)


@pytest.fixture
def make_project() -> Callable[..., int]:
    """``make_project(**overrides) -> project id``.

    Defaults: owner 1, name ``"Test project N"``, a synthetic research question, empty description,
    ``active=True`` and default ``ProjectSettings``. ``settings={...}`` is a partial dict merged over the
    defaults (validated); any other keyword sets that ``projects`` column.
    """
    from radar import models
    from radar.schemas import ProjectSettings

    def _make(**overrides: Any) -> int:
        n = next(_project_counter)
        patch = overrides.pop("settings", None) or {}
        settings = ProjectSettings.model_validate({**ProjectSettings().model_dump(), **patch})
        values: dict[str, Any] = {
            "owner_id": models.LOCAL_USER_ID,
            "name": f"Test project {n}",
            "research_question": f"Test question {n}: how should synthetic test papers be ranked?",
            "description": "",
            "settings_json": settings.model_dump(mode="json"),
            "active": True,
        }
        values.update(overrides)
        with radar_db.session_scope(write=True) as session:
            project = models.Project(**values)
            session.add(project)
            session.flush()
            project_id = project.id
        return project_id

    return _make


@pytest.fixture
def make_paper() -> Callable[..., int]:
    """``make_paper(**overrides) -> paper id``.

    Defaults: title ``"Test paper N"`` (``title=`` sets ``canonical_title`` and ``title_norm``), a synthetic
    abstract, today's ``publication_date``/``year``, status ``active``, category ``cs.IR``,
    ``ingested_via="arxiv_feed"``, one author ``"Test Author N"``.
    Extra keywords: ``identifiers={scheme: normalized value}`` (primary rows), ``authors=[names]``,
    ``versions=["v1", "v2", "published"]`` (arXiv rows for ``vN``, OpenAlex for others),
    ``categories=[...]`` (also sets ``primary_category``); any other keyword sets that ``papers`` column.
    """
    from radar import models

    def _make(**overrides: Any) -> int:
        n = next(_paper_counter)
        now = datetime.now(UTC)
        title = overrides.pop("title", None) or overrides.pop("canonical_title", None) or f"Test paper {n}"
        identifiers: dict[str, str] = dict(overrides.pop("identifiers", None) or {})
        authors = overrides.pop("authors", None)
        if authors is None:
            authors = [f"Test Author {n}"]
        versions: list[str] = list(overrides.pop("versions", None) or [])
        categories = overrides.pop("categories", None)
        values: dict[str, Any] = {
            "canonical_title": title,
            "title_norm": _normalize_title(title),
            "first_author_norm": _surname(authors[0]) if authors else None,
            "abstract": f"Synthetic abstract for test paper {n}.",
            "publication_date": now.date(),
            "year": now.year,
            "status": "active",
            "primary_category": "cs.IR",
            "categories_json": ["cs.IR"],
            "ingested_via": "arxiv_feed",
            "first_seen_at": now,
        }
        if categories is not None:
            values["categories_json"] = list(categories)
            values["primary_category"] = categories[0] if categories else None
        values.update(overrides)
        with radar_db.session_scope(write=True) as session:
            paper = models.Paper(**values)
            session.add(paper)
            session.flush()
            for scheme, value in identifiers.items():
                session.add(
                    models.PaperIdentifier(paper_id=paper.id, scheme=scheme, value=value, is_primary=True)
                )
            for position, name in enumerate(authors):
                author = models.Author(display_name=name, name_norm=_surname(name))
                session.add(author)
                session.flush()
                session.add(
                    models.PaperAuthor(
                        paper_id=paper.id, author_id=author.id, position=position, raw_name=name
                    )
                )
            for label in versions:
                session.add(
                    models.PaperVersion(
                        paper_id=paper.id,
                        version_label=label,
                        source="arxiv" if re.fullmatch(r"v\d+", label) else "openalex",
                        observed_at=now,
                    )
                )
            paper_id = paper.id
        return paper_id

    return _make
