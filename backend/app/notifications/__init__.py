"""Notifications package: provider interface, drivers, templates, fan-out."""

from app.notifications.emails import send_access_code_email, send_reply_notice_email
from app.notifications.fanout import dispatch_milestone, milestone_for_event
from app.notifications.provider import (
    ConsoleDriver,
    DisabledDriver,
    Notification,
    NotificationProvider,
    SmtpEmailDriver,
    get_driver,
)
from app.notifications.templates import RenderedEmail, render_email

__all__ = [
    "ConsoleDriver",
    "DisabledDriver",
    "Notification",
    "NotificationProvider",
    "RenderedEmail",
    "SmtpEmailDriver",
    "dispatch_milestone",
    "get_driver",
    "milestone_for_event",
    "render_email",
    "send_access_code_email",
    "send_reply_notice_email",
]
