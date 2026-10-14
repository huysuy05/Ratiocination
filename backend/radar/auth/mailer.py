"""Outgoing mail for verification and reset links (SPEC §5.7). Owner: auth.

* Sent only when SMTP is configured (``RADAR_SMTP_HOST`` + ``RADAR_SMTP_FROM``) and not offline.
* The raw link token exists only in memory: delivery runs on a short-lived daemon thread (never a
  ``jobs`` row, which would store the token at rest); logs name neither the address nor the link.
* ``smtplib`` with a 20 s timeout; ``RADAR_SMTP_SECURITY`` = ``starttls`` (default), ``ssl`` or ``none``.
* Tests call :func:`capture` to collect messages instead of sending them.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage

from radar.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 20


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    body: str


_capture_lock = threading.Lock()
_captured: list[OutgoingMail] | None = None


@contextmanager
def capture() -> Iterator[list[OutgoingMail]]:
    """Collect mail instead of sending it (tests)."""
    global _captured
    box: list[OutgoingMail] = []
    with _capture_lock:
        previous, _captured = _captured, box
    try:
        yield box
    finally:
        with _capture_lock:
            _captured = previous


def can_send() -> bool:
    """True when mail would actually be sent (or captured in tests)."""
    settings = get_settings()
    with _capture_lock:
        if _captured is not None:
            return True
    return settings.smtp_configured and not settings.offline


def _message(to: str, subject: str, body: str) -> EmailMessage:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or "ratiocination@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _deliver(msg: EmailMessage) -> None:
    settings = get_settings()
    host = settings.smtp_host or ""
    try:
        if settings.smtp_security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, settings.smtp_port, timeout=TIMEOUT_S, context=ssl.create_default_context()
            )
        else:
            smtp = smtplib.SMTP(host, settings.smtp_port, timeout=TIMEOUT_S)
        with smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(msg)
        log.info("mail delivered")
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("mail delivery failed (%s)", type(exc).__name__)


def send_link_email(to: str, subject: str, intro: str, link: str) -> bool:
    """Send one plain-text email with a single link; returns False when mail is not configured."""
    body = f"{intro}\n\n{link}\n\nIf you did not ask for this, you can ignore this email.\n— Ratiocination\n"
    with _capture_lock:
        if _captured is not None:
            _captured.append(OutgoingMail(to=to, subject=subject, body=body))
            return True
    if not can_send():
        log.info("mail not sent: SMTP is not configured or the server is offline")
        return False
    threading.Thread(
        target=_deliver, args=(_message(to, subject, body),), daemon=True, name="radar-mail"
    ).start()
    return True


__all__ = ["OutgoingMail", "can_send", "capture", "send_link_email"]
