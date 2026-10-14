"""Jobs API (docs/API.md): GET /api/jobs, GET /api/jobs/{id}, POST /api/jobs/run."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from radar import models
from radar.db import session_scope
from radar.jobs import registry, runner
from radar.net import http as net_http

JOB_KEYS = {
    "id",
    "kind",
    "status",
    "project_id",
    "params",
    "stats",
    "progress",
    "message",
    "error_code",
    "error_message",
    "created_at",
    "started_at",
    "finished_at",
}


@pytest.fixture
def kinds() -> Iterator[Callable[[str, Callable[..., Any]], None]]:
    names: list[str] = []

    def _register(kind: str, fn: Callable[..., Any]) -> None:
        registry.register(kind, fn)
        names.append(kind)

    yield _register
    for name in names:
        registry.unregister(name)


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture
def executor_client(set_env: Callable[..., None], gate: threading.Event) -> Iterator[TestClient]:
    """The app with RADAR_JOBS_INLINE=0 (jobs run on the executor); waits for jobs at teardown."""
    from radar.main import create_app

    set_env(RADAR_JOBS_INLINE="0")
    with TestClient(create_app(), base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}) as c:
        yield c
        gate.set()
        assert runner.wait_idle(timeout=15)


def add_job(kind: str, *, created_at: datetime, project_id: int | None = None, **values: Any) -> int:
    with session_scope(write=True) as s:
        job = models.Job(
            kind=kind,
            dedupe_key=f"test-{kind}-{created_at.isoformat()}",
            created_at=created_at,
            project_id=project_id,
            status=values.pop("status", "succeeded"),
            **values,
        )
        s.add(job)
        s.flush()
        return job.id


def job_total() -> int:
    with session_scope() as s:
        return int(s.scalar(select(func.count()).select_from(models.Job)) or 0)


def error(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body = resp.json()["error"]
    assert body["code"] == code
    return body


# ------------------------------------------------------------------------------------------------
# GET
# ------------------------------------------------------------------------------------------------


def test_list_jobs_empty(client: TestClient) -> None:
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_newest_first_with_filters(client: TestClient, make_project: Callable[..., int]) -> None:
    project_id = make_project()
    base = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    enrich = add_job("enrich", created_at=base)
    deck = add_job(
        "build_deck",
        created_at=base + timedelta(minutes=1),
        project_id=project_id,
        params_json={"mode": "daily"},
    )
    expand = add_job("expand", created_at=base + timedelta(minutes=2), params_json={"paper_id": 3})
    embed = add_job("embed", created_at=base + timedelta(minutes=3), status="running")
    add_job("test_internal", created_at=base + timedelta(minutes=4))  # not an API kind: never listed

    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert ids == [embed, expand, deck, enrich]
    assert [j["id"] for j in client.get("/api/jobs", params={"limit": 2}).json()] == [embed, expand]
    assert [j["id"] for j in client.get("/api/jobs", params={"kind": "build_deck"}).json()] == [deck]
    assert [j["id"] for j in client.get("/api/jobs", params={"project_id": project_id}).json()] == [deck]
    assert client.get("/api/jobs", params={"kind": "daily"}).json() == []
    for bad in ({"limit": 0}, {"limit": 201}, {"kind": "bogus"}, {"project_id": "x"}):
        error(client.get("/api/jobs", params=bad), 422, "validation_error")


def test_get_job_shape(client: TestClient, make_project: Callable[..., int]) -> None:
    project_id = make_project()
    created = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    job_id = add_job(
        "build_deck",
        created_at=created,
        project_id=project_id,
        status="failed",
        params_json={"mode": "daily", "prepare": True},
        stats_json={"pool_size": 12},
        progress=0.5,
        message="Ranking 12 candidates",
        error_code="source_unavailable",
        error_message="arxiv unavailable (HTTP 503)",
        started_at=created + timedelta(seconds=1),
        finished_at=created + timedelta(seconds=2),
    )
    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == JOB_KEYS
    assert body == {
        "id": job_id,
        "kind": "build_deck",
        "status": "failed",
        "project_id": project_id,
        "params": {"mode": "daily", "prepare": True},
        "stats": {"pool_size": 12},
        "progress": 0.5,
        "message": "Ranking 12 candidates",
        "error_code": "source_unavailable",
        "error_message": "arxiv unavailable (HTTP 503)",
        "created_at": "2026-10-12T08:00:00Z",
        "started_at": "2026-10-12T08:00:01Z",
        "finished_at": "2026-10-12T08:00:02Z",
    }


def test_get_job_not_found(client: TestClient) -> None:
    error(client.get("/api/jobs/987654"), 404, "not_found")
    internal = add_job("test_internal", created_at=datetime(2026, 10, 12, tzinfo=UTC))
    error(client.get(f"/api/jobs/{internal}"), 404, "not_found")


# ------------------------------------------------------------------------------------------------
# POST /api/jobs/run
# ------------------------------------------------------------------------------------------------


def test_run_job_returns_202_with_the_stored_job(client: TestClient, kinds: Callable[..., None]) -> None:
    calls: list[dict[str, Any]] = []

    def fake_enrich(
        ctx: runner.JobContext, paper_ids: list[int] | None = None, limit: int = 300
    ) -> dict[str, Any]:
        calls.append({"paper_ids": paper_ids, "limit": limit, "project_id": ctx.project_id})
        ctx.progress(0.5, "Enriching 5 papers")
        return {"enriched": 5}

    kinds("enrich", fake_enrich)
    resp = client.post("/api/jobs/run", json={"kind": "enrich", "params": {"limit": 5}})
    assert resp.status_code == 202, resp.text
    job = resp.json()["job"]
    assert set(job) == JOB_KEYS
    assert (job["kind"], job["status"], job["project_id"]) == ("enrich", "succeeded", None)  # inline in tests
    assert job["params"] == {"limit": 5} and job["stats"] == {"enriched": 5}
    assert job["progress"] == 1.0 and job["message"] == "Enriching 5 papers"
    assert job["created_at"].endswith("Z") and job["finished_at"].endswith("Z")
    assert calls == [{"paper_ids": None, "limit": 5, "project_id": None}]
    assert client.get(f"/api/jobs/{job['id']}").json() == job


def test_run_job_with_a_project(
    client: TestClient, kinds: Callable[..., None], make_project: Callable[..., int]
) -> None:
    project_id = make_project()
    seen: list[tuple[int | None, str]] = []
    kinds("build_deck", lambda ctx, mode="daily": seen.append((ctx.project_id, mode)) or {"items": 0})
    resp = client.post(
        "/api/jobs/run", json={"kind": "build_deck", "project_id": project_id, "params": {"mode": "more"}}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["job"]["project_id"] == project_id
    assert seen == [(project_id, "more")]


def test_run_job_failure_is_visible_on_the_job(client: TestClient, kinds: Callable[..., None]) -> None:
    exc = net_http.SourceUnavailable("arxiv", 503)

    def failing(ctx: runner.JobContext) -> None:
        raise exc

    kinds("ingest_feeds", failing)
    resp = client.post("/api/jobs/run", json={"kind": "ingest_feeds"})
    assert resp.status_code == 202
    job = resp.json()["job"]
    assert (job["status"], job["error_code"], job["error_message"]) == (
        "failed",
        "source_unavailable",
        str(exc),
    )


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"kind": "bogus"}, "kind"),
        ({"kind": "build_deck"}, "project_id"),
        ({"kind": "backfill"}, "project_id"),
        ({"kind": "expand", "params": {}}, "params.paper_id"),
        ({"kind": "expand", "params": {"paper_id": "3"}}, "params.paper_id"),
        ({"kind": "enrich", "params": {"bogus": 1}}, "params.bogus"),
        ({"kind": "enrich", "params": {"project_id": 1}}, "params.project_id"),
        ({"kind": "enrich", "params": {"limit": "5"}}, "params.limit"),
        ({"kind": "process_document", "params": {}}, "params.document_id"),
        ({"kind": "download_model", "params": {"model_id": 3}}, "params.model_id"),
    ],
)
def test_run_job_validation(client: TestClient, body: dict[str, Any], path: str) -> None:
    err = error(client.post("/api/jobs/run", json=body), 422, "validation_error")
    assert path in [f["path"] for f in err["details"]["fields"]]
    assert job_total() == 0


def test_run_job_rejects_non_json_numbers(client: TestClient, kinds: Callable[..., None]) -> None:
    kinds("enrich", lambda ctx, **params: params)  # accepts any parameter name
    for raw in (
        '{"kind": "enrich", "params": {"x": NaN}}',
        '{"kind": "enrich", "params": {"limit": Infinity}}',
    ):
        resp = client.post("/api/jobs/run", content=raw, headers={"Content-Type": "application/json"})
        err = error(resp, 422, "validation_error")
        assert [f["path"] for f in err["details"]["fields"]] in (["params"], ["params.limit"])
    assert job_total() == 0


def test_run_build_deck_validates_mode_and_project(
    client: TestClient, make_project: Callable[..., int]
) -> None:
    project_id = make_project()
    err = error(
        client.post(
            "/api/jobs/run",
            json={"kind": "build_deck", "project_id": project_id, "params": {"mode": "weekly"}},
        ),
        422,
        "validation_error",
    )
    assert [f["path"] for f in err["details"]["fields"]] == ["params.mode"]
    error(client.post("/api/jobs/run", json={"kind": "build_deck", "project_id": 424242}), 404, "not_found")
    error(client.post("/api/jobs/run", json={"kind": "enrich", "project_id": 424242}), 404, "not_found")
    assert job_total() == 0


def test_run_job_dedupes_to_the_active_job(
    executor_client: TestClient, kinds: Callable[..., None], gate: threading.Event
) -> None:
    kinds("enrich", lambda ctx, paper_ids=None, limit=300: {"ok": gate.wait(10)})
    first = executor_client.post("/api/jobs/run", json={"kind": "enrich", "params": {"limit": 5}})
    second = executor_client.post("/api/jobs/run", json={"kind": "enrich", "params": {"limit": 5}})
    other = executor_client.post("/api/jobs/run", json={"kind": "enrich", "params": {"limit": 6}})
    assert first.status_code == second.status_code == other.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"] != other.json()["job"]["id"]
    assert second.json()["job"]["status"] in ("queued", "running")
    gate.set()
    assert runner.wait_idle(timeout=10)
    assert executor_client.get(f"/api/jobs/{first.json()['job']['id']}").json()["status"] == "succeeded"


def test_run_build_deck_with_another_mode_active_is_a_conflict(
    executor_client: TestClient,
    kinds: Callable[..., None],
    gate: threading.Event,
    make_project: Callable[..., int],
) -> None:
    project_id = make_project()
    kinds("build_deck", lambda ctx, mode="daily", prepare=False: {"ok": gate.wait(10)})
    daily = executor_client.post("/api/jobs/run", json={"kind": "build_deck", "project_id": project_id})
    assert daily.status_code == 202
    job_id = daily.json()["job"]["id"]
    same = executor_client.post(
        "/api/jobs/run", json={"kind": "build_deck", "project_id": project_id, "params": {"mode": "daily"}}
    )
    assert same.status_code == 202 and same.json()["job"]["id"] == job_id
    err = error(
        executor_client.post(
            "/api/jobs/run",
            json={"kind": "build_deck", "project_id": project_id, "params": {"mode": "more"}},
        ),
        409,
        "conflict",
    )
    assert err["details"]["job"]["id"] == job_id
    assert set(err["details"]["job"]) == JOB_KEYS
    assert job_total() == 1


def test_run_job_requires_the_client_header(client: TestClient) -> None:
    resp = client.post("/api/jobs/run", json={"kind": "enrich"}, headers={"X-Radar-Client": ""})
    error(resp, 403, "unauthorized")
    assert job_total() == 0
