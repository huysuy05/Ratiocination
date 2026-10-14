"""Application settings (SPEC §3).

Values come from environment variables and the repo-root ``.env`` file (environment wins).
``get_settings()`` is cached; tests change the environment and call ``get_settings.cache_clear()``.

Extra knobs that are not product settings:

* ``RADAR_STRICT_TX`` - transaction strict mode (SPEC §2.1), on in tests.
* ``RADAR_JOBS_INLINE`` - the job runner executes a submitted job synchronously right after it is
  dispatched (used by tests).
* ``RADAR_ENV_FILE`` - path of the dotenv file to read instead of ``<repo>/.env``; an empty value
  disables dotenv loading (the test suite does this so a developer's ``.env`` never leaks into tests).
* ``RADAR_DEV_ORIGINS`` - extra allowed ``Origin`` values (comma list, e.g. ``http://localhost:5174``)
  for a Vite dev server on a non-default port; empty by default (SPEC §17 list only).

Secrets (``openalex_api_key``, ``s2_api_key``, ``contact_email``, OAuth client secrets, the SMTP
password, ``anthropic_api_key``) are plain ``str | None`` values hidden from ``repr``; they are never
returned by the API or logged.
"""

from __future__ import annotations

import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from radar import __version__

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
"""Repository root (derived from this file: ``<repo>/backend/radar/config.py``)."""
BACKEND_DIR: Path = REPO_ROOT / "backend"
MIGRATIONS_DIR: Path = BACKEND_DIR / "migrations"
WEB_DIST_DIR: Path = REPO_ROOT / "web" / "dist"
DEFAULT_DATA_DIR: Path = REPO_ROOT / "data"
DEFAULT_ENV_FILE: Path = REPO_ROOT / ".env"

DEFAULT_EMBEDDER = "fastembed/BAAI/bge-small-en-v1.5"
HASHING_EMBEDDER = "local/hashing-v1"
VITE_DEV_PORT = 5173
VERSION = __version__

AuthMode = Literal["local", "accounts"]
SmtpSecurity = Literal["starttls", "ssl", "none"]
DEFAULT_SUMMARY_MODEL = "claude-opus-5-5"

_LOOPBACK_NAMES = frozenset({"localhost", "localhost.", "ip6-localhost", "ip6-loopback"})


def is_loopback(host: str) -> bool:
    """True when ``host`` names the local machine only (``127.0.0.0/8``, ``::1``, ``localhost``).

    Accepts bracketed IPv6 literals (``[::1]``). ``0.0.0.0``/``::`` (all interfaces) are NOT loopback.
    """
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped.is_loopback
    return addr.is_loopback


