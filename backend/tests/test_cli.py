"""CLI (``python -m radar.cli``): argument parsing, job commands, exit codes, serve wiring."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from radar import cli, models
from radar.db import session_scope
from radar.jobs import registry, runner
from radar.net import http as net_http

BACKEND_DIR = Path(__file__).resolve().parents[1]

Calls = list[tuple[str, int | None, dict[str, Any]]]


@pytest.fixture
def fake_jobs() -> Iterator[Calls]:
    """Fake functions for every built-in kind, recording (kind, ctx.project_id, params)."""
    calls: Calls = []

    def make(kind: str) -> Callable[..., dict[str, Any]]:
        def fn(ctx: runner.JobContext, **params: Any) -> dict[str, Any]:
            calls.append((kind, ctx.project_id, params))
            return {"fake": kind}

        return fn

    for kind in registry.REGISTRY:
        registry.register(kind, make(kind))
    yield calls
    registry.reset_overrides()


def summary(out: str) -> dict[str, Any]:
    return json.loads(out[out.index("{") :])


def jobs_of(kind: str) -> list[models.Job]:
    with session_scope() as s:
        return list(s.scalars(select(models.Job).where(models.Job.kind == kind).order_by(models.Job.id)))


@pytest.mark.parametrize(
    ("argv", "kind", "with_project", "params"),
    [
        (["daily"], "daily", False, {}),
        (["ingest-feeds"], "ingest_feeds", False, {}),
        (["backfill", "--project", "{P}"], "backfill", True, {}),
        (["enrich"], "enrich", False, {}),
        (["embed"], "embed", False, {}),
        (["build-deck", "--project", "{P}"], "build_deck", True, {"mode": "daily"}),
        (
            ["build-deck", "--project", "{P}", "--mode", "more", "--prepare"],
            "build_deck",
            True,
            {"mode": "more", "prepare": True},
        ),
        (["expand", "--paper", "5"], "expand", False, {"paper_id": 5}),
        (
            ["expand", "--paper", "5", "--project", "{P}", "--force"],
            "expand",
            True,
            {"paper_id": 5, "force": True},
        ),
        (["download-model"], "download_model", False, {}),
        (
            ["download-model", "--model", "local/hashing-v1"],
            "download_model",
            False,
            {"model_id": "local/hashing-v1"},
        ),
    ],
)
def test_job_commands(
    argv: list[str],
    kind: str,
    with_project: bool,
    params: dict[str, Any],
    fake_jobs: Calls,
    make_project: Callable[..., int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_id = make_project()
    args = [a.replace("{P}", str(project_id)) for a in argv]
    assert cli.main(args) == 0
    expected_project = project_id if with_project else None
    assert fake_jobs == [(kind, expected_project, params)]
    [job] = jobs_of(kind)
    assert (job.status, job.project_id, job.params_json) == ("succeeded", expected_project, params)
    out = summary(capsys.readouterr().out)
    assert out == {
        "kind": kind,
        "job_id": job.id,
        "status": "succeeded",
        "stats": {"fake": kind},
        "followup_jobs": [],
    }


def test_failed_job_exits_1_with_the_job_error(capsys: pytest.CaptureFixture[str]) -> None:
    def failing(ctx: runner.JobContext) -> None:
        ctx.log_stat("fetched", 0)
        raise net_http.BudgetExceeded("openalex")

    registry.register("enrich", failing)
    try:
        assert cli.main(["enrich"]) == 1
    finally:
        registry.unregister("enrich")
    [job] = jobs_of("enrich")
    captured = capsys.readouterr()
    message = str(net_http.BudgetExceeded("openalex"))
    assert f"enrich failed (job {job.id}): budget_exceeded: {message}" in captured.err
    out = summary(captured.out)
    assert (out["status"], out["job_id"], out["error_code"], out["stats"]) == (
        "failed",
        job.id,
        "budget_exceeded",
        {"fetched": 0},
    )


def test_not_implemented_job_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    def stub(ctx: runner.JobContext) -> None:
        raise NotImplementedError("owned by someone")

    registry.register("embed", stub)
    try:
        assert cli.main(["embed"]) == 1
    finally:
        registry.unregister("embed")
    assert "not_implemented" in capsys.readouterr().err


def test_identical_active_job_exits_1(fake_jobs: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    key = runner.dedupe_key("ingest_feeds", None, {})
    with session_scope(write=True) as s:
        job = models.Job(kind="ingest_feeds", dedupe_key=key, status="running", worker_pid=os.getppid())
        s.add(job)
        s.flush()
        other = job.id
    assert cli.main(["ingest-feeds"]) == 1
    assert fake_jobs == []
    captured = capsys.readouterr()
    assert "already queued or running" in captured.err
    out = summary(captured.out)
    assert (out["status"], out["job_id"]) == ("conflict", other)


@pytest.mark.parametrize(
    "argv",
    [
        ["build-deck", "--project", "424242"],
        ["backfill", "--project", "424242"],
        ["expand", "--paper", "1", "--project", "424242"],
    ],
)
def test_unknown_project_exits_1_without_a_job(
    argv: list[str], fake_jobs: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 1
    assert "project 424242 not found" in capsys.readouterr().err
    assert fake_jobs == []
    with session_scope() as s:
        assert s.scalars(select(models.Job)).first() is None


@pytest.mark.parametrize("inline", ["1", "0"])
def test_followup_jobs_finish_before_the_command_returns(
    inline: str,
    set_env: Callable[..., None],
    make_project: Callable[..., int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_env(RADAR_JOBS_INLINE=inline)
    projects = [make_project(), make_project()]
    built: list[int | None] = []

    def fake_daily(ctx: runner.JobContext) -> dict[str, Any]:
        for project_id in projects:
            runner.submit_job("build_deck", project_id=project_id, params={"mode": "daily"})
        return {"submitted": len(projects)}

    registry.register("daily", fake_daily)
    registry.register("build_deck", lambda ctx, mode="daily": built.append(ctx.project_id) or {"mode": mode})
    try:
        assert cli.main(["daily"]) == 0
    finally:
        registry.reset_overrides()
    assert sorted(p for p in built if p is not None) == sorted(projects)
    out = summary(capsys.readouterr().out)
    assert out["status"] == "succeeded" and out["stats"] == {"submitted": 2}
    followups = out["followup_jobs"]
    assert [(j["kind"], j["status"]) for j in followups] == [("build_deck", "succeeded")] * 2
    assert sorted(j["project_id"] for j in followups) == sorted(projects)
    assert runner.wait_idle(timeout=0)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["no-such-command"],
        ["backfill"],
        ["build-deck"],
        ["build-deck", "--project", "1", "--mode", "weekly"],
        ["build-deck", "--project", "x"],
        ["expand"],
        ["build-deck", "--project", "1", "--mode", "initial"],  # removed in v0.3
        ["eval-replay", "--project", "1"],  # removed in v0.3
    ],
)
def test_argument_errors_exit_2(
    argv: list[str], fake_jobs: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    assert fake_jobs == []


def test_serve_runs_uvicorn_in_factory_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured.update(app=app, **kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert cli.main(["serve", "--port", "8123"]) == 0
    assert captured["app"] == "radar.main:create_app"
    assert captured["factory"] is True and captured["workers"] == 1
    assert (captured["host"], captured["port"]) == ("127.0.0.1", 8123)
    assert captured["log_config"]["version"] == 1
    assert "uvicorn.access" in captured["log_config"]["loggers"]


def test_init_db(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init-db"]) == 0
    assert "database ready" in capsys.readouterr().out


def test_module_entry_point_help() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "radar.cli", "--help"],
        cwd=BACKEND_DIR,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for command in (
        "init-db",
        "serve",
        "daily",
        "ingest-feeds",
        "backfill",
        "enrich",
        "embed",
        "build-deck",
        "expand",
        "download-model",
        "reset-link",
        "invite-link",
    ):
        assert command in out
    assert "eval-replay" not in out


def test_reset_link_prints_a_one_time_link(
    capsys: pytest.CaptureFixture[str], set_env: Callable[..., None]
) -> None:
    from radar import models
    from radar.db import session_scope

    set_env(RADAR_PUBLIC_URL="https://radar.example.org")
    with session_scope(write=True) as session:
        session.add(models.User(email="ada@example.org", display_name="Ada"))
    assert cli.main(["reset-link", "--email", "ADA@example.org"]) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith("https://radar.example.org/reset?token=") and len(link) > 60
    assert cli.main(["reset-link", "--email", "nobody@example.org"]) == 1
    assert "no account" in capsys.readouterr().err


def test_invite_link_creates_a_password_less_account_and_prints_a_link(
    capsys: pytest.CaptureFixture[str], set_env: Callable[..., None]
) -> None:
    set_env(RADAR_AUTH="accounts", RADAR_PUBLIC_URL="https://radar.example.org")
    assert cli.main(["invite-link", "--email", " Boss@Example.org "]) == 0
    out, err = capsys.readouterr()
    link = out.strip()
    assert link.startswith("https://radar.example.org/reset?token=") and len(link) > 60
    assert "works once" in err and "30 minutes" in err and "confirms the address" in err
    with session_scope() as session:
        user = session.scalars(select(models.User).where(models.User.email == "boss@example.org")).one()
        assert (user.password_hash, user.email_verified, user.is_admin) == (None, False, False)
        first_id = user.id
    # again: the same account, a new link (the old one stops working)
    assert cli.main(["invite-link", "--email", "boss@example.org"]) == 0
    assert capsys.readouterr().out.strip() != link
    with session_scope() as session:
        rows = session.scalars(select(models.User).where(models.User.email == "boss@example.org")).all()
        assert [u.id for u in rows] == [first_id]
        tokens = session.scalars(
            select(models.AuthToken).where(
                models.AuthToken.user_id == first_id, models.AuthToken.used_at.is_(None)
            )
        ).all()
        assert len(tokens) == 1 and tokens[0].purpose == "reset_password"


@pytest.mark.parametrize(
    ("env", "email", "code", "message"),
    [
        ({"RADAR_AUTH": "local"}, "boss@example.org", 1, "password sign-in is off"),
        ({"RADAR_AUTH": "accounts", "RADAR_PASSWORD_LOGIN": "0"}, "boss@example.org", 1, "sign-in is off"),
        ({"RADAR_AUTH": "accounts"}, "not-an-address", 2, "valid email address"),
        (
            {"RADAR_AUTH": "accounts", "RADAR_ALLOWED_EMAILS": "@lab.example.org"},
            "boss@example.org",
            1,
            "RADAR_ALLOWED_EMAILS",
        ),
    ],
)
def test_invite_link_refusals(
    capsys: pytest.CaptureFixture[str],
    set_env: Callable[..., None],
    env: dict[str, str],
    email: str,
    code: int,
    message: str,
) -> None:
    set_env(**env)
    assert cli.main(["invite-link", "--email", email]) == code
    out, err = capsys.readouterr()
    assert out == "" and message in err
    with session_scope() as session:
        assert session.scalar(select(models.User.id).where(models.User.email == email.lower())) is None
