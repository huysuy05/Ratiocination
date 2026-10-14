"""API errors and the single error envelope (docs/API.md "Conventions").

Every error response is ``{"error": {"code", "message", "request_id", "details"?}}``:

* :class:`ApiError` (and subclasses) -> its status/code/message/details;
* ``RequestValidationError`` -> 422 ``validation_error`` with ``details.fields = [{path, message}]``
  (dotted paths such as ``settings.slate_size``; input values are never echoed or logged);
* Starlette ``HTTPException`` (unknown routes 404, wrong method 405, ...) -> envelope with a code
  derived from the status;
* ``NotImplementedError`` (a module that is still a stub) -> 501 ``not_implemented``;
* anything else -> 500 ``internal_error`` with a generic message (the traceback is logged with the
  request id only). The catch-all lives in ``radar.main`` (outermost middleware) so the response
  still carries ``X-Request-ID`` and ``Referrer-Policy``.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class ApiError(Exception):
    """An error returned to the client in the standard envelope.

    ``code`` is one of API.md's ``ErrorCode`` values; ``message`` is shown to the user and must not
    contain private text, URLs with query strings, keys or emails; ``details`` is JSON-serializable
    (Pydantic models allowed), e.g. ``{"job": Job}`` or ``{"document_id": 3}``.
    """

    def __init__(self, status: int, code: str, message: str, details: object | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def __repr__(self) -> str:
        return f"ApiError(status={self.status!r}, code={self.code!r})"


class RetryableError(Exception):
    """Raised by a job function to ask the runner for a bounded retry (SPEC §15: 2, 4, 8 s)."""


class UnresolvableIdentifier(ApiError):
    """422 ``unresolvable_identifier``: a seed identifier that no source could resolve (SPEC §1)."""

    def __init__(
        self, message: str = "Could not resolve this identifier", details: object | None = None
    ) -> None:
        super().__init__(422, "unresolvable_identifier", message, details)


# ------------------------------------------------------------------------------------------------
# Envelope helpers
# ------------------------------------------------------------------------------------------------

_STATUS_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    501: "not_implemented",
    503: "source_unavailable",
}

_STATUS_MESSAGES: dict[int, str] = {
    400: "Bad request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation failed",
    429: "Too many requests",
    501: "Not implemented",
    503: "Service unavailable",
}


def code_for_status(status: int) -> str:
    """Map an HTTP status to an ``ErrorCode``."""
    if status in _STATUS_CODES:
        return _STATUS_CODES[status]
    return "internal_error" if status >= 500 else "validation_error"


def _iso_z(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    text = value.astimezone(dt.UTC).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def encode_details(details: object) -> Any:
    """JSON-encode ``details`` (Pydantic models via ``model_dump(mode='json')``; datetimes with Z)."""
    if isinstance(details, BaseModel):
        return details.model_dump(mode="json")
    if isinstance(details, Mapping):
        return {str(k): encode_details(v) for k, v in details.items()}
    if isinstance(details, (list, tuple)):
        return [encode_details(v) for v in details]
    if isinstance(details, dt.datetime):
        return _iso_z(details)
    return jsonable_encoder(details, custom_encoder={dt.datetime: _iso_z})


def get_request_id(request: Request) -> str:
    """The request id set by the request-id middleware (a fresh one if missing)."""
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
    return str(rid)


def error_payload(code: str, message: str, request_id: str, details: object | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = encode_details(details)
    return {"error": body}


def error_response(
    status: int,
    code: str,
    message: str,
    request_id: str,
    details: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an envelope response (the request-id middleware adds X-Request-ID/Referrer-Policy)."""
    hdrs = {REQUEST_ID_HEADER: request_id}
    if headers:
        hdrs.update(headers)
    return JSONResponse(error_payload(code, message, request_id, details), status_code=status, headers=hdrs)


# ------------------------------------------------------------------------------------------------
# Validation errors: dotted paths, never input values
# ------------------------------------------------------------------------------------------------

