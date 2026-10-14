"""Projects CRUD, settings validation, deck builds and ProjectStats (docs/API.md "Projects")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from radar import models
from radar.db import session_scope

NEW_PROJECT = {"name": "Test project", "research_question": "How are synthetic test papers ranked?"}

DEFAULT_SETTINGS = {"arxiv_categories": [], "time_window_days": 14, "slate_size": 20}

ZERO_STATS = {
    "seeds": 0,
    "documents": 0,
    "relevant": 0,
    "saved": 0,
    "not_relevant": 0,
    "remaining_in_deck": 0,
    "last_deck_at": None,
}


def _create(client: TestClient, **extra: Any) -> dict[str, Any]:
    r = client.post("/api/projects", json={**NEW_PROJECT, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _fields(resp: Any) -> dict[str, str]:
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    return {f["path"]: f["message"] for f in err["details"]["fields"]}


def _deck_jobs(project_id: int) -> list[models.Job]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(models.Job).where(models.Job.kind == "build_deck", models.Job.project_id == project_id)
            )
        )


# ------------------------------------------------------------------------------------------------
# Create / read
# ------------------------------------------------------------------------------------------------


def test_create_project_defaults(client: TestClient) -> None:
    body = _create(client)
    assert body["name"] == NEW_PROJECT["name"]
    assert body["research_question"] == NEW_PROJECT["research_question"]
    assert body["description"] == ""
    assert body["active"] is True
    assert body["settings"] == DEFAULT_SETTINGS
    assert body["stats"] == ZERO_STATS
    assert "hidden_topics" not in body
    assert body["created_at"].endswith("Z") and body["updated_at"].endswith("Z")
    assert client.get(f"/api/projects/{body['id']}").json() == body


def test_create_project_with_partial_settings_and_inactive(client: TestClient) -> None:
    body = _create(
        client,
        description="  A synthetic description  ",
        active=False,
        settings={
            "arxiv_categories": ["cs.IR", "hep-th", "math.GT", "cs.IR"],
            "slate_size": 10,
            "exploration_slots": 3,  # a v0.2 setting: ignored
        },
    )
    assert body["active"] is False
    assert body["description"] == "A synthetic description"
    assert body["settings"] == {
        "arxiv_categories": ["cs.IR", "hep-th", "math.GT"],
        "slate_size": 10,
        "time_window_days": 14,
    }
    assert _deck_jobs(body["id"]) == []  # inactive: no deck is built


def test_create_requires_name_and_question(client: TestClient) -> None:
    fields = _fields(client.post("/api/projects", json={"name": "   ", "research_question": ""}))
    assert {"name", "research_question"} <= set(fields)
    fields = _fields(client.post("/api/projects", json={"research_question": "q"}))
    assert "name" in fields
    assert "name" in _fields(client.post("/api/projects", json={**NEW_PROJECT, "name": "x" * 201}))
    assert "research_question" in _fields(
        client.post("/api/projects", json={**NEW_PROJECT, "research_question": "q" * 4001})
    )
    assert "description" in _fields(
        client.post("/api/projects", json={**NEW_PROJECT, "description": "d" * 4001})
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("time_window_days", 0),
        ("time_window_days", 91),
        ("slate_size", 4),
        ("slate_size", 51),
        ("slate_size", 10.5),
        ("arxiv_categories", [f"cs.X{chr(65 + i)}" for i in range(21)]),
    ],
)
def test_settings_bounds(client: TestClient, key: str, value: Any) -> None:
    fields = _fields(client.post("/api/projects", json={**NEW_PROJECT, "settings": {key: value}}))
    assert any(path == f"settings.{key}" or path.startswith(f"settings.{key}.") for path in fields), fields


@pytest.mark.parametrize("category", ["CS.IR", "cs IR", "cs.", ".IR", "cs.IR.x", "cs_IR", ""])
def test_category_pattern(client: TestClient, category: str) -> None:
    fields = _fields(
        client.post("/api/projects", json={**NEW_PROJECT, "settings": {"arxiv_categories": [category]}})
    )
    assert "settings.arxiv_categories.0" in fields


def test_settings_bounds_inclusive(client: TestClient) -> None:
    body = _create(client, settings={"time_window_days": 90, "slate_size": 50})
    assert body["settings"]["slate_size"] == 50 and body["settings"]["time_window_days"] == 90
    body = _create(client, settings={"slate_size": 5, "time_window_days": 1})
    assert body["settings"]["slate_size"] == 5


def test_project_quota(client: TestClient, set_env: Any) -> None:
    set_env(RADAR_MAX_PROJECTS="2")
    _create(client)
    _create(client)
    r = client.post("/api/projects", json=NEW_PROJECT)
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"


def test_active_projects_with_categories_get_a_first_deck(client: TestClient) -> None:
    without = _create(client)
    assert _deck_jobs(without["id"]) == []  # no categories: nothing to rank yet
    project = _create(client, settings={"arxiv_categories": ["cs.IR"]})
    (job,) = _deck_jobs(project["id"])
    assert job.params_json == {"mode": "daily", "prepare": True} and job.owner_id == models.LOCAL_USER_ID
    pid = without["id"]
    client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})
    assert _deck_jobs(pid) == []
    client.patch(f"/api/projects/{pid}", json={"settings": {"arxiv_categories": ["cs.DL"]}})
    assert len(_deck_jobs(pid)) == 1  # categories changed: rebuild with a fresh backfill


def test_list_projects_newest_first(client: TestClient) -> None:
    ids = [_create(client, name=f"Test project {i}")["id"] for i in range(3)]
    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == list(reversed(ids))


def test_get_unknown_project_is_404(client: TestClient) -> None:
    r = client.get("/api/projects/999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert client.patch("/api/projects/999", json={"name": "x"}).status_code == 404


# ------------------------------------------------------------------------------------------------
# Update
# ------------------------------------------------------------------------------------------------


def test_patch_fields_and_settings_merge_key_by_key(client: TestClient) -> None:
    project = _create(client, settings={"arxiv_categories": ["cs.IR"], "slate_size": 12})
    pid = project["id"]
    r = client.patch(f"/api/projects/{pid}", json={"settings": {"time_window_days": 30}})
    assert r.status_code == 200, r.text
    settings = r.json()["settings"]
    assert settings["time_window_days"] == 30
    assert settings["slate_size"] == 12
    assert settings["arxiv_categories"] == ["cs.IR"]
    r = client.patch(f"/api/projects/{pid}", json={"name": "Renamed test project", "active": False})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed test project"
    assert body["active"] is False
    assert body["settings"]["time_window_days"] == 30
    assert body["research_question"] == NEW_PROJECT["research_question"]


def test_patch_validates_merged_settings(client: TestClient) -> None:
    pid = _create(client)["id"]
    assert client.patch(f"/api/projects/{pid}", json={"settings": {"slate_size": 5}}).status_code == 200
    after = client.get(f"/api/projects/{pid}").json()["settings"]
    assert after["slate_size"] == 5
    # explicit null for a non-nullable setting
    assert "settings.slate_size" in _fields(
        client.patch(f"/api/projects/{pid}", json={"settings": {"slate_size": None}})
    )
    assert "settings.slate_size" in _fields(
        client.patch(f"/api/projects/{pid}", json={"settings": {"slate_size": 99}})
    )


def test_patch_rejects_null_fields(client: TestClient) -> None:
    pid = _create(client)["id"]
    for key in ("name", "research_question", "description", "active", "settings"):
        assert key in _fields(client.patch(f"/api/projects/{pid}", json={key: None})), key


def test_patch_empty_body_is_noop(client: TestClient) -> None:
    project = _create(client)
    r = client.patch(f"/api/projects/{project['id']}", json={})
    assert r.status_code == 200
    assert r.json()["settings"] == project["settings"]
    assert r.json()["name"] == project["name"]


# ------------------------------------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------------------------------------


def test_project_stats(client: TestClient, make_project: Any, make_paper: Any) -> None:
    pid = make_project()
    other = make_project()
    papers = [make_paper() for _ in range(10)]
    now = datetime.now(UTC)
    states = [(0, "relevant"), (1, "relevant"), (2, "saved"), (5, "not_relevant"), (7, None)]
    with session_scope(write=True) as s:
        s.add(models.ProjectSeed(project_id=pid, paper_id=papers[8], weight=1.0, added_via="doi"))
        s.add(models.ProjectSeed(project_id=pid, paper_id=papers[9], weight=1.0, added_via="arxiv"))
        s.add(models.ProjectSeed(project_id=other, paper_id=papers[9], weight=1.0, added_via="arxiv"))
        s.add(
            models.Document(
                project_id=pid,
                filename="test-document.txt",
                mime_type="text/plain",
                size_bytes=10,
                checksum="0" * 64,
                storage_uri="uploads/1/x.txt",
                status="ready",
            )
        )
        for index, state in states:
            s.add(
                models.ProjectPaperState(
                    project_id=pid,
                    paper_id=papers[index],
                    state=state,
                    updated_at=now,
                    last_interaction_id=None,
                )
            )
        s.add(models.ProjectPaperState(project_id=other, paper_id=papers[0], state="saved", updated_at=now))
        older = models.RecommendationSlate(
            project_id=pid,
            generated_at=now - timedelta(days=1),
            mode="daily",
            ranker_version="test",
            embedding_model="local/hashing-v1",
            candidate_set_hash="a" * 64,
            pool_size=3,
        )
        latest = models.RecommendationSlate(
            project_id=pid,
            generated_at=now - timedelta(hours=1),
            mode="daily",
            ranker_version="test",
            embedding_model="local/hashing-v1",
            candidate_set_hash="b" * 64,
            pool_size=3,
        )
        s.add_all([older, latest])
        s.flush()
        for rank, paper_index in enumerate([0, 7, 8, 9], start=1):
            s.add(
                models.RecommendationItem(
                    slate_id=latest.id, paper_id=papers[paper_index], rank=rank, score=0.5
                )
            )
        s.add(models.RecommendationItem(slate_id=older.id, paper_id=papers[1], rank=1, score=0.5))
        latest_at = latest.generated_at

    stats = client.get(f"/api/projects/{pid}").json()["stats"]
    assert stats == {
        "seeds": 2,
        "documents": 1,
        "relevant": 2,
        "saved": 1,
        "not_relevant": 1,
        "remaining_in_deck": 3,  # papers 7 (state null), 8, 9
        "last_deck_at": latest_at.isoformat().replace("+00:00", "Z"),
    }
    listed = {p["id"]: p["stats"] for p in client.get("/api/projects").json()}
    assert listed[pid] == stats
    assert listed[other]["seeds"] == 1 and listed[other]["saved"] == 1 and listed[other]["relevant"] == 0


# ------------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------------


def test_delete_project_calls_private_data_cleanup_and_cascades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, make_paper: Any
) -> None:
    from radar.documents import service

    calls: list[int] = []

    def fake_cleanup(session: Any, project_id: int) -> dict[str, int]:
        assert session is not None
        calls.append(project_id)
        return {"documents": 0}

    monkeypatch.setattr(service, "delete_project_private_data", fake_cleanup)
    pid = _create(client)["id"]
    keep = _create(client, name="Other test project")["id"]
    paper = make_paper()
    with session_scope(write=True) as s:
        s.add(models.ProjectSeed(project_id=pid, paper_id=paper, weight=1.0, added_via="doi"))
        s.add(models.ProjectPaperState(project_id=pid, paper_id=paper, state="relevant"))
        s.add(models.Interaction(project_id=pid, paper_id=paper, action="relevant"))
        s.add(models.ProjectSeed(project_id=keep, paper_id=paper, weight=1.0, added_via="doi"))

    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 204
    assert r.content == b""
    assert calls == [pid]
    assert client.get(f"/api/projects/{pid}").status_code == 404
    with session_scope() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(models.ProjectSeed)
                .where(models.ProjectSeed.project_id == pid)
            )
            == 0
        )
        assert s.scalar(select(func.count()).select_from(models.ProjectPaperState)) == 0
        assert s.scalar(select(func.count()).select_from(models.Interaction)) == 0
        assert s.scalar(select(func.count()).select_from(models.ProjectSeed)) == 1
        assert s.get(models.Paper, paper) is not None
    assert client.get(f"/api/projects/{keep}").status_code == 200


def test_delete_unknown_project_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from radar.documents import service

    called: list[int] = []
    monkeypatch.setattr(service, "delete_project_private_data", lambda s, pid: called.append(pid) or {})
    r = client.delete("/api/projects/12345")
    assert r.status_code == 404
    assert called == []


def test_delete_rolls_back_when_cleanup_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from radar.documents import service

    def failing(session: Any, project_id: int) -> dict[str, int]:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(service, "delete_project_private_data", failing)
    pid = _create(client)["id"]
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert client.get(f"/api/projects/{pid}").status_code == 200


def test_writes_are_committed_before_the_response(client: TestClient, assert_no_open_write: Any) -> None:
    pid = _create(client)["id"]
    # visible from an independent session immediately after the response
    with session_scope() as s:
        assert s.get(models.Project, pid) is not None
    assert_no_open_write()
