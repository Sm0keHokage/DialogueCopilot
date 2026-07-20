"""Outbound email for account verification links (stdlib smtplib only).

No third-party mail dependency: delivery goes through smtplib in a worker
thread (SMTP is blocking) via asyncio.to_thread, so it never blocks the event
loop. When SMTP is not configured (local dev / tests) mail is only recorded
in `outbox` — nothing is sent, nothing raises.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger(__name__)

_OUTBOX_MAX = 100


class Emailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        #: Every attempted send lands here (used by tests and local dev to
        #: read the "sent" mail without a real mailbox); capped at 100.
        self.outbox: list[dict[str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self._remember(to, subject, body)
        settings = self._settings
        if settings.smtp_host:
            try:
                await asyncio.to_thread(self._send_sync, to, subject, body)
            except Exception as exc:  # noqa: BLE001 - mail delivery is best-effort
                log.error("mail delivery to %s failed: %s", _mask(to), type(exc).__name__)
                return
        log.info("mail queued to=%s subject=%r", _mask(to), subject)

    def _remember(self, to: str, subject: str, body: str) -> None:
        self.outbox.append({"to": to, "subject": subject, "body": body})
        if len(self.outbox) > _OUTBOX_MAX:
            del self.outbox[: len(self.outbox) - _OUTBOX_MAX]

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{name[:1]}***@{domain}"