_LOC_PREFIXES = frozenset({"body", "query", "path", "header", "cookie"})
_SAFE_MESSAGES: dict[str, str] = {
    # Pydantic messages that can echo the offending input value
    "union_tag_invalid": "Input tag does not match any of the expected tags",
    "get_attribute_error": "Invalid value",
    "mapping_type": "Input should be a valid mapping",
    "iteration_error": "Invalid value",
    "no_such_attribute": "Invalid attribute",
    "json_invalid": "Invalid JSON",
    "json_type": "Invalid JSON",
}


def _error_path(error: Mapping[str, Any]) -> str:
    loc: Sequence[Any] = tuple(error.get("loc") or ())
    if error.get("type") in {"json_invalid", "json_type"}:
        return "body"
    if loc and loc[0] in _LOC_PREFIXES:
        if len(loc) == 1:
            return str(loc[0])
        loc = loc[1:]
    return ".".join(str(part) for part in loc)


def validation_fields(errors: Sequence[Mapping[str, Any]], prefix: str = "") -> list[dict[str, str]]:
    """Convert Pydantic/FastAPI errors to ``[{path, message}]`` without input values or ctx.

    ``prefix`` (e.g. ``"settings"``) is prepended to each path.
    """
    fields: list[dict[str, str]] = []
    for err in errors:
        path = _error_path(err)
        if prefix:
            path = f"{prefix}.{path}" if path else prefix
        err_type = str(err.get("type") or "")
        message = _SAFE_MESSAGES.get(err_type) or str(err.get("msg") or "Invalid value")
        fields.append({"path": path, "message": message})
    return fields


def validation_error(fields: list[dict[str, str]], message: str = "Validation failed") -> ApiError:
    """An ``ApiError`` 422 ``validation_error`` with ``details.fields``."""
    return ApiError(422, "validation_error", message, {"fields": fields})


# ------------------------------------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------------------------------------


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    rid = get_request_id(request)
    headers: dict[str, str] | None = None
    retry = exc.details.get("retry_after_seconds") if isinstance(exc.details, Mapping) else None
    if isinstance(retry, int) and not isinstance(retry, bool):
        headers = {"Retry-After": str(max(1, retry))}
    return error_response(exc.status, exc.code, exc.message, rid, exc.details, headers=headers)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    rid = get_request_id(request)
    fields = validation_fields(exc.errors())
    # Log locations and types only - never input values or ctx.
    log.info(
        "validation error request_id=%s fields=%s",
        rid,
        [(f["path"], e.get("type")) for f, e in zip(fields, exc.errors(), strict=False)],
    )
    return error_response(422, "validation_error", "Validation failed", rid, {"fields": fields})


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    rid = get_request_id(request)
    status = exc.status_code
    code = code_for_status(status)
    if status in _STATUS_MESSAGES:
        message = _STATUS_MESSAGES[status]
    elif isinstance(exc.detail, str) and exc.detail:
        message = exc.detail
    else:
        message = "Request failed"
    headers = dict(exc.headers) if exc.headers else None
    return error_response(status, code, message, rid, headers=headers)


async def not_implemented_handler(request: Request, exc: Exception) -> JSONResponse:
    """A stubbed module raised ``NotImplementedError`` -> 501 ``not_implemented`` (no exception text)."""
    rid = get_request_id(request)
    log.warning("not implemented request_id=%s", rid)
    return error_response(501, "not_implemented", "This feature is not implemented yet", rid)


def internal_error_response(request_id: str) -> JSONResponse:
    """The generic 500 envelope (no exception text)."""
    return error_response(500, "internal_error", "Internal server error", request_id)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort (Starlette's ServerErrorMiddleware): generic 500, traceback logged by request id only.

    ``radar.main`` normally catches unhandled exceptions earlier, in its outermost middleware.
    """
    rid = get_request_id(request)
    log.error("unhandled error request_id=%s", rid, exc_info=exc)
    response = internal_error_response(rid)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Install the envelope handlers on ``app`` (ApiError, validation, HTTP, NotImplementedError, any)."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(NotImplementedError, not_implemented_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