class Settings(BaseSettings):
    """All runtime configuration (SPEC §3). Field ``x`` reads ``RADAR_X`` unless noted."""

    model_config = SettingsConfigDict(
        env_prefix="RADAR_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        validate_default=True,
        populate_by_name=True,
    )

    data_dir: Path = DEFAULT_DATA_DIR
    database_url: str = ""  # empty -> sqlite:///<data_dir>/radar.db (filled by the validator)
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    tls_cert: str | None = None
    tls_key: str | None = None
    behind_tls_proxy: bool = False
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra allowed Origin values for non-GET /api requests (e.g. a Vite dev server on another port).
    dev_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # RADAR_CLIENT_IP_HEADER: header the TLS proxy sets to the real client IP (Fly.io: Fly-Client-IP);
    # replaces the socket peer as the client address (per-IP sign-in throttle, access log). Lowercased.
    client_ip_header: str | None = None
    offline: bool = False
    strict_tx: bool = False
    jobs_inline: bool = False
    embedder: str = DEFAULT_EMBEDDER
    scheduler: bool = False  # RADAR_SCHEDULER: seeds the app_settings key scheduler_enabled
    daily_cron: str = "30 0 * * *"
    timezone: str = "America/New_York"
    openalex_api_key: str | None = Field(None, validation_alias=AliasChoices("OPENALEX_API_KEY"), repr=False)
    openalex_daily_budget_override: float | None = Field(
        None, ge=0, validation_alias=AliasChoices("RADAR_OPENALEX_DAILY_BUDGET_USD")
    )
    budget_stop_fraction: float = Field(0.8, gt=0, le=1)
    s2_api_key: str | None = Field(None, validation_alias=AliasChoices("S2_API_KEY"), repr=False)
    s2_breaker_minutes: float = Field(15, ge=0)
    # RADAR_ARXIV_BREAKER_MINUTES: pause export.arxiv.org after a 429 (feed + OAI-PMH unaffected).
    arxiv_breaker_minutes: float = Field(10, ge=0)
    contact_email: str | None = Field(None, repr=False)
    max_expansions_per_day: int = Field(10, ge=0)
    max_upload_mb: int = Field(50, ge=1)

    # ---- accounts (SPEC §5) ----
    auth: AuthMode = "accounts"
    public_url: str = ""  # empty -> http://localhost:<port>
    password_login: bool = True
    google_client_id: str | None = None
    google_client_secret: str | None = Field(None, repr=False)
    github_client_id: str | None = None
    github_client_secret: str | None = Field(None, repr=False)
    allowed_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    session_days: int = Field(30, ge=1, le=365)
    smtp_host: str | None = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = Field(None, repr=False)
    smtp_from: str | None = None
    smtp_security: SmtpSecurity = "starttls"
    # RADAR_REQUIRE_EMAIL_VERIFICATION: a password account gets a session only after it confirms its
    # email (SPEC §5.3). Unset = on when the server is public (``is_public``), off on loopback only.
    require_email_verification: bool | None = None

    # ---- AI summary and per-user quotas (SPEC §3, §14.4) ----
    anthropic_api_key: str | None = Field(
        None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"), repr=False
    )
    summary_model: str = DEFAULT_SUMMARY_MODEL
    summaries_per_day: int = Field(5, ge=0)
    # RADAR_SUMMARIES_GLOBAL_PER_DAY: AI summaries per UTC day for all users together (bounds the daily
    # Anthropic spend however many accounts exist). At least 1: there is no "unlimited".
    summaries_global_per_day: int = Field(50, ge=1)
    breakthroughs_per_day: int = Field(5, ge=0)
    expansions_per_user_per_day: int = Field(30, ge=0)
    user_storage_mb: int = Field(500, ge=1)
    max_projects: int = Field(50, ge=1)

    # ---- validators -------------------------------------------------------------------------

    @field_validator(
        "tls_cert",
        "tls_key",
        "openalex_api_key",
        "s2_api_key",
        "contact_email",
        "google_client_id",
        "google_client_secret",
        "github_client_id",
        "github_client_secret",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "anthropic_api_key",
        "require_email_verification",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("openalex_daily_budget_override", mode="before")
    @classmethod
    def _blank_budget(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute_data_dir(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @field_validator("allowed_hosts", "dev_origins", mode="before")
    @classmethod
    def _split_hosts(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @field_validator("client_ip_header", mode="before")
    @classmethod
    def _valid_header_name(cls, v: Any) -> Any:
        if v is None:
            return None
        v = str(v).strip()
        if not v:
            return None
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", v):
            raise ValueError("RADAR_CLIENT_IP_HEADER must be a header name such as Fly-Client-IP")
        return v.lower()

    @field_validator("allowed_emails", "admin_emails", mode="before")
    @classmethod
    def _split_emails(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [e.strip().lower() for e in v.split(",") if e.strip()]
        return [str(e).strip().lower() for e in v if str(e).strip()]

    @field_validator("public_url", mode="after")
    @classmethod
    def _valid_public_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            return v
        parsed = urlsplit(v)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path or parsed.query:
            raise ValueError("RADAR_PUBLIC_URL must look like https://radar.example.org (no path)")
        return v

    @field_validator("host", mode="after")
    @classmethod
    def _strip_host(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("RADAR_HOST must not be empty")
        return v

    @field_validator("timezone", mode="after")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("RADAR_TIMEZONE must be an IANA time zone name") from exc
        return v

    @field_validator("daily_cron", mode="after")
    @classmethod
    def _cron_has_five_fields(cls, v: str) -> str:
        if len(v.split()) != 5:
            raise ValueError("RADAR_DAILY_CRON must be a 5-field crontab expression")
        return v

    @model_validator(mode="after")
    def _derived_defaults(self) -> Settings:
        if not self.database_url.strip():
            self.database_url = f"sqlite:///{self.data_dir / 'radar.db'}"
        return self

    # ---- derived values -----------------------------------------------------------------------

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def openalex_daily_budget_usd(self) -> float:
        """OpenAlex daily budget: RADAR_OPENALEX_DAILY_BUDGET_USD, else $1.00 with a key, $0.10 without (D7)."""
        if self.openalex_daily_budget_override is not None:
            return float(self.openalex_daily_budget_override)
        return 1.0 if self.openalex_api_key else 0.1

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def db_backend(self) -> Literal["sqlite", "postgresql"]:
        """``"sqlite"`` or ``"postgresql"`` (from the SQLAlchemy URL scheme)."""
        scheme = self.database_url.split(":", 1)[0].lower()
        return "postgresql" if scheme.startswith("postgres") else "sqlite"

    @property
    def is_sqlite(self) -> bool:
        return self.db_backend == "sqlite"

    @property
    def bind_is_loopback(self) -> bool:
        return is_loopback(self.host)

    @property
    def is_public(self) -> bool:
        """Reachable from other machines: a non-loopback bind, a TLS proxy in front, or a
        ``RADAR_PUBLIC_URL`` whose host is not loopback (a same-host reverse proxy). A public server
        must pass ``main._check_bind`` (accounts, admin emails, TLS, an https public URL)."""
        if not self.bind_is_loopback or self.behind_tls_proxy:
            return True
        public_host = urlsplit(self.public_url).hostname if self.public_url else None
        return bool(public_host) and not is_loopback(public_host or "")

    @property
    def email_verification_required(self) -> bool:
        """``RADAR_REQUIRE_EMAIL_VERIFICATION`` when set, else on for a public server (SPEC §5.3)."""
        if self.require_email_verification is not None:
            return self.require_email_verification
        return self.is_public

    @property
    def tls_configured(self) -> bool:
        """TLS terminates here (cert + key) or at a reverse proxy (RADAR_BEHIND_TLS_PROXY=1)."""
        return bool(self.tls_cert and self.tls_key) or self.behind_tls_proxy

    @property
    def trusted_hosts(self) -> list[str]:
        """Host header values accepted by the TrustedHost guard (SPEC §17)."""
        hosts: list[str] = ["127.0.0.1", "localhost", "[::1]", "::1"]
        public_host = urlsplit(self.public_url).hostname if self.public_url else None
        for h in [self.host, *self.allowed_hosts, *([public_host] if public_host else [])]:
            h = h.strip().lower()
            if not h:
                continue
            if ":" in h and not h.startswith("["):
                try:
                    if ipaddress.ip_address(h).version == 6:
                        h = f"[{h}]"
                except ValueError:
                    pass
            if h not in hosts:
                hosts.append(h)
        return hosts

    @property
    def public_origin(self) -> str:
        """``RADAR_PUBLIC_URL`` without a trailing slash, else ``http://localhost:<port>``."""
        return self.public_url or f"http://localhost:{self.port}"

    @property
    def cookie_secure(self) -> bool:
        """Session cookies get ``Secure`` (and the ``__Host-`` prefix) when the public URL is https."""
        return self.public_origin.startswith("https://")

    @property
    def google_enabled(self) -> bool:
        return self.auth == "accounts" and bool(self.google_client_id and self.google_client_secret)

    @property
    def github_enabled(self) -> bool:
        return self.auth == "accounts" and bool(self.github_client_id and self.github_client_secret)

    @property
    def password_enabled(self) -> bool:
        return self.auth == "accounts" and self.password_login

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def ai_summary_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    def email_allowed(self, email: str) -> bool:
        """``RADAR_ALLOWED_EMAILS`` check: exact addresses or ``@domain`` entries; empty = anyone."""
        if not self.allowed_emails:
            return True
        email = email.strip().lower()
        domain = "@" + email.rsplit("@", 1)[-1]
        return email in self.allowed_emails or domain in self.allowed_emails

    @property
    def version(self) -> str:
        return VERSION


def env_file_path() -> Path | None:
    """The dotenv file to read: ``RADAR_ENV_FILE`` if set (empty disables), else ``<repo>/.env``."""
    raw = os.environ.get("RADAR_ENV_FILE")
    if raw is None:
        return DEFAULT_ENV_FILE
    raw = raw.strip()
    if not raw:
        return None
    return Path(raw).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached). Call ``get_settings.cache_clear()`` after changing the env."""
    env_file = env_file_path()
    if env_file is not None and env_file.is_file():
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings(_env_file=None)  # type: ignore[call-arg]
