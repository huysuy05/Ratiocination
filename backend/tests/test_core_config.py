"""Settings (SPEC §3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from radar.config import REPO_ROOT, Settings, env_file_path, get_settings, is_loopback


def test_test_environment_settings(radar_data_dir: Path) -> None:
    s = get_settings()
    assert s.offline is True
    assert s.strict_tx is True
    assert s.jobs_inline is True
    assert s.embedder == "local/hashing-v1"
    assert s.data_dir == radar_data_dir.resolve()
    assert s.data_dir != (REPO_ROOT / "data").resolve()
    assert s.database_url == f"sqlite:///{radar_data_dir / 'radar.db'}"
    assert s.openalex_api_key is None and s.s2_api_key is None and s.contact_email is None
    assert s.auth == "local" and s.anthropic_api_key is None
    assert not hasattr(s, "access_token") and not hasattr(s, "privacy_mode")
    assert env_file_path() is None


def test_defaults_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RADAR_DATA_DIR", "RADAR_DATABASE_URL", "RADAR_OFFLINE", "RADAR_STRICT_TX", "RADAR_JOBS_INLINE",
                 "RADAR_EMBEDDER"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.data_dir == (REPO_ROOT / "data").resolve()
    assert s.database_url == f"sqlite:///{(REPO_ROOT / 'data').resolve() / 'radar.db'}"
    assert (s.host, s.port) == ("127.0.0.1", 8000)
    assert s.offline is False and s.strict_tx is False and s.jobs_inline is False
    assert s.embedder == "fastembed/BAAI/bge-small-en-v1.5"
    assert s.scheduler is False
    assert (s.daily_cron, s.timezone) == ("30 0 * * *", "America/New_York")
    assert s.openalex_daily_budget_usd == 0.1
    assert s.budget_stop_fraction == 0.8
    assert s.s2_breaker_minutes == 15
    assert s.max_expansions_per_day == 10
    assert s.max_upload_mb == 50 and s.max_upload_bytes == 50 * 1024 * 1024
    assert s.allowed_hosts == []
    assert s.uploads_dir == s.data_dir / "uploads"
    assert s.models_dir == s.data_dir / "models"
    assert s.logs_dir == s.data_dir / "logs"
    assert s.db_backend == "sqlite"
    assert s.bind_is_loopback is True


def test_openalex_budget_depends_on_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "zq-key-123")
    get_settings.cache_clear()
    assert get_settings().openalex_daily_budget_usd == 1.0
    monkeypatch.setenv("RADAR_OPENALEX_DAILY_BUDGET_USD", "0.5")
    get_settings.cache_clear()
    assert get_settings().openalex_daily_budget_usd == 0.5
    monkeypatch.delenv("OPENALEX_API_KEY")
    monkeypatch.setenv("RADAR_OPENALEX_DAILY_BUDGET_USD", "")
    get_settings.cache_clear()
    assert get_settings().openalex_daily_budget_usd == 0.1


def test_env_values_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_ALLOWED_HOSTS", " radar.local ,10.0.0.5,, ")
    monkeypatch.setenv("RADAR_PORT", "8123")
    monkeypatch.setenv("S2_API_KEY", "  ")
    monkeypatch.setenv("RADAR_CONTACT_EMAIL", "zq@example.invalid")
    monkeypatch.setenv("RADAR_SCHEDULER", "true")
    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://u@localhost/radar")
    get_settings.cache_clear()
    s = get_settings()
    assert s.allowed_hosts == ["radar.local", "10.0.0.5"]
    assert s.port == 8123
    assert s.s2_api_key is None
    assert s.contact_email == "zq@example.invalid"
    assert s.scheduler is True
    assert s.db_backend == "postgresql"
    assert "radar.local" in s.trusted_hosts and "[::1]" in s.trusted_hosts
    assert "zq@example.invalid" not in repr(s)


def test_secrets_hidden_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = {
        "OPENALEX_API_KEY": "zqsecretkey9",
        "RADAR_GOOGLE_CLIENT_SECRET": "zqgooglesecret",
        "RADAR_GITHUB_CLIENT_SECRET": "zqgithubsecret",
        "RADAR_SMTP_PASSWORD": "zqsmtppassword",
        "ANTHROPIC_API_KEY": "zqanthropickey",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    text = repr(get_settings()) + str(get_settings())
    assert not [v for v in secrets.values() if v in text]


def test_account_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RADAR_AUTH",):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.auth == "accounts" and s.password_enabled and not s.google_enabled and not s.github_enabled
    assert (s.public_origin, s.cookie_secure, s.session_days) == ("http://localhost:8000", False, 30)
    assert s.summary_model == "claude-opus-5-5" and not s.ai_summary_configured
    monkeypatch.setenv("RADAR_PUBLIC_URL", "https://radar.example.org/")
    monkeypatch.setenv("RADAR_ALLOWED_EMAILS", "Ada@Example.org, @lab.example.org")
    monkeypatch.setenv("RADAR_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("RADAR_GOOGLE_CLIENT_SECRET", "secret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.public_origin, s.cookie_secure, s.google_enabled) == ("https://radar.example.org", True, True)
    assert "radar.example.org" in s.trusted_hosts
    assert s.allowed_emails == ["ada@example.org", "@lab.example.org"]
    assert s.email_allowed("ADA@example.org") and s.email_allowed("bob@lab.example.org")
    assert not s.email_allowed("eve@example.org")
    monkeypatch.setenv("RADAR_AUTH", "local")
    assert not Settings(_env_file=None).google_enabled  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RADAR_TIMEZONE", "Mars/Olympus"),
        ("RADAR_DAILY_CRON", "every day"),
        ("RADAR_AUTH", "public"),
        ("RADAR_PUBLIC_URL", "radar.example.org"),
        ("RADAR_PUBLIC_URL", "https://radar.example.org/app"),
        ("RADAR_SMTP_SECURITY", "tls"),
        ("RADAR_PORT", "70000"),
        ("RADAR_BUDGET_STOP_FRACTION", "0"),
    ],
)
def test_invalid_settings_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        get_settings()


def test_dotenv_file_is_read_and_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "OPENALEX_API_KEY=zq-from-file\nRADAR_MAX_UPLOAD_MB=7\nRADAR_PORT=9001\nUNRELATED_SETTING=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RADAR_ENV_FILE", str(env_file))
    monkeypatch.setenv("RADAR_PORT", "9002")
    get_settings.cache_clear()
    s = get_settings()
    assert env_file_path() == env_file
    assert s.openalex_api_key == "zq-from-file"
    assert s.max_upload_mb == 7
    assert s.port == 9002


def test_default_env_file_is_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_ENV_FILE")
    assert env_file_path() == REPO_ROOT / ".env"


def test_relative_data_dir_is_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RADAR_DATA_DIR", "rel-data")
    monkeypatch.delenv("RADAR_DATABASE_URL")
    get_settings.cache_clear()
    s = get_settings()
    assert s.data_dir == (tmp_path / "rel-data").resolve()
    assert s.database_url.endswith("rel-data/radar.db")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.8.9.10", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("::ffff:10.0.0.1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("radar.local", False),
        ("", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
    first = get_settings()
    get_settings.cache_clear()
    assert get_settings() is not first
