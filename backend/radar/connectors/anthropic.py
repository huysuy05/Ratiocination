"""Anthropic Messages API, forced tool use (SPEC §14.4). Owner: dashboard.

One call shape: ``POST https://api.anthropic.com/v1/messages`` (``anthropic-version: 2023-06-01``) with
one tool and ``tool_choice: {type: "tool", name}``, so the reply's ``tool_use`` block carries a JSON
result. ``radar.net.http`` attaches the ``x-api-key`` header (``ANTHROPIC_API_KEY``), retries
429/5xx/529 and records the call in the ledger; this module builds the body and reads the reply.

Shapes follow the public API reference: a reply is ``{model, content: [{type: "tool_use", name,
input}, ...], stop_reason, usage: {input_tokens, output_tokens}}``; an error is ``{type: "error",
error: {type, message}}``.

Errors: 401/403 -> ``ApiError(503, "not_configured")`` (the key was rejected); any other non-200 ->
``SourceUnavailable`` naming the status and the error type; a reply without the tool block ->
``SourceUnavailable``. Messages never contain the key, the request body or the provider's error text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from radar.errors import ApiError
from radar.net.http import ANTHROPIC, SourceUnavailable, get_http

log = logging.getLogger(__name__)

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT_S = 120.0
ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "not_found_error",
        "request_too_large",
        "rate_limit_error",
        "api_error",
        "overloaded_error",
    }
)


@dataclass(frozen=True)
class ToolCall:
    input: dict[str, Any]
    model: str
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def tool_request(
    *,
    model: str,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    """The request body (pure)."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
        "tool_choice": {"type": "tool", "name": tool_name},
    }


def parse_tool_call(payload: Any, tool_name: str) -> ToolCall | None:
    """The first ``tool_use`` block for ``tool_name`` in a reply, or ``None`` (pure)."""
    if not isinstance(payload, dict):
        return None
    for block in payload.get("content") or []:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == tool_name
            and isinstance(block.get("input"), dict)
        ):
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            stop = payload.get("stop_reason")
            return ToolCall(
                input=block["input"],
                model=str(payload.get("model") or ""),
                stop_reason=stop if isinstance(stop, str) else None,
                input_tokens=_int(usage.get("input_tokens")),
                output_tokens=_int(usage.get("output_tokens")),
            )
    return None


def _error_type(payload: Any) -> str | None:
    error = payload.get("error") if isinstance(payload, dict) else None
    kind = error.get("type") if isinstance(error, dict) else None
    return kind if kind in ERROR_TYPES else None


def _json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def call_tool(
    *,
    model: str,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 1500,
) -> ToolCall:
    """Send one forced-tool request and return the tool input (see the module docstring for errors)."""
    body = tool_request(
        model=model,
        system=system,
        user=user,
        tool_name=tool_name,
        tool_description=tool_description,
        input_schema=input_schema,
        max_tokens=max_tokens,
    )
    response = get_http().request(
        "POST",
        MESSAGES_URL,
        provider=ANTHROPIC,
        call_type="generate",
        json=body,
        headers={"anthropic-version": API_VERSION},
        timeout=TIMEOUT_S,
    )
    status = response.status_code
    payload = _json(response)
    if status in (401, 403):
        raise ApiError(
            503, "not_configured", "The Anthropic API key was rejected; check ANTHROPIC_API_KEY on the server"
        )
    if status != 200:
        kind = _error_type(payload)
        hint = "; check RADAR_SUMMARY_MODEL" if kind == "not_found_error" else ""
        log.warning("anthropic /v1/messages: HTTP %s (%s)", status, kind or "unknown error")
        raise SourceUnavailable(
            ANTHROPIC,
            status,
            None,
            message=f"anthropic /v1/messages: HTTP {status}" + (f" ({kind})" if kind else "") + hint,
        )
    call = parse_tool_call(payload, tool_name)
    if call is None:
        reason = payload.get("stop_reason") if isinstance(payload, dict) else None
        raise SourceUnavailable(
            ANTHROPIC,
            200,
            None,
            message=f"anthropic /v1/messages: the reply had no {tool_name} result "
            f"(stop reason: {reason if isinstance(reason, str) else 'unknown'})",
        )
    return call


__all__ = ["API_VERSION", "MESSAGES_URL", "ToolCall", "call_tool", "parse_tool_call", "tool_request"]
