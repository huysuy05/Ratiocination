"""Password hashing and rules (SPEC §5.3). Owner: auth.

Argon2id via ``argon2-cffi``'s ``PasswordHasher()`` (RFC 9106 low-memory profile: t=3, m=64 MiB,
p=4; ~40 ms per hash). Hashing is CPU work: callers run it outside write transactions.

Every hash and verification (the dummy hash too) holds one of :data:`HASH_CONCURRENCY` slots of a
process-wide semaphore: each Argon2 call allocates 64 MiB, and sync handlers run on up to 40
threads, so 40 parallel sign-ins would otherwise need ~2.5 GiB. Extra callers wait for a slot.

:func:`verify_password` also runs for unknown emails (against a dummy hash) so that a failed sign-in
takes the same time whether or not the account exists.
"""

from __future__ import annotations

import re
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 10
MAX_LENGTH = 256
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

HASH_CONCURRENCY = 2
"""Argon2 calls that may run at once (64 MiB each); the rest wait."""

_hasher = PasswordHasher()
_slots = threading.BoundedSemaphore(HASH_CONCURRENCY)
_dummy_lock = threading.Lock()
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    """Argon2id encoded hash of ``password`` (waits for a hashing slot)."""
    with _slots:
        return _hasher.hash(password)


def _dummy() -> str:
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = hash_password("ratiocination timing equalizer")
        return _dummy_hash


def verify_password(stored_hash: str | None, password: str) -> bool:
    """True when ``password`` matches ``stored_hash``; ``None`` (no account / no password) is checked
    against a dummy hash and always fails, taking the same time as a real check."""
    target = stored_hash or _dummy()
    try:
        with _slots:
            ok = _hasher.verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(ok) and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash uses older parameters than the current hasher."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def email_problem(email: str) -> str | None:
    """A human-readable reason why ``email`` is not usable, or ``None``."""
    value = normalize_email(email)
    if not value or len(value) > 320 or not EMAIL_RE.fullmatch(value):
        return "Enter a valid email address"
    return None


def password_problem(password: str, email: str | None = None) -> str | None:
    """A human-readable reason why ``password`` is not acceptable, or ``None`` (SPEC §5.3)."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters"
    if email:
        lowered = password.strip().lower()
        address = normalize_email(email)
        if lowered in (address, address.split("@", 1)[0]):
            return "Do not use your email address as your password"
    return None


__all__ = [
    "HASH_CONCURRENCY",
    "MAX_LENGTH",
    "MIN_LENGTH",
    "email_problem",
    "hash_password",
    "needs_rehash",
    "normalize_email",
    "password_problem",
    "verify_password",
]
