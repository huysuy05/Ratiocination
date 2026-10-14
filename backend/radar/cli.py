"""Command line interface: ``python -m radar.cli <command>`` (SPEC §2, §5.7, §15). Owner: jobs.

Commands: init-db, serve, daily, ingest-feeds, backfill --project, enrich, embed,
build-deck --project [--mode] [--prepare], expand --paper [--project] [--force],
breakthroughs --paper [--force], download-model [--model], reset-link --email, invite-link --email.

``invite-link`` bootstraps an account without SMTP (e.g. the first admin of a public server): it
creates the account for the address when missing (no password) and prints a one-time link (30
minutes) on which the owner chooses a password; that proves the address, so admin rights and user 1
follow the usual rules (SPEC §5.6, §5.7). Exit codes: 0 printed, 1 password sign-in is off or the
address is not admitted by RADAR_ALLOWED_EMAILS, 2 not a valid email address.

Job commands (daily, ingest-feeds, backfill, enrich, embed, build-deck, expand, breakthroughs,
download-model):
``init_db()`` -> logging -> ``runner.start()`` -> ``runner.run_job_sync(kind, ...)`` in this process
(a ``jobs`` row records the run) -> wait until the jobs this process queued meanwhile have finished
(e.g. the ``build_deck`` jobs that ``daily`` submits) -> print a JSON summary ``{job_id?, kind,
status, stats, followup_jobs}`` on stdout. Exit code 0 when the job succeeded, 1 when it failed
(``"<kind> failed (job N): <error_code>: <error_message>"`` on stderr) or an identical job is already
queued or running. ``download-model`` is the explicit model download (SPEC §9).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

SLATE_MODES = ("daily", "more")


def cmd_init_db(args: argparse.Namespace) -> int:
    """Create/upgrade the database and the local user."""
    from radar.db import init_db, shutdown_writer

    init_db()
    shutdown_writer()
    print("database ready")
    return 0


def cmd_reset_link(args: argparse.Namespace) -> int:
    """Print a one-time password reset link for an account (operators without SMTP, SPEC §5.7)."""
    from sqlalchemy import select

    from radar.auth import passwords, tokens
    from radar.db import init_db, session_scope, shutdown_writer
    from radar.models import User

    init_db()
    try:
        email = passwords.normalize_email(args.email)
        with session_scope(write=True) as session:
            user_id = session.scalar(select(User.id).where(User.email == email))
            if user_id is None:
                print("reset-link failed: no account with that email", file=sys.stderr)
                return 1
            raw = tokens.issue(session, user_id, tokens.RESET_PASSWORD, email)
        print(tokens.link(tokens.RESET_PASSWORD, raw))
        print("The link works once and expires in 30 minutes.", file=sys.stderr)
        return 0
    finally:
        shutdown_writer()


def cmd_invite_link(args: argparse.Namespace) -> int:
    """Print a one-time link on which ``--email`` sets a password; creates the account (no SMTP needed)."""
    from radar.auth import accounts, passwords, tokens
    from radar.config import get_settings
    from radar.db import init_db, session_scope, shutdown_writer

    settings = get_settings()
    if not settings.password_enabled:
        print(
            "invite-link failed: email + password sign-in is off (RADAR_AUTH=accounts and "
            "RADAR_PASSWORD_LOGIN=1 are needed)",
            file=sys.stderr,
        )
        return 1
    email = passwords.normalize_email(args.email)
    problem = passwords.email_problem(email)
    if problem is not None:
        print(f"invite-link failed: {problem}", file=sys.stderr)
        return 2
    if not settings.email_allowed(email):
        print("invite-link failed: the address is not admitted by RADAR_ALLOWED_EMAILS", file=sys.stderr)
        return 1
    init_db()
    try:
        with session_scope(write=True) as session:
            user = accounts.invite(session, email)
            raw = tokens.issue(session, user.id, tokens.RESET_PASSWORD, email)
        print(tokens.link(tokens.RESET_PASSWORD, raw))
        print(
            "The link works once and expires in 30 minutes; choosing a password there confirms the address.",
            file=sys.stderr,
        )
        return 0
    finally:
        shutdown_writer()


def cmd_serve(args: argparse.Namespace) -> int:
    """Run uvicorn with ONE worker on RADAR_HOST/RADAR_PORT (or --host/--port)."""
    import uvicorn

    from radar.config import get_settings
    from radar.logging_setup import uvicorn_log_config

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if host != settings.host:
        # create_app() validates the bind (a public server needs accounts, admins, TLS and an https
        # RADAR_PUBLIC_URL); keep the setting and the actual bind consistent.
        os.environ["RADAR_HOST"] = host
        get_settings.cache_clear()
    uvicorn.run(
        "radar.main:create_app",
        factory=True,
        host=host,
        port=port,
        workers=1,
        log_config=uvicorn_log_config(),
        ssl_certfile=settings.tls_cert,
        ssl_keyfile=settings.tls_key,
    )
    return 0


# ------------------------------------------------------------------------------------------------
# Job commands
# ------------------------------------------------------------------------------------------------


def _jobs_created_since(after_id: int) -> list[dict[str, Any]]:
    """Jobs this process created after ``after_id`` (``worker_pid`` = this process), oldest first."""
    from sqlalchemy import select

    from radar.db import session_scope
    from radar.models import Job

    with session_scope() as session:
        rows = session.scalars(
            select(Job).where(Job.id > after_id, Job.worker_pid == os.getpid()).order_by(Job.id)
        ).all()
        return [
            {
                "id": job.id,
                "kind": job.kind,
                "project_id": job.project_id,
                "status": job.status,
                "error_code": job.error_code,
            }
            for job in rows
        ]


def _max_job_id() -> int:
    from sqlalchemy import func, select

    from radar.db import session_scope
    from radar.models import Job

    with session_scope() as session:
        return int(session.scalar(select(func.max(Job.id))) or 0)


def _project_exists(project_id: int) -> bool:
    from radar.db import session_scope
    from radar.models import Project

    with session_scope() as session:
        return session.get(Project, project_id) is not None


def run_job_command(kind: str, *, project_id: int | None = None, params: dict[str, Any] | None = None) -> int:
    """Run one job synchronously, wait for the jobs it queued, print a summary; 0 = succeeded."""
    from radar import db
    from radar.config import get_settings
    from radar.jobs import runner
    from radar.logging_setup import configure_logging

    db.init_db()
    configure_logging(get_settings())
    if project_id is not None and not _project_exists(project_id):
        print(f"{kind} failed: project {project_id} not found", file=sys.stderr)
        db.shutdown_writer()
        return 1
    before = _max_job_id()
    runner.start()
    try:
        summary: dict[str, Any] = {"kind": kind, "job_id": None}
        exit_code = 0
        try:
            stats = runner.run_job_sync(kind, project_id=project_id, params=params)
            summary.update(status="succeeded", stats=stats)
        except runner.JobFailed as exc:
            exit_code = 1
            summary.update(
                job_id=exc.job_id,
                status="conflict" if isinstance(exc, runner.JobConflict) else "failed",
                error_code=exc.error_code,
                error_message=exc.error_message,
                stats=exc.stats,
            )
            print(f"{kind} failed (job {exc.job_id}): {exc.error_code}: {exc.error_message}", file=sys.stderr)
        if not runner.wait_idle(timeout=0):
            print("waiting for the jobs queued by this run to finish ...", file=sys.stderr)
            runner.wait_idle()
        created = _jobs_created_since(before)
        if summary["job_id"] is None and created:
            summary["job_id"] = created[0]["id"]  # run_job_sync inserts its row before anything else
        summary["followup_jobs"] = [job for job in created if job["id"] != summary["job_id"]]
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return exit_code
    finally:
        runner.shutdown()
        db.shutdown_writer()


def _job_handler(
    kind: str, build: Callable[[argparse.Namespace], tuple[int | None, dict[str, Any]]]
) -> Callable[[argparse.Namespace], int]:
    def handler(args: argparse.Namespace) -> int:
        project_id, params = build(args)
        return run_job_command(kind, project_id=project_id, params=params)

    handler.__name__ = f"cmd_{kind}"
    return handler


def _no_args(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    return None, {}


def _project_only(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    return args.project, {}


def _build_deck_args(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    params: dict[str, Any] = {"mode": args.mode}
    if args.prepare:
        params["prepare"] = True
    return args.project, params


def _expand_args(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    params: dict[str, Any] = {"paper_id": args.paper}
    if args.force:
        params["force"] = True
    return args.project, params


def _download_model_args(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    return None, ({"model_id": args.model} if args.model else {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m radar.cli", description="Ratiocination command line")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, func: Callable[[argparse.Namespace], int]) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    add("init-db", "create/upgrade the database and the local user", cmd_init_db)
    p = add("reset-link", "print a one-time password reset link for an account", cmd_reset_link)
    p.add_argument("--email", required=True)
    p = add(
        "invite-link",
        "print a one-time link that lets an address set a password (creates the account; no SMTP needed)",
        cmd_invite_link,
    )
    p.add_argument("--email", required=True)
    p = add("serve", "run the web server (uvicorn, one worker)", cmd_serve)
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    add("daily", "run the daily pipeline now", _job_handler("daily", _no_args))
    add("ingest-feeds", "fetch arXiv category feeds", _job_handler("ingest_feeds", _no_args))
    p = add(
        "backfill",
        "backfill a project's time window from the arXiv API",
        _job_handler("backfill", _project_only),
    )
    p.add_argument("--project", type=int, required=True)
    add("enrich", "enrich papers from OpenAlex", _job_handler("enrich", _no_args))
    add("embed", "compute local paper embeddings", _job_handler("embed", _no_args))
    p = add("build-deck", "build a deck for a project", _job_handler("build_deck", _build_deck_args))
    p.add_argument("--project", type=int, required=True)
    p.add_argument("--mode", choices=SLATE_MODES, default="daily")
    p.add_argument("--prepare", action="store_true", help="backfill, enrich and embed first")
    p = add("expand", "expand a paper's citation neighborhood", _job_handler("expand", _expand_args))
    p.add_argument("--paper", type=int, required=True)
    p.add_argument("--project", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p = add(
        "download-model",
        "download the local embedding model (explicit egress)",
        _job_handler("download_model", _download_model_args),
    )
    p.add_argument("--model", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
