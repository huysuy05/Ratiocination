"""Request-scoped sessions and request helpers (radar.api.deps)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import db, models
from radar.api.deps import (
    ReadSession,
    WriteSession,
    decode_cursor,
    encode_cursor,
    get_project_or_404,
    split_csv,
)
from radar.errors import ApiError


def _concepts() -> int:
    with db.session_scope() as s:
        return int(s.scalar(select(func.count()).select_from(models.Concept)) or 0)


def _app_with_routes() -> FastAPI:
    from radar.main import create_app

    app = create_app()

    def write_ok(session: WriteSession) -> dict[str, int]:
        session.add(models.Concept(canonical_label="Test", ontology_source="user", external_id="user:test"))
        session.flush()
        return {"ok": 1}

    def write_then_fail(session: WriteSession) -> dict[str, int]:
        session.add(models.Concept(canonical_label="Test", ontology_source="user", external_id="user:test"))
        session.flush()
        raise ApiError(409, "conflict", "Nope")

    def commit_fails(session: WriteSession) -> dict[str, int]:
        # Both rows reach the database only at commit time -> IntegrityError during commit.
        session.add(models.Concept(canonical_label="A", ontology_source="user", external_id="user:dup"))
        session.add(models.Concept(canonical_label="B", ontology_source="user", external_id="user:dup"))
        return {"ok": 1}

    def read_then_write(session: ReadSession) -> dict[str, int]:
        session.add(models.Concept(canonical_label="Test", ontology_source="user", external_id="user:test"))
        session.flush()
        return {"ok": 1}

    def shared(a: ReadSession, b: ReadSession) -> dict[str, bool]:
        return {"same": a is b}

    for path, fn, method in [
        ("/api/_test/write", write_ok, "POST"),
        ("/api/_test/write-fail", write_then_fail, "POST"),
        ("/api/_test/commit-fails", commit_fails, "POST"),
        ("/api/_test/read-write", read_then_write, "POST"),
        ("/api/_test/shared", shared, "GET"),
    ]:
        app.add_api_route(path, fn, methods=[method])
    return app


class _CommitProbe:
    """ASGI wrapper that records whether the row is committed when the response starts."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.seen_at_start: list[int] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def probe_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.seen_at_start.append(_concepts())
            await send(message)

        await self.app(scope, receive, probe_send)


def test_write_session_commits_before_response_starts() -> None:
    probe = _CommitProbe(_app_with_routes())
    with TestClient(probe, base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}) as c:
        r = c.post("/api/_test/write")
        assert r.status_code == 200
    assert probe.seen_at_start == [1]
    assert not db._holders, "write-holder flag leaked across threads"


def test_write_session_rolls_back_on_error() -> None:
    with TestClient(
        _app_with_routes(), base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}
    ) as c:
        r = c.post("/api/_test/write-fail")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
    assert _concepts() == 0
    assert not db._holders


def test_commit_failure_is_a_500_not_a_silent_success() -> None:
    with TestClient(
        _app_with_routes(), base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}
    ) as c:
        r = c.post("/api/_test/commit-fails")
        assert r.status_code == 500
        assert r.json()["error"]["code"] == "internal_error"
    assert _concepts() == 0
    assert not db._holders


def test_read_session_cannot_write() -> None:
    with TestClient(
        _app_with_routes(), base_url="http://127.0.0.1:8000", headers={"X-Radar-Client": "web"}
    ) as c:
        r = c.post("/api/_test/read-write")
        assert r.status_code == 500
    assert _concepts() == 0


def test_sessions_are_shared_within_a_request() -> None:
    with TestClient(_app_with_routes(), base_url="http://127.0.0.1:8000") as c:
        assert c.get("/api/_test/shared").json() == {"same": True}


def test_get_project_or_404(make_project: Any) -> None:
    pid = make_project()
    with db.session_scope() as s:
        assert get_project_or_404(s, pid, 1).id == pid
        with pytest.raises(ApiError) as info:
            get_project_or_404(s, pid + 1000, 1)
        assert (info.value.status, info.value.code) == (404, "not_found")
        with pytest.raises(ApiError) as info:
            get_project_or_404(s, pid, owner_id=2)  # another user's project looks missing
        assert info.value.status == 404


def test_cursor_round_trip_and_errors() -> None:
    payload = {"u": "2026-10-12T00:00:00Z", "i": 42}
    token = encode_cursor(payload)
    assert "=" not in token and "/" not in token and "+" not in token
    assert decode_cursor(token) == payload
    assert decode_cursor(None) is None and decode_cursor("") is None
    for bad in ("%%%", "bm90IGpzb24", encode_cursor({"x": 1})[:-2] + "!!", "WzEsMl0"):  # "WzEsMl0" = [1,2]
        with pytest.raises(ApiError) as info:
            decode_cursor(bad)
        assert info.value.status == 422
        assert info.value.details == {"fields": [{"path": "cursor", "message": "Invalid cursor"}]}


def test_split_csv() -> None:
    assert split_csv(None, default=["relevant", "saved"]) == ["relevant", "saved"]
    assert split_csv(" relevant, saved,,relevant ") == ["relevant", "saved"]
    assert split_csv("saved", allowed={"saved", "pinned"}, param="states") == ["saved"]
    with pytest.raises(ApiError) as info:
        split_csv("saved,zzz", allowed={"saved"}, param="states")
    assert info.value.details["fields"][0]["path"] == "states"  # type: ignore[index]


def test_session_types() -> None:
    assert ReadSession.__metadata__[0].dependency.__name__ == "get_db"  # type: ignore[attr-defined]
    assert WriteSession.__metadata__[0].dependency.__name__ == "get_write_db"  # type: ignore[attr-defined]
    assert Session is ReadSession.__origin__  # type: ignore[attr-defined]
