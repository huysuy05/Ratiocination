"""In-memory sliding-window throttles for sign-in, sign-up and emails (SPEC §5.4). Owner: auth.

One uvicorn worker means one process, so a process-local counter is exact. Windows:

* failed sign-ins per email: 5 per 15 minutes;
* sign-in + sign-up attempts per client IP: 30 per 15 minutes (resending a verification link while
  signed out counts as one);
* verification/reset emails per address: 5 per hour;
* wrong current passwords on ``POST /api/me/password`` per user id: 5 per 15 minutes.

Exceeding a window raises ``ApiError(429, "rate_limited")`` with ``Retry-After``.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from radar.errors import ApiError


class SlidingWindow:
    """At most ``limit`` events per ``window_s`` seconds per key."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.setdefault(key, deque())
        while events and events[0] <= now - self.window_s:
            events.popleft()
        return events

    def retry_after(self, key: str) -> float | None:
        """Seconds until one more event is allowed, or ``None`` when allowed now."""
        with self._lock:
            now = self.clock()
            events = self._prune(key, now)
            if len(events) < self.limit:
                if not events:
                    self._events.pop(key, None)
                return None
            return max(0.0, events[0] + self.window_s - now)

    def hit(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune(key, now).append(now)

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


failed_signins = SlidingWindow(5, 15 * 60)
attempts_by_ip = SlidingWindow(30, 15 * 60)
emails_by_address = SlidingWindow(5, 60 * 60)
failed_password_checks = SlidingWindow(5, 15 * 60)


def _raise(seconds: float, message: str) -> None:
    wait = max(1, math.ceil(seconds))
    raise ApiError(
        429,
        "rate_limited",
        message,
        details={"retry_after_seconds": wait},
    )


def check_attempt(ip: str | None) -> None:
    """Count a sign-in/sign-up attempt from ``ip``; 429 when the IP is over its window."""
    key = ip or "unknown"
    wait = attempts_by_ip.retry_after(key)
    if wait is not None:
        _raise(wait, "Too many attempts from this network. Try again later.")
    attempts_by_ip.hit(key)


def check_signin(email: str) -> None:
    """429 when ``email`` had too many failed sign-ins recently."""
    wait = failed_signins.retry_after(email)
    if wait is not None:
        _raise(wait, "Too many failed sign-ins for this account. Try again later.")


def record_failure(email: str) -> None:
    failed_signins.hit(email)


def record_success(email: str) -> None:
    failed_signins.clear(email)


def check_email_send(email: str) -> None:
    """Count an outgoing verification/reset email; 429 when the address got too many recently."""
    wait = emails_by_address.retry_after(email)
    if wait is not None:
        _raise(wait, "Too many emails were sent to this address. Try again later.")
    emails_by_address.hit(email)


def check_password_change(user_id: int) -> None:
    """429 when user ``user_id`` typed a wrong current password too often recently."""
    wait = failed_password_checks.retry_after(str(user_id))
    if wait is not None:
        _raise(wait, "Too many wrong passwords. Try again later.")


def record_password_failure(user_id: int) -> None:
    failed_password_checks.hit(str(user_id))


def record_password_success(user_id: int) -> None:
    failed_password_checks.clear(str(user_id))


def reset() -> None:
    """Forget every counter (tests)."""
    for window in (failed_signins, attempts_by_ip, emails_by_address, failed_password_checks):
        window.clear()


__all__ = [
    "SlidingWindow",
    "check_attempt",
    "check_email_send",
    "check_password_change",
    "check_signin",
    "record_failure",
    "record_password_failure",
    "record_password_success",
    "record_success",
    "reset",
]
