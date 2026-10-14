"""FastAPI dependencies and small request helpers (SPEC §2, §2.1).

Sessions
--------
``get_db`` (read) and ``get_write_db`` (write) return a request-scoped Session that is committed
(or rolled back when the endpoint raised) and closed right after the endpoint returns - BEFORE the
response is sent, so clients never observe uncommitted data and commit failures become a 500.
Use them as ``session: Session = Depends(get_write_db)`` or via the aliases ``ReadSession`` /
``WriteSession``; within one request every ``Depends(get_db)`` shares one session.

Route handlers that touch the DB are sync ``def``. Never do network calls (HttpClient, connectors,
``submit_job(session=None)``) after the write session has been used in a request: do slow work first,
or use ``radar.db.session_scope(write=True)`` yourself for a short write (CONVENTIONS).

Users (SPEC §5.9)
-----------------
``current_user`` resolves the session cookie to a :class:`CurrentUser` (401 ``unauthenticated``
without a live session); in ``RADAR_AUTH=local`` mode every request is user 1 (an admin).
``get_owner_id``/``OwnerId`` give the user's id, ``AdminUser`` requires an admin (403 ``forbidden``).
Routers are included with ``dependencies=[Depends(current_user)]`` in ``radar.main``, so every route
of a protected router needs a session even when it does not ask for the user.

Projects
--------
``get_project_or_404(session, project_id, owner_id)`` loads a project of that user or raises 404
(another user's project is indistinguishable from a missing one).

Cursors
-------
``encode_cursor``/``decode_cursor`` turn a small JSON dict into an opaque URL-safe string; a malformed
cursor is a 422 ``validation_error`` on ``cursor``. ``split_csv`` parses list query parameters such
as ``states=relevant,saved``.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Collection
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Annotated, Any

import anyio
from dataclasses import dataclass

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from radar.auth import sessions as auth_sessions
from radar.config import get_settings
from radar.db import new_session
from radar.errors import ApiError, validation_error
from radar.models import LOCAL_USER_ID, Project

_FUNCTION_STACK_KEY = "fastapi_function_astack"


def _finish_session(session: Session, ok: bool) -> None:
    try:
        if ok:
            session.commit()
        else:
            session.rollback()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _request_session(request: Request, *, write: bool) -> Session:
    stack = request.scope.get(_FUNCTION_STACK_KEY)
    if not isinstance(stack, AsyncExitStack):  # pragma: no cover - FastAPI internals changed
        raise RuntimeError("get_db/get_write_db must be used as FastAPI route dependencies")
    session = new_session(write=write)

    async def _exit(
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Shielded: the transaction must end (and the write lock be released) even on cancellation.
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(_finish_session, session, exc is None)
        return False

    stack.push_async_exit(_exit)
    return session


def get_db(request: Request) -> Session:
    """Read session for this request (BEGIN deferred; strict mode forbids writes)."""
    return _request_session(request, write=False)


def get_write_db(request: Request) -> Session:
    """Write session for this request (BEGIN IMMEDIATE); committed before the response is sent."""
    return _request_session(request, write=True)


@dataclass(frozen=True)
class CurrentUser:
    """The signed-in user of a request (SPEC §5.9)."""

    id: int
    is_admin: bool
    session_id: int | None  # None in local mode


def current_user(request: Request, response: Response) -> CurrentUser:
    """The request's user: user 1 in local mode, else the live session's user (401 otherwise).

    Records activity on the session (at most every 5 minutes) and re-issues the cookie when its
    expiry slides (SPEC §5.2). FastAPI caches the result per request.
    """
    settings = get_settings()
    if settings.auth == "local":
        return CurrentUser(id=LOCAL_USER_ID, is_admin=True, session_id=None)
    token = request.cookies.get(auth_sessions.cookie_name(settings))
    info = auth_sessions.resolve(token)
    if info is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue")
    if auth_sessions.touch(info) and token:
        auth_sessions.set_cookie(response, token, settings)
    request.state.user_id = info.user_id
    return CurrentUser(id=info.user_id, is_admin=info.is_admin, session_id=info.session_id)


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """403 ``forbidden`` unless the user is an admin."""
    if not user.is_admin:
        raise ApiError(403, "forbidden", "Only an administrator can do this")
    return user


def get_owner_id(user: CurrentUser = Depends(current_user)) -> int:
    """The id of the request's user."""
    return user.id


ReadSession = Annotated[Session, Depends(get_db)]
WriteSession = Annotated[Session, Depends(get_write_db)]
UserDep = Annotated[CurrentUser, Depends(current_user)]
AdminUser = Annotated[CurrentUser, Depends(require_admin)]
OwnerId = Annotated[int, Depends(get_owner_id)]


def get_project_or_404(session: Session, project_id: int, owner_id: int) -> Project:
    """Load the project ``project_id`` owned by ``owner_id`` or raise 404 ``not_found``."""
    project = session.get(Project, project_id)
    if project is None or project.owner_id != owner_id:
        raise ApiError(404, "not_found", "Project not found")
    return project


def encode_cursor(payload: dict[str, Any]) -> str:
    """Opaque, URL-safe cursor for ``payload`` (JSON-serializable values only)."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    """Decode a cursor made by :func:`encode_cursor`; ``None``/empty -> ``None``; invalid -> 422."""
    if cursor is None or cursor == "":
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        data = None
    if not isinstance(data, dict):
        raise validation_error([{"path": "cursor", "message": "Invalid cursor"}], "Invalid cursor")
    return data


def split_csv(
    value: str | None,
    *,
    allowed: Collection[str] | None = None,
    param: str = "value",
    default: list[str] | None = None,
) -> list[str]:
    """Parse a comma-separated query value (blank items dropped, order kept, duplicates removed).

    Unknown items (when ``allowed`` is given) raise 422 ``validation_error`` on ``param``.
    ``None``/blank returns ``default`` (or ``[]``).
    """
    if value is None or not value.strip():
        return list(default or [])
    items: list[str] = []
    for part in value.split(","):
        item = part.strip()
        if item and item not in items:
            items.append(item)
    if allowed is not None:
        unknown = [i for i in items if i not in allowed]
        if unknown:
            raise validation_error(
                [{"path": param, "message": f"Unknown value; allowed: {', '.join(sorted(allowed))}"}]
            )
    return items
