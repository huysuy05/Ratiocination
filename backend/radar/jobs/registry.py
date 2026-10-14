"""Job kind -> ``"module:function"`` (SPEC §15), resolved lazily. Owner: jobs.

* :data:`REGISTRY` maps every API ``JobKind`` to a ``"module:function"`` string. Nothing is imported
  until :func:`resolve` is called (when a job runs, or when the API validates parameters), so importing
  ``radar.jobs`` never pulls in connectors, embeddings or ranking code.
* :func:`register` overrides a kind (or adds a new one) with a callable or another
  ``"module:function"`` string; tests use it to install fake job functions and must call
  :func:`unregister` (or :func:`reset_overrides`) afterwards.
* :func:`validate_params` checks job parameters against the job function's signature (names,
  required arguments, strict JSON types); ``POST /api/jobs/run`` uses it.

A job function is called as ``fn(ctx, **params)`` where ``ctx`` is a
:class:`radar.jobs.runner.JobContext`; ``project_id`` is never a parameter (it is ``ctx.project_id``).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)

REGISTRY: dict[str, str] = {
    "daily": "radar.jobs.pipeline:run_daily",
    "ingest_feeds": "radar.ingestion.feeds:run_ingest_feeds",
    "backfill": "radar.ingestion.backfill:run_backfill",
    "enrich": "radar.ingestion.enrich:run_enrich",
    "embed": "radar.ingestion.vectors:run_embed_papers",
    "build_deck": "radar.jobs.pipeline:run_build_deck",
    "expand": "radar.graph.expand:run_expand",
    "breakthroughs": "radar.graph.breakthroughs:run_breakthroughs",
    "process_document": "radar.documents.service:run_process_document",
    "download_model": "radar.embeddings.registry:run_download_model",
    "summarize_findings": "radar.dashboard.summary:run_summarize_findings",
}

GLOBAL_KINDS: frozenset[str] = frozenset({"daily", "ingest_feeds", "enrich", "embed", "download_model"})
"""Kinds that only admins may run through ``POST /api/jobs/run`` (SPEC §5.9)."""

PROJECT_REQUIRED: frozenset[str] = frozenset({"backfill", "build_deck"})
"""Kinds whose function requires ``ctx.project_id`` (SPEC §15)."""

MAX_KIND_LENGTH = 32
"""``jobs.kind`` is ``String(32)``."""

JobFunction = Callable[..., Any]

_lock = threading.Lock()
_overrides: dict[str, JobFunction | str] = {}


class UnknownJobKind(KeyError):
    """A job kind that is neither in :data:`REGISTRY` nor installed with :func:`register`."""

    def __init__(self, kind: object) -> None:
        super().__init__(kind)
        self.kind = kind

    def __str__(self) -> str:
        return f"unknown job kind: {self.kind!r}"


class JobParamsError(ValueError):
    """Job parameters that do not fit the job function; ``fields`` = ``[{path, message}]``.

    Paths are dotted and start with ``params`` (e.g. ``params.paper_id``); messages never echo input
    values.
    """

    def __init__(self, fields: list[dict[str, str]]) -> None:
        super().__init__("invalid job parameters")
        self.fields = fields


# ------------------------------------------------------------------------------------------------
# Registration (tests) and lookup
# ------------------------------------------------------------------------------------------------


def register(kind: str, fn: JobFunction | str) -> None:
    """Use ``fn`` (a callable or a ``"module:function"`` string) for ``kind``.

    Overrides a built-in kind or adds a new one (a registered kind is a valid kind for
    ``submit_job``). Intended for tests; undo with :func:`unregister`.
    """
    if not isinstance(kind, str) or not kind or len(kind) > MAX_KIND_LENGTH:
        raise ValueError(f"job kind must be a non-empty string of at most {MAX_KIND_LENGTH} characters")
    if isinstance(fn, str):
        _split_target(fn)  # validate the format now, import lazily later
    elif not callable(fn):
        raise TypeError("fn must be a callable or a 'module:function' string")
    with _lock:
        _overrides[kind] = fn


def unregister(kind: str) -> None:
    """Remove an override installed by :func:`register` (built-in kinds fall back to REGISTRY)."""
    with _lock:
        _overrides.pop(kind, None)


def reset_overrides() -> None:
    """Remove every override installed by :func:`register`."""
    with _lock:
        _overrides.clear()


def is_known(kind: object) -> bool:
    """True for kinds in :data:`REGISTRY` or installed with :func:`register`."""
    if not isinstance(kind, str):
        return False
    with _lock:
        return kind in _overrides or kind in REGISTRY


def known_kinds() -> list[str]:
    """Sorted list of every valid kind (built-in and registered)."""
    with _lock:
        return sorted(set(REGISTRY) | set(_overrides))


def target(kind: str) -> JobFunction | str:
    """The callable or ``"module:function"`` string ``kind`` maps to, without importing anything."""
    if isinstance(kind, str):
        with _lock:
            if kind in _overrides:
                return _overrides[kind]
        if kind in REGISTRY:
            return REGISTRY[kind]
    raise UnknownJobKind(kind)


def _split_target(spec: str) -> tuple[str, str]:
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name.strip() or not attr.strip():
        raise ValueError(f"job target must look like 'package.module:function', got {spec!r}")
    return module_name.strip(), attr.strip()


def _import_target(spec: str) -> JobFunction:
    module_name, attr = _split_target(spec)
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"job target {spec!r} is not callable")
    return obj


def resolve(kind: str) -> Callable[..., dict[str, Any] | None]:
    """Import and return the job function for ``kind`` (``KeyError`` for unknown kinds).

    Raises :class:`UnknownJobKind` (a ``KeyError``) for unknown kinds; import errors propagate.
    Nothing is cached, so a monkeypatched module attribute is picked up on the next call.
    """
    found = target(kind)
    if isinstance(found, str):
        return _import_target(found)
    return found


# ------------------------------------------------------------------------------------------------
# Parameter validation (POST /api/jobs/run)
# ------------------------------------------------------------------------------------------------


def _signature(fn: JobFunction) -> inspect.Signature:
    """The signature with string annotations evaluated where possible.

    Job modules import ``JobContext`` only under TYPE_CHECKING, so that name is supplied here; when an
    annotation still cannot be evaluated the raw signature is used (string annotations = any value).
    """
    try:
        return inspect.signature(fn, locals={"JobContext": object}, eval_str=True)
    except Exception:
        return inspect.signature(fn)


def _job_parameters(fn: JobFunction) -> tuple[dict[str, inspect.Parameter], bool]:
    """The keyword parameters after ``ctx`` and whether ``**kwargs`` is accepted."""
    params = list(_signature(fn).parameters.values())
    if params and params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        params = params[1:]  # ctx
    accepted: dict[str, inspect.Parameter] = {}
    var_keyword = False
    for p in params:
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            var_keyword = True
        elif p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            accepted[p.name] = p
    return accepted, var_keyword


def validate_params(kind: str, params: Mapping[str, Any]) -> None:
    """Check ``params`` against the signature of ``kind``'s job function.

    Unknown names (unless the function takes ``**kwargs``), missing required arguments and values
    that do not match the annotation in pydantic strict mode raise :class:`JobParamsError`;
    ``project_id`` is always rejected (it is a job field, not a parameter). Unknown kinds raise
    :class:`UnknownJobKind`. When the job module cannot be imported the check is skipped (the job
    itself will fail and record the error).
    """
    from pydantic import ConfigDict, TypeAdapter, ValidationError

    from radar.errors import validation_fields

    try:
        fn = resolve(kind)
    except UnknownJobKind:
        raise
    except Exception:
        log.warning("could not import the job function for kind %s; parameters not validated", kind)
        return

    accepted, var_keyword = _job_parameters(fn)
    fields: list[dict[str, str]] = []
    for name, value in params.items():
        path = f"params.{name}"
        if name == "project_id":
            fields.append({"path": path, "message": "project_id is a job field, not a parameter"})
            continue
        param = accepted.get(name)
        if param is None:
            if not var_keyword:
                fields.append({"path": path, "message": "Unknown parameter"})
            continue
        annotation = param.annotation
        if annotation is inspect.Parameter.empty or annotation is Any or isinstance(annotation, str):
            continue
        try:
            adapter: TypeAdapter[Any] = TypeAdapter(annotation, config=ConfigDict(strict=True))
        except Exception:  # an annotation pydantic cannot build a validator for: accept any value
            continue
        try:
            adapter.validate_python(value)
        except ValidationError as exc:
            fields.extend(validation_fields(exc.errors(include_input=False), prefix=path))
    for name, param in accepted.items():
        if param.default is inspect.Parameter.empty and name not in params:
            fields.append({"path": f"params.{name}", "message": "Field required"})
    if fields:
        raise JobParamsError(fields)


__all__ = [
    "GLOBAL_KINDS",
    "MAX_KIND_LENGTH",
    "PROJECT_REQUIRED",
    "REGISTRY",
    "JobParamsError",
    "UnknownJobKind",
    "is_known",
    "known_kinds",
    "register",
    "reset_overrides",
    "resolve",
    "target",
    "unregister",
    "validate_params",
]
