"""Notifications package: provider interface, console driver, milestone fan-out."""

from app.notifications.fanout import dispatch_milestone, milestone_for_event
from app.notifications.provider import (
    ConsoleDriver,
    Notification,
    NotificationProvider,
    get_driver,
)

__all__ = [
    "ConsoleDriver",
    "Notification",
    "NotificationProvider",
    "dispatch_milestone",
    "get_driver",
    "milestone_for_event",
]
