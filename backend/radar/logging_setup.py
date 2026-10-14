"""Logging configuration and secret redaction (SPEC §17).

* :func:`configure_logging` installs a console handler and a rotating file handler
  (``<data>/logs/radar.log``) on the root logger, sets ``httpx``/``httpcore`` to WARNING, strips query
  strings from uvicorn access-log records and installs a log-record factory that redacts every record.
* Redaction covers ``api_key=``, ``mailto=``/``mailto:``, ``x-api-key``, ``Authorization`` values,
  OAuth parameters (``client_secret``, ``code``, ``code_verifier``, ``access_token``, ``id_token``,
  ``refresh_token``, ``state``, ``token``), session cookies, and the literal configured secrets
  (``OPENALEX_API_KEY``, ``S2_API_KEY``, ``RADAR_CONTACT_EMAIL``, OAuth client secrets, the SMTP
  password, ``ANTHROPIC_API_KEY``, including their URL-encoded forms).
* :func:`uvicorn_log_config` returns a ``logging.config.dictConfig`` dict for uvicorn whose access log
  prints the path without the query string. ``python -m radar.logging_setup`` prints it as JSON
  (``scripts/dev.sh`` passes it to ``uvicorn --log-config``).

Never log private text (CONVENTIONS "Python style"): log IDs and counts.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import threading
from collections.abc import Mapping
from copy import copy
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, quote_plus

from uvicorn.logging import AccessFormatter as _UvicornAccessFormatter

if TYPE_CHECKING:
    from radar.config import Settings

REDACTED = "[REDACTED]"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_FILE_NAME = "radar.log"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 3

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(\bapi_key=)[^&\s'\"<>]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bmailto=)[^&\s'\"<>]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bmailto:)[^\s;,'\")<>]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;})]+"), r"\1" + REDACTED),
    (
        re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(?:(?:bearer|basic|token)\s+)?[^'\"\s,;})]+"),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)(\b(?:client_secret|code_verifier|access_token|id_token|refresh_token|code|state|token)"
            r"['\"]?\s*[=:]\s*['\"]?)[^&\s'\",;})<>]+"
        ),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(?i)((?:__Host-)?radar_(?:session|oauth)=)[^;\s'\"]+"), r"\1" + REDACTED),
)

_literal_lock = threading.Lock()
_literal_cache: tuple[tuple[str, ...], re.Pattern[str] | None] = ((), None)


_reentry = threading.local()


def _secret_values() -> tuple[str, ...]:
    if getattr(_reentry, "busy", False):
        # get_settings() itself logged something (e.g. a dotenv parse warning): use the last values.
        return _literal_cache[0]
    _reentry.busy = True
    try:
        from radar.config import get_settings

        s = get_settings()
    except Exception:  # pragma: no cover - never break logging
        return ()
    finally:
        _reentry.busy = False
    values: list[str] = []
    for raw in (
        s.openalex_api_key,
        s.s2_api_key,
        s.contact_email,
        s.google_client_secret,
        s.github_client_secret,
        s.smtp_password,
        s.anthropic_api_key,
    ):
        if raw and len(raw) >= 4:
            for v in (raw, quote(raw, safe=""), quote_plus(raw)):
                if v not in values:
                    values.append(v)
    return tuple(values)


def _literal_pattern() -> re.Pattern[str] | None:
    global _literal_cache
    values = _secret_values()
    cached_values, cached_pattern = _literal_cache
    if values == cached_values:
        return cached_pattern
    pattern = None
    if values:
        ordered = sorted(values, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(v) for v in ordered), re.IGNORECASE)
    with _literal_lock:
        _literal_cache = (values, pattern)
    return pattern


def redact(text: str) -> str:
    """Replace credentials and configured secret values in ``text`` with ``[REDACTED]``."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    literal = _literal_pattern()
    if literal is not None:
        text = literal.sub(REDACTED, text)
    return text


def _redact_arg(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)
    try:
        text = str(value)
    except Exception:  # pragma: no cover
        return value
    redacted = redact(text)
    return value if redacted == text else redacted


_EXC_FORMATTER = logging.Formatter()


def redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact a record in place (message, args, exception and stack text). Idempotent."""
    try:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        elif record.msg is not None:
            record.msg = _redact_arg(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_arg(a) for a in args)
        elif isinstance(args, Mapping):
            record.args = {k: _redact_arg(v) for k, v in args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(_EXC_FORMATTER.formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
    except Exception:  # pragma: no cover - never break logging
        pass
    return record


class RedactionFilter(logging.Filter):
    """Handler/logger filter that redacts records (see :func:`redact`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record)
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the final formatted text as a last line of defence."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _strip_query(path: Any) -> Any:
    if isinstance(path, str):
        return path.split("?", 1)[0]
    return path


class AccessLogQueryFilter(logging.Filter):
    """Drop the query string from uvicorn access-log records (args: client, method, path, http, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], _strip_query(args[2]), *args[3:])
        return True


class AccessFormatter(_UvicornAccessFormatter):
    """uvicorn access formatter that prints the path without its query string."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            record = copy(record)
            record.args = (args[0], args[1], _strip_query(args[2]), args[3], args[4])
        return redact(super().formatMessage(record))


_factory_lock = threading.Lock()
_factory_installed = False


def install_record_factory() -> None:
    """Wrap the global LogRecord factory so every record is redacted at creation (idempotent)."""
    global _factory_installed
    with _factory_lock:
        if _factory_installed:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            return redact_record(previous(*args, **kwargs))

        logging.setLogRecordFactory(factory)
        _factory_installed = True


def _install_access_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogQueryFilter) for f in access.filters):
        access.addFilter(AccessLogQueryFilter())


_HANDLER_TAG = "_radar_handler"


def reset_logging() -> None:
    """Remove and close the handlers :func:`configure_logging` installed (tests, shutdown)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover
                pass


def configure_logging(settings: Settings | None = None, *, level: int | str = logging.INFO) -> None:
    """Configure application logging (idempotent: replaces handlers it installed earlier)."""
    if settings is None:
        from radar.config import get_settings

        settings = get_settings()
    install_record_factory()
    _install_access_filter()
    for name in ("httpx", "httpcore", "alembic.runtime.plugins"):
        logging.getLogger(name).setLevel(logging.WARNING)

    reset_logging()
    root = logging.getLogger()

    formatter = RedactingFormatter(LOG_FORMAT)
    redaction = RedactionFilter()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(redaction)
    setattr(console, _HANDLER_TAG, True)
    root.addHandler(console)

    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            settings.logs_dir / LOG_FILE_NAME,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUPS,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redaction)
        setattr(file_handler, _HANDLER_TAG, True)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only data dir
        logging.getLogger(__name__).warning("could not open the log file; logging to the console only")

    numeric = logging.getLevelName(level) if isinstance(level, str) else level
    if isinstance(numeric, int) and (root.level == logging.NOTSET or root.level > numeric):
        root.setLevel(numeric)


def uvicorn_log_config(settings: Settings | None = None) -> dict[str, Any]:
    """dictConfig for uvicorn (``--log-config``): redacted, access log without query strings."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "redact": {"()": "radar.logging_setup.RedactionFilter"},
            "strip_query": {"()": "radar.logging_setup.AccessLogQueryFilter"},
        },
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "radar.logging_setup.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "filters": ["redact"],
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["strip_query", "redact"],
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
                "filters": ["strip_query"],
            },
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }


if __name__ == "__main__":  # pragma: no cover - used by scripts/dev.sh
    json.dump(uvicorn_log_config(), sys.stdout, indent=2)
    sys.stdout.write("\n")
