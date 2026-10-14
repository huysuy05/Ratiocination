"""The shared conftest fixtures behave as documented."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from radar import models
from radar.config import REPO_ROOT, get_settings
from radar.db import holding_write_transaction, session_scope


def test_environment_is_isolated(radar_data_dir: Path) -> None:
    assert os.environ["RADAR_OFFLINE"] == "1"
    assert os.environ["RADAR_STRICT_TX"] == "1"
    assert os.environ["RADAR_JOBS_INLINE"] == "1"
    assert os.environ["RADAR_EMBEDDER"] == "local/hashing-v1"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert radar_data_dir.is_dir()
    assert not radar_data_dir.resolve().is_relative_to((REPO_ROOT / "data").resolve())
    assert get_settings().data_dir == radar_data_dir.resolve()
    assert (radar_data_dir / "radar.db").is_file()


@pytest.mark.parametrize("run", [1, 2])
def test_each_test_gets_a_fresh_database(run: int, radar_data_dir: Path, make_project: Any) -> None:
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 0
    make_project()
    marker = radar_data_dir / "marker-from-previous-test"
    assert not marker.exists()
    marker.write_text("x", encoding="utf-8")


def test_make_project_factory(make_project: Any, assert_no_open_write: Any) -> None:
    pid = make_project(settings={"arxiv_categories": ["cs.IR"], "slate_size": 10}, active=False)
    assert_no_open_write()
    with session_scope() as s:
        p = s.get(models.Project, pid)
        assert p is not None
        assert p.owner_id == 1 and p.active is False
        assert p.settings_json["arxiv_categories"] == ["cs.IR"]
        assert p.settings_json["slate_size"] == 10
        assert p.settings_json["time_window_days"] == 14
        assert p.name.startswith("Test project")
    with pytest.raises(ValidationError):
        make_project(settings={"slate_size": 1})


def test_make_paper_factory(make_paper: Any, assert_no_open_write: Any) -> None:
    pid = make_paper(
        title="Test paper A: Ranking {Synthetic} Things",
        identifiers={"arxiv": "2610.00001", "doi": "10.0000/test.a"},
        authors=["Ada Test", "Grace Example"],
        versions=["v1", "v2", "published"],
        categories=["cs.CL", "cs.IR"],
        abstract=None,
    )
    assert_no_open_write()
    with session_scope() as s:
        paper = s.get(models.Paper, pid)
        assert paper is not None
        assert paper.canonical_title == "Test paper A: Ranking {Synthetic} Things"
        assert paper.title_norm == "test paper a ranking synthetic things"
        assert paper.abstract is None
        assert paper.primary_category == "cs.CL" and paper.categories_json == ["cs.CL", "cs.IR"]
        idents = {(i.scheme, i.value, i.is_primary) for i in paper.identifiers}
        assert idents == {("arxiv", "2610.00001", True), ("doi", "10.0000/test.a", True)}
        assert [a.raw_name for a in paper.paper_authors] == ["Ada Test", "Grace Example"]
        assert {(v.version_label, v.source) for v in paper.versions} == {
            ("v1", "arxiv"),
            ("v2", "arxiv"),
            ("published", "openalex"),
        }
        assert paper.first_seen_at.tzinfo is not None
    other = make_paper()
    with session_scope() as s:
        assert s.get(models.Paper, other).canonical_title.startswith("Test paper")  # type: ignore[union-attr]


def test_assert_no_open_write_detects_open_transaction(assert_no_open_write: Any) -> None:
    with session_scope(write=True) as s:
        s.execute(select(models.User.id)).all()
        assert holding_write_transaction()
        with pytest.raises(AssertionError):
            assert_no_open_write()
    assert_no_open_write()


def test_fixture_helpers(fixture_path: Any, load_fixture: Any) -> None:
    assert fixture_path("arxiv_feed_multi.xml").is_file()
    data = load_fixture("openalex_work_arxiv.json")
    assert isinstance(data, dict)
    xml = load_fixture("arxiv_feed_empty.xml")
    assert isinstance(xml, bytes) and xml.lstrip().startswith(b"<")
    with pytest.raises(FileNotFoundError):
        fixture_path("does-not-exist.json")


def test_http_mock_routing(http_mock: Any) -> None:
    from radar.net import http as net_http

    assert isinstance(net_http.get_http(), net_http.HttpClient)

    http_mock.add("GET", "https://api.openalex.org/works/W1", {"id": "W1"})
    http_mock.add(
        "GET", re.compile(r"^https://export\.arxiv\.org/api/query\?"), b"<feed/>", headers={"x-a": "1"}
    )
    http_mock.add(
        "POST", r"https://api\.semanticscholar\.org/graph/v1/paper/batch", lambda req: [None, {"x": 1}]
    )
    http_mock.add("GET", "https://api.github.com/user", "rate limited", status=429, times=1)
    http_mock.add("GET", "https://api.github.com/user", {"status": "ok"})
    http_mock.add("GET", "https://rss.arxiv.org/atom/cs.IR", httpx.Response(304))

    with httpx.Client(transport=httpx.MockTransport(http_mock.handler)) as raw:
        r = raw.get("https://api.openalex.org/works/W1", params={"select": "id"})
        assert r.status_code == 200 and r.json() == {"id": "W1"}
        r = raw.get("https://export.arxiv.org/api/query", params={"id_list": "2504.08385"})
        assert r.content == b"<feed/>" and r.headers["x-a"] == "1"
        r = raw.post("https://api.semanticscholar.org/graph/v1/paper/batch", json={"ids": ["a", "b"]})
        assert r.json() == [None, {"x": 1}]
        assert raw.get("https://api.github.com/user").status_code == 429
        assert raw.get("https://api.github.com/user").json() == {"status": "ok"}
        assert raw.get("https://api.github.com/user").json() == {"status": "ok"}
        assert raw.get("https://rss.arxiv.org/atom/cs.IR").status_code == 304
        assert raw.get("https://rss.arxiv.org/atom/cs.IR").status_code == 304
        missing = raw.get("https://example.org/unmocked")
        assert missing.status_code == 599
        assert raw.delete("https://api.openalex.org/works/W1").status_code == 599

    assert len(http_mock.requests) == 10
    assert [str(r.url) for r in http_mock.unmatched] == [
        "https://example.org/unmocked",
        "https://api.openalex.org/works/W1",
    ]
    assert len(http_mock.calls("GET", "https://api.github.com/user")) == 3
    assert len(http_mock.calls(url_or_regex=re.compile("openalex"))) == 2
    batch = http_mock.calls("POST")[0]
    assert batch.read() == b'{"ids":["a","b"]}'


def test_set_env_fixture(set_env: Any, make_project: Any) -> None:
    make_project()
    set_env(OPENALEX_API_KEY="zq-key", RADAR_OFFLINE="0")
    assert get_settings().openalex_api_key == "zq-key"
    assert get_settings().offline is False
    with session_scope() as s:  # same per-test database after the engine reset
        assert s.scalar(select(func.count()).select_from(models.Project)) == 1
    set_env(OPENALEX_API_KEY=None)
    assert get_settings().openalex_api_key is None


def test_env_restored_after_set_env() -> None:
    assert os.environ["RADAR_OFFLINE"] == "1"
    assert "OPENALEX_API_KEY" not in os.environ
