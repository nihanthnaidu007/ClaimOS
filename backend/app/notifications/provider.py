"""Notification drivers: the pluggable delivery boundary for customer email.

`NotificationProvider` is the interface; `ConsoleDriver` is the default — it
logs every notification as a structured event, which is the audit-friendly
stand-in for real delivery (F1). `SmtpEmailDriver` sends actual email: it
renders the pre-written templates in `templates.py` and delivers both MIME
parts over SMTP. Delivery drivers never raise into their callers: a failed
notification must not fail the pipeline event or API call that triggered it,
so `dispatch_milestone` and the email helpers treat driver exceptions as
logged degradation.
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import structlog

from app.config import settings
from app.notifications.templates import RenderedEmail, render_email

logger = structlog.get_logger("claimos.notifications")


@dataclass(frozen=True)
class Notification:
    """One customer-facing notification. Milestones are persisted first (the
    in-app center renders the record); access-code and reply-notice emails are
    transactional one-offs. `milestone` doubles as the template key: a pipeline
    milestone key, or `access_code` / `reply_notice` for direct emails."""

    id: str
    claim_id: str
    recipient_email: str
    milestone: str
    title: str
    body: str
    created_at: str
    # Extra template variables beyond the claim number (e.g. the access code
    # itself). Milestone notifications render from the milestone key alone.
    template_vars: dict[str, str] = field(default_factory=dict)


class NotificationProvider(Protocol):
    """Delivery boundary — implement this to add email/SMS/push drivers."""

    async def send(self, notification: Notification) -> None: ...


class ConsoleDriver:
    """Default driver: emits a structured `notification_dispatched` log event."""

    async def send(self, notification: Notification) -> None:
        logger.info(
            "notification_dispatched",
            notification_id=notification.id,
            claim_id=notification.claim_id,
            recipient_email=notification.recipient_email,
            milestone=notification.milestone,
            title=notification.title,
            body=notification.body,
            driver="console",
        )


class DisabledDriver:
    """EMAIL_DISABLED=true: drop every notification without touching a channel."""

    async def send(self, notification: Notification) -> None:
        logger.info(
            "notification_dropped",
            notification_id=notification.id,
            claim_id=notification.claim_id,
            milestone=notification.milestone,
            reason="email_disabled",
        )


# smtplib is blocking; the driver runs it off the event loop. 30s covers a
# three-attempt TCP/TLS conversation without stalling an API response long.
_SMTP_TIMEOUT_S = 30


def _smtp_send(recipient: str, rendered: RenderedEmail) -> None:
    """Blocking SMTP conversation, run in a worker thread. Builds a
    multipart/alternative message (text + HTML) from the rendered parts."""
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = rendered.subject
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")

    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_S
        ) as smtp:
            _authenticate_and_send(smtp, message, recipient)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_S) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            _authenticate_and_send(smtp, message, recipient)


def _authenticate_and_send(smtp: smtplib.SMTP, message: EmailMessage, recipient: str) -> None:
    if settings.smtp_username:
        smtp.login(settings.smtp_username, settings.smtp_password)
    smtp.send_message(message)


class SmtpEmailDriver:
    """Real-email driver: renders the pre-written template for the notification
    and delivers both MIME parts over SMTP. Raises on delivery failure — the
    fan-out and email helpers catch and log, so a down server degrades the
    notification instead of failing the submission that triggered it."""

    async def send(self, notification: Notification) -> None:
        rendered = render_email(
            notification.milestone,
            claim_number=notification.claim_id,
            **notification.template_vars,
        )
        await asyncio.to_thread(_smtp_send, notification.recipient_email, rendered)


def get_driver() -> NotificationProvider:
    """Resolve the configured driver.

    "console" (the default) logs structured events — the dev/CI stand-in with
    byte-identical behavior to the pre-email days; "smtp" delivers real email;
    EMAIL_DISABLED=true drops delivery entirely. EMAIL_PROVIDER is a Literal
    at the config layer, so unknown names cannot reach this function.
    """
    if settings.email_disabled:
        return DisabledDriver()
    if settings.email_provider == "smtp":
        return SmtpEmailDriver()
    return ConsoleDriver()
