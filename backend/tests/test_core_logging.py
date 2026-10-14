"""Logging setup and redaction (SPEC §17)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from radar.config import get_settings
from radar.logging_setup import (
    AccessFormatter,
    AccessLogQueryFilter,
    configure_logging,
    redact,
    redact_record,
    uvicorn_log_config,
)


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("GET https://api.openalex.org/works/W1?api_key=abc123def&select=id", "abc123def"),
        (
            "params={'mailto': 'x'} url=/works?mailto=someone%40example.org&per-page=3",
            "someone%40example.org",
        ),
        (
            "User-Agent: Ratiocination/0.1 (local research tool; mailto:someone@example.org)",
            "someone@example.org",
        ),
        ("headers={'x-api-key': 's2secretvalue', 'accept': 'json'}", "s2secretvalue"),
        ("X-API-KEY: s2secretvalue", "s2secretvalue"),
        ("Authorization: Bearer tok-123456", "tok-123456"),
        ("{'authorization': 'Bearer tok-123456'}", "tok-123456"),
    ],
)
def test_redact_patterns(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out
    assert "[REDACTED]" in out


def test_redact_configured_literals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "zqsecretkey9")
    monkeypatch.setenv("RADAR_CONTACT_EMAIL", "zq@example.invalid")
    monkeypatch.setenv("S2_API_KEY", "zqs2key")
    monkeypatch.setenv("RADAR_GITHUB_CLIENT_SECRET", "zqgithubsecret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "zqanthropickey")
    get_settings.cache_clear()
    text = (
        "failed zqsecretkey9 for zq@example.invalid / zq%40example.invalid / ZQ@EXAMPLE.INVALID "
        "with zqs2key, zqgithubsecret and zqanthropickey"
    )
    out = redact(text)
    for secret in (
        "zqsecretkey9",
        "zq@example.invalid",
        "zq%40example.invalid",
        "zqs2key",
        "zqgithubsecret",
        "zqanthropickey",
    ):
        assert secret.lower() not in out.lower()
    assert redact("nothing secret here") == "nothing secret here"


def test_redact_is_idempotent() -> None:
    once = redact("url?api_key=abc&mailto=x")
    assert redact(once) == once


def test_redact_record_keeps_arg_types() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "%s got %d rows from %s", ("job", 5, "x?api_key=k1"), None
    )
    redact_record(record)
    assert record.args[1] == 5  # type: ignore[index]
    assert record.getMessage() == "job got 5 rows from x?api_key=[REDACTED]"


def test_exception_text_is_redacted() -> None:
    try:
        raise RuntimeError("boom at https://api.openalex.org/works?api_key=zz-secret-1")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    redact_record(record)
    assert "zz-secret-1" not in (record.exc_text or "")
    assert "[REDACTED]" in (record.exc_text or "")


def test_configure_logging_writes_redacted_file(
    radar_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "zqsecretkey9")
    get_settings.cache_clear()
    configure_logging()
    configure_logging()  # idempotent: no duplicated handlers
    root = logging.getLogger()
    tagged = [h for h in root.handlers if getattr(h, "_radar_handler", False)]
    assert len(tagged) == 2
    log = logging.getLogger("radar.test")
    log.warning("calling %s", "https://api.openalex.org/works?api_key=zqsecretkey9")
    log.warning("plain key zqsecretkey9 in message")
    for handler in tagged:
        handler.flush()
    content = (radar_data_dir / "logs" / "radar.log").read_text(encoding="utf-8")
    assert "zqsecretkey9" not in content
    assert content.count("[REDACTED]") >= 2
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_records_reaching_other_handlers_are_redacted(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONTACT_EMAIL", "zq@example.invalid")
    get_settings.cache_clear()
    configure_logging()
    with caplog.at_level(logging.INFO):
        logging.getLogger("radar.test").info("polite pool %s", "mailto:zq@example.invalid")
    assert "zq@example.invalid" not in caplog.text


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", 200),
        None,
    )


def test_access_log_drops_query_string() -> None:
    record = _access_record("/api/papers?query=private+draft+words&limit=5")
    AccessLogQueryFilter().filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /api/papers HTTP/1.1" 200'
    formatter = AccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
    )
    line = formatter.format(_access_record("/api/papers?query=private+draft+words"))
    assert "private" not in line
    assert "/api/papers" in line


def test_access_filter_installed_on_uvicorn_logger() -> None:
    configure_logging()
    assert any(isinstance(f, AccessLogQueryFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_uvicorn_log_config_is_json_dictconfig() -> None:
    config = uvicorn_log_config()
    json.dumps(config)
    assert config["formatters"]["access"]["()"] == "radar.logging_setup.AccessFormatter"
    assert "strip_query" in config["handlers"]["access"]["filters"]
    assert config["loggers"]["httpx"]["level"] == "WARNING"
