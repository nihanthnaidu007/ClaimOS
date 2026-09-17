"""Notification drivers: the pluggable delivery boundary for milestone fan-out.

`NotificationProvider` is the interface; `ConsoleDriver` is the default — it
logs every notification as a structured event, which is the audit-friendly
stand-in for real email/SMS drivers. Delivery drivers never raise: a failed
notification must not fail the pipeline event that triggered it, so
`dispatch_milestone` treats driver exceptions as logged degradation.
"""

from dataclasses import dataclass
from typing import Protocol

import structlog

from app.config import settings

logger = structlog.get_logger("claimos.notifications")


@dataclass(frozen=True)
class Notification:
    """One customer-facing milestone notification, already persisted."""

    id: str
    claim_id: str
    recipient_email: str
    milestone: str
    title: str
    body: str
    created_at: str


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


def get_driver() -> NotificationProvider:
    """Resolve the configured driver. Unknown names fall back to console with
    a loud log rather than crashing startup — notifications are derived data."""
    driver_name = settings.notification_driver
    if driver_name == "console":
        return ConsoleDriver()
    logger.warning("unknown_notification_driver", driver=driver_name)
    return ConsoleDriver()
