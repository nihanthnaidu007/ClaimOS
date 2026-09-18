"""Direct (non-milestone) customer emails: access codes and reply notices.

These ride the same driver boundary as milestone fan-out — `get_driver()` —
with the same degradation contract: a failed send is logged, never raised.
Nothing here is persisted to the notifications collection: these are
transactional one-offs, not milestone records for the in-app center. An empty
recipient skips delivery silently (AC-1.2) — a claim with no contact email on
file has nobody to notify, and that is not an error.
"""

import secrets
from datetime import datetime, timezone

import structlog

from app.notifications.provider import Notification, get_driver
from app.notifications.templates import RenderedEmail, render_email

logger = structlog.get_logger("claimos.notifications")


async def _deliver(
    template_key: str,
    claim_number: str,
    recipient_email: str,
    rendered: RenderedEmail,
    template_vars: dict[str, str],
) -> None:
    recipient = (recipient_email or "").strip().lower()
    if not recipient:
        # No address on file: skip silently, log the skip for the audit trail.
        logger.info("email_skipped_no_recipient", claim_id=claim_number, milestone=template_key)
        return
    notification = Notification(
        id=f"eml_{secrets.token_hex(8)}",
        claim_id=claim_number,
        recipient_email=recipient,
        milestone=template_key,
        title=rendered.subject,
        body=rendered.text,
        created_at=datetime.now(timezone.utc).isoformat(),
        template_vars=template_vars,
    )
    try:
        await get_driver().send(notification)
    except Exception:
        logger.exception(
            "notification_delivery_failed", claim_id=claim_number, milestone=template_key
        )


async def send_access_code_email(
    claim_number: str, recipient_email: str, access_code: str, holder_name: str = ""
) -> None:
    """Deliver the portal access code by email — at FNOL time (AC-1.2) and on
    recovery (AC-1.3). The recipient must be the address on file; callers
    enforce that, this helper only delivers."""
    rendered = render_email(
        "access_code", claim_number=claim_number, holder_name=holder_name, access_code=access_code
    )
    await _deliver(
        "access_code",
        claim_number,
        recipient_email,
        rendered,
        {"holder_name": (holder_name or "").strip(), "access_code": access_code},
    )


async def send_reply_notice_email(
    claim_number: str, recipient_email: str, sender_name: str, snippet: str
) -> None:
    """New-message notice for the adjuster-customer thread. F5 wires this into
    the messaging endpoints; the F1 surface is the template + this helper."""
    rendered = render_email(
        "reply_notice", claim_number=claim_number, sender_name=sender_name, snippet=snippet
    )
    await _deliver(
        "reply_notice",
        claim_number,
        recipient_email,
        rendered,
        {"sender_name": (sender_name or "").strip(), "snippet": snippet},
    )
