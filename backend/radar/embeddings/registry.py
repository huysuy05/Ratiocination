"""Embedder selection, fallback and the explicit model download (SPEC §9). Owner: emb.

* :func:`get_embedder` - the default model comes from ``RADAR_EMBEDDER``. Each model is loaded at most
  once per process: the first attempt either succeeds (the instance is reused) or records why the
  model is unavailable, and nothing is retried until :func:`reset`. An unavailable model is replaced
  by the first other fastembed model whose files are already downloaded (e.g. the previous default
  after the default changed, until the new one is downloaded), else by ``local/hashing-v1``; the
  fallback is decided once per process. Callers keep ``emb = get_embedder(...)`` and pass
  ``emb.model_id`` everywhere (C3); the vector APIs refuse a model id whose embedder is not the one
  actually loaded, and vectors are stored per model, so vectors of two models are never compared.
* :func:`run_download_model` - the ``download_model`` job: the only place that downloads model files
  (into ``<data>/models/fastembed``), never when ``RADAR_OFFLINE=1``; :func:`reset` afterwards.

This package never imports ``radar.net``/``radar.jobs`` (SPEC §2 import rule).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from radar.config import get_settings
from radar.db import check_not_holding_write
from radar.embeddings import fastembed_backend
from radar.embeddings.base import Embedder
from radar.embeddings.hashing import HashingEmbedder
from radar.errors import ApiError
from radar.schemas import EmbedderInfo

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

HASHING_MODEL_ID = "local/hashing-v1"
FASTEMBED_MODEL_ID = fastembed_backend.DEFAULT_SPEC.model_id
"""The default fastembed model (``RADAR_EMBEDDER``'s default)."""
BGE_SMALL_MODEL_ID = fastembed_backend.BGE_SMALL.model_id
BGE_BASE_MODEL_ID = fastembed_backend.BGE_BASE.model_id

KNOWN_DIMENSIONS: dict[str, int] = {
    **{model_id: spec.dim for model_id, spec in fastembed_backend.SPECS.items()},
    HASHING_MODEL_ID: 1024,
}
"""Every model this build can embed text with (display order of :func:`available_embedders`)."""

REASON_NOT_INSTALLED = "fastembed is not installed"
REASON_NOT_DOWNLOADED = "Model not downloaded yet"
REASON_UNKNOWN = "Unknown embedding model"


class EmbedderUnavailable(RuntimeError):
    """A known model cannot be used in this process; the message is the user-facing reason."""


class ModelDownloadBlocked(RuntimeError):
    """The model download was refused because ``RADAR_OFFLINE=1`` (SPEC §9).

    Only raised when ``radar.net.http`` is not loaded; see :func:`_egress_blocked`.
    """


class ModelDownloadFailed(ApiError):
    """The model download, or loading the model right after it, failed.

    An :class:`~radar.errors.ApiError` so the job runner stores its code and message (SPEC §16):
    ``source_unavailable`` when Hugging Face could not deliver the files, ``internal_error`` otherwise.
    Messages never contain URLs or paths.
    """

    def __init__(self, message: str, *, code: str = "source_unavailable") -> None:
        super().__init__(503 if code == "source_unavailable" else 500, code, message)


def _make_hashing() -> Embedder:
    return HashingEmbedder()


def _fastembed_factory(spec: fastembed_backend.ModelSpec) -> Callable[[], Embedder]:
    def make() -> Embedder:
        if not fastembed_backend.fastembed_installed():
            raise EmbedderUnavailable(REASON_NOT_INSTALLED)
        if not fastembed_backend.is_cached(spec.name):
            raise EmbedderUnavailable(REASON_NOT_DOWNLOADED)
        return fastembed_backend.FastEmbedEmbedder(spec)

    return make


_FACTORIES: dict[str, Callable[[], Embedder]] = {
    **{model_id: _fastembed_factory(spec) for model_id, spec in fastembed_backend.SPECS.items()},
    HASHING_MODEL_ID: _make_hashing,
}

_lock = threading.RLock()
_loaded: dict[str, Embedder] = {}
_unavailable: dict[str, str] = {}
_warned: set[str] = set()
_rechecked: dict[str, float] = {}
_download_lock = threading.Lock()
RECHECK_SECONDS = 30.0
"""How often a model recorded as "not downloaded" is looked for again (see :func:`_recheck_download`)."""


def _default_model_id() -> str:
    return get_settings().embedder.strip()


def _try_load(model_id: str) -> Embedder | None:
    """The cached embedder, or one load attempt whose outcome is remembered until :func:`reset`."""
    with _lock:
        emb = _loaded.get(model_id)
        if emb is not None:
            return emb
        if model_id in _unavailable:
            return None
        factory = _FACTORIES.get(model_id)
        if factory is None:
            _unavailable[model_id] = REASON_UNKNOWN
            return None
        try:
            emb = factory()
        except EmbedderUnavailable as exc:
            _unavailable[model_id] = str(exc)
            _rechecked[model_id] = time.monotonic()
            return None
        except Exception as exc:  # a broken install or cache must never break the app: fall back
            log.warning("embedding model %s failed to load (%s)", model_id, type(exc).__name__, exc_info=True)
            _unavailable[model_id] = f"Model could not be loaded ({type(exc).__name__})"
            return None
        if emb.model_id != model_id:
            _unavailable[model_id] = "Embedder reports a different model id"
            log.error("embedder for %s reports model id %s", model_id, emb.model_id)
            return None
        _loaded[model_id] = emb
        log.info("embedding model %s loaded (dim %d)", model_id, emb.dim)
        return emb


def _fallbacks(requested: str) -> list[str]:
    """The other fastembed models (in :data:`SPECS` order; only one whose files are downloaded loads,
    and each outcome is remembered like any other load), then hashing."""
    others = [
        model_id for model_id in fastembed_backend.SPECS if model_id != requested and model_id in _FACTORIES
    ]
    return [*others, HASHING_MODEL_ID]


def _recheck_download(model_id: str) -> None:
    """Forget a "not downloaded" verdict once the model's files are there.

    ``radar download-model`` (the CLI) downloads in another process, so this process never runs the
    :func:`reset` that follows an in-process download and would keep the fallback until a restart. A
    model recorded as not downloaded is therefore looked for again (a few file checks, at most every
    :data:`RECHECK_SECONDS`) and gets a new load attempt when its files have appeared.
    """
    with _lock:
        if _unavailable.get(model_id) != REASON_NOT_DOWNLOADED:
            return
        now = time.monotonic()
        last = _rechecked.get(model_id)
        if last is not None and now - last < RECHECK_SECONDS:
            return
        _rechecked[model_id] = now
    if model_cached(model_id):
        with _lock:
            if _unavailable.get(model_id) == REASON_NOT_DOWNLOADED:
                del _unavailable[model_id]
                _warned.discard(model_id)
                log.info("embedding model %s was downloaded by another process; loading it", model_id)


def get_embedder(model_id: str | None = None) -> Embedder:
    """The embedder for ``model_id`` (default from RADAR_EMBEDDER); fallback decided once per process
    (until :func:`reset`, or until a model that was missing has been downloaded by another process).

    A known but unavailable model (not downloaded, failed to load) - or an unknown *default* - yields
    another fastembed model whose files are downloaded, else ``local/hashing-v1``; always use the
    returned ``emb.model_id``. An unknown explicit ``model_id`` raises ``ValueError``.
    """
    requested = (model_id if model_id is not None else _default_model_id()).strip()
    if model_id is not None and requested not in _FACTORIES:
        raise ValueError(f"unknown embedding model: {requested!r}")
    _recheck_download(requested)
    emb = _try_load(requested)
    if emb is not None:
        return emb
    fallback: Embedder | None = None
    for candidate in _fallbacks(requested):
        fallback = _try_load(candidate)
        if fallback is not None:
            break
    with _lock:
        reason = _unavailable.get(requested, REASON_UNKNOWN)
        first_time = requested not in _warned
        _warned.add(requested)
    if fallback is None:
        raise RuntimeError(f"no embedding model is available ({_unavailable.get(HASHING_MODEL_ID)})")
    if first_time:
        log.warning("embedding model %s unavailable (%s); using %s", requested, reason, fallback.model_id)
    return fallback


def available_embedders() -> list[EmbedderInfo]:
    """Every known embedder with availability and the reason when unavailable.

    Never loads a model: a model that was not tried yet is reported from its install/cache state.
    """
    infos: list[EmbedderInfo] = []
    for model_id, dim in KNOWN_DIMENSIONS.items():
        available, reason = _availability(model_id)
        infos.append(EmbedderInfo(model_id=model_id, dim=dim, available=available, reason=reason))
    return infos


def _availability(model_id: str) -> tuple[bool, str | None]:
    with _lock:
        if model_id in _loaded:
            return True, None
        if model_id in _unavailable:
            return False, _unavailable[model_id]
    spec = fastembed_backend.SPECS.get(model_id)
    if spec is not None:
        if not fastembed_backend.fastembed_installed():
            return False, REASON_NOT_INSTALLED
        if not fastembed_backend.is_cached(spec.name):
            return False, REASON_NOT_DOWNLOADED
    return True, None


def fallback_active() -> bool:
    """True when the default model is unavailable and another model (see :func:`get_embedder`) is used."""
    return get_embedder().model_id != _default_model_id()


def known_dimension(model_id: str) -> int | None:
    """The vector dimension of ``model_id`` when this build knows it (loaded or built-in), else ``None``."""
    with _lock:
        emb = _loaded.get(model_id)
    if emb is not None:
        return int(emb.dim)
    return KNOWN_DIMENSIONS.get(model_id)


def reset() -> None:
    """Forget cached embedders and the fallback decision (tests / after a download)."""
    with _lock:
        _loaded.clear()
        _unavailable.clear()
        _warned.clear()
        _rechecked.clear()


def model_cached(model_id: str | None = None) -> bool:
    """True when the model files are present locally.

    ``local/hashing-v1`` needs no files (always ``True``); an unknown model is never cached. Only the
    files are checked here; loading them is :func:`get_embedder`'s job.
    """
    target = (model_id if model_id is not None else _default_model_id()).strip()
    if target == HASHING_MODEL_ID:
        return True
    spec = fastembed_backend.SPECS.get(target)
    if spec is not None:
        return fastembed_backend.is_cached(spec.name)
    return False


def _progress(ctx: Any, fraction: float | None, message: str) -> None:
    report = getattr(ctx, "progress", None)
    if report is None:
        return
    try:
        report(fraction, message)
    except NotImplementedError:  # job runner still a stub
        pass


def _egress_blocked(message: str) -> Exception:
    """The error for a download refused offline.

    The job runner maps ``radar.net.http.EgressBlocked`` to error code ``egress_blocked`` (SPEC §16),
    but this package must not import ``radar.net`` (SPEC §2). The class is therefore used only when
    that module is already loaded (always the case under the app or the runner); otherwise
    :class:`ModelDownloadBlocked`.
    """
    net_http = sys.modules.get("radar.net.http")
    cls = getattr(net_http, "EgressBlocked", None) if net_http is not None else None
    if isinstance(cls, type) and issubclass(cls, Exception):
        return cls(message)
    return ModelDownloadBlocked(message)


def run_download_model(ctx: JobContext, model_id: str | None = None) -> dict[str, Any]:
    """Job ``download_model``: download the model into ``<data>/models/fastembed`` (never offline).

    ``model_id`` defaults to RADAR_EMBEDDER; ``local/hashing-v1`` needs no download (returns at once).
    Hugging Face offline mode is lifted only while the files are fetched; afterwards the registry is
    :func:`reset` and the model is loaded once to verify it. Returns stats: ``model_id``, ``dim``,
    ``downloaded``, ``download_bytes``, ``cache_bytes``, ``seconds``.
    """
    settings = get_settings()
    target = (model_id if model_id is not None else settings.embedder).strip()
    if target not in KNOWN_DIMENSIONS:
        raise ValueError(f"unknown embedding model: {target!r}")
    if target == HASHING_MODEL_ID:
        _progress(ctx, 1.0, "local/hashing-v1 needs no download")
        return {"model_id": target, "dim": KNOWN_DIMENSIONS[target], "downloaded": False, "download_bytes": 0}
    if settings.offline:
        raise _egress_blocked("Model download is disabled while RADAR_OFFLINE=1")
    if not fastembed_backend.fastembed_installed():
        raise ModelDownloadFailed(REASON_NOT_INSTALLED, code="internal_error")
    check_not_holding_write("download_model")

    cache_dir = fastembed_backend.default_cache_dir()
    with _download_lock:
        before = fastembed_backend.cache_bytes(cache_dir)
        _progress(ctx, 0.05, f"Downloading {target} from Hugging Face")
        started = time.perf_counter()
        try:
            fastembed_backend.download_model_files(fastembed_backend.spec_for(target).name, cache_dir)
        except Exception as exc:
            log.warning("model download failed (%s)", type(exc).__name__, exc_info=True)
            raise ModelDownloadFailed(
                f"Model download failed ({type(exc).__name__}); check the network connection and retry"
            ) from exc
        seconds = time.perf_counter() - started
        after = fastembed_backend.cache_bytes(cache_dir)
        reset()
        _progress(ctx, 0.9, "Loading the downloaded model")
        emb = get_embedder(target)
        if emb.model_id != target:
            with _lock:
                reason = _unavailable.get(target, REASON_UNKNOWN)
            raise ModelDownloadFailed(
                f"Model downloaded but could not be loaded: {reason}", code="internal_error"
            )
    log.info(
        "embedding model %s ready (%d bytes downloaded in %.1f s)", target, max(0, after - before), seconds
    )
    _progress(ctx, 1.0, "Model ready")
    return {
        "model_id": target,
        "dim": int(emb.dim),
        "downloaded": after > before,
        "download_bytes": max(0, after - before),
        "cache_bytes": after,
        "seconds": round(seconds, 3),
    }
