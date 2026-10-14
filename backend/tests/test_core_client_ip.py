"""RADAR_CLIENT_IP_HEADER (SPEC §3, §5.4): behind a TLS proxy such as Fly.io, a header the proxy sets
(``Fly-Client-IP``) replaces the socket peer as the client address, so the per-IP sign-in throttle sees
real clients instead of one proxy address."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from radar.config import Settings
from radar.main import ClientIpHeaderMiddleware

BASE = "http://localhost:8000"


def test_setting_is_normalized_and_validated() -> None:
    assert Settings(_env_file=None).client_ip_header is None  # type: ignore[call-arg]
    assert Settings(_env_file=None, client_ip_header="  ").client_ip_header is None  # type: ignore[call-arg]
    s = Settings(_env_file=None, client_ip_header=" Fly-Client-IP ")  # type: ignore[call-arg]
    assert s.client_ip_header == "fly-client-ip"
    for bad in ("Fly Client IP", "x:y", "a" * 65):
        with pytest.raises(ValidationError, match="RADAR_CLIENT_IP_HEADER"):
            Settings(_env_file=None, client_ip_header=bad)  # type: ignore[call-arg]


def _client_seen(
    headers: list[tuple[bytes, bytes]], peer: tuple[str, int] | None = ("10.0.0.2", 4711)
) -> Any:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["client"] = scope.get("client")

    middleware = ClientIpHeaderMiddleware(app, header="Fly-Client-IP")  # type: ignore[arg-type]
    scope: dict[str, Any] = {"type": "http", "headers": headers, "client": peer}
    asyncio.run(middleware(scope, None, None))  # type: ignore[arg-type]
    return seen["client"]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"fly-client-ip", b"203.0.113.7")], ("203.0.113.7", 4711)),
        ([(b"fly-client-ip", b" 2001:DB8::1 ")], ("2001:db8::1", 4711)),
        ([(b"fly-client-ip", b"[2001:db8::2]")], ("2001:db8::2", 4711)),
        ([(b"fly-client-ip", b"198.51.100.1"), (b"fly-client-ip", b"203.0.113.9")], ("203.0.113.9", 4711)),
        ([(b"fly-client-ip", b"not-an-ip")], ("10.0.0.2", 4711)),
        ([(b"fly-client-ip", b"203.0.113.7, 10.0.0.1")], ("10.0.0.2", 4711)),
        ([(b"x-forwarded-for", b"203.0.113.7")], ("10.0.0.2", 4711)),
        ([], ("10.0.0.2", 4711)),
    ],
)
def test_middleware_replaces_the_peer_only_with_a_valid_ip(
    headers: list[tuple[bytes, bytes]], expected: tuple[str, int]
) -> None:
    assert _client_seen(headers) == expected


def test_middleware_without_a_peer() -> None:
    assert _client_seen([(b"fly-client-ip", b"203.0.113.7")], peer=None) == ("203.0.113.7", 0)
    assert _client_seen([], peer=None) is None


def _signup_attempt(client: TestClient, ip: str) -> int:
    # Counts as a sign-up attempt for the IP, then fails validation (no password hashing).
    r = client.post(
        "/api/auth/signup",
        json={"email": "not-an-email", "password": "long enough password"},
        headers={"Fly-Client-IP": ip},
    )
    return r.status_code


@pytest.fixture
def accounts_client(set_env: Callable[..., None]) -> Callable[[], TestClient]:
    def make() -> TestClient:
        from radar.main import create_app

        return TestClient(create_app(), base_url=BASE, headers={"X-Radar-Client": "web"})

    set_env(RADAR_AUTH="accounts", RADAR_PUBLIC_URL=BASE)
    return make


def test_per_ip_throttle_uses_the_header_when_configured(
    set_env: Callable[..., None], accounts_client: Callable[[], TestClient]
) -> None:
    set_env(RADAR_CLIENT_IP_HEADER="Fly-Client-IP")
    with accounts_client() as client:
        for _ in range(30):
            assert _signup_attempt(client, "203.0.113.7") == 422
        assert _signup_attempt(client, "203.0.113.7") == 429
        assert _signup_attempt(client, "203.0.113.8") == 422  # another client behind the same proxy


def test_header_is_ignored_when_not_configured(accounts_client: Callable[[], TestClient]) -> None:
    with accounts_client() as client:
        for _ in range(30):
            assert _signup_attempt(client, "203.0.113.7") == 422
        assert _signup_attempt(client, "203.0.113.8") == 429  # same socket peer: the header changes nothing
