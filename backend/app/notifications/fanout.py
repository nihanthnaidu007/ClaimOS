"""Milestone fan-out: pipeline events become customer notifications.

Mapping (spec milestone list):
  claim_submitted            -> submitted
  DOCUMENT_AGENT complete    -> documents_received
  DECISION_AGENT complete    -> decision_ready
  payout_recorded            -> payout_recorded

Every notification is recorded in the `notifications` collection first
(unique per claim+milestone, so replayed events cannot double-notify) and
then handed to the configured driver. Fan-out failures are logged, never
raised: notifications are derived data and must not fail the event write
that triggered them.
"""

import secrets
from datetime import datetime, timezone

import structlog

import database
from app.notifications.provider import Notification, get_driver

logger = structlog.get_logger("claimos.notifications")

MILESTONE_TITLES = {
    "submitted": "Claim submitted",
    "documents_received": "Documents received",
    "decision_ready": "Decision ready",
    "payout_recorded": "Payout recorded",
}

_BODIES = {
    "submitted": "Your claim {claim} has been received and is queued for processing.",
    "documents_received": "Documents for claim {claim} have been received and analyzed.",
    "decision_ready": "A decision is ready for claim {claim}. Open the status page to view your decision letter.",
    "payout_recorded": "A payout has been recorded for claim {claim}.",
}


def milestone_for_event(event_type: str, data: dict) -> str | None:
    """The notification milestone an event maps to, or None when it is not a
    customer-facing milestone (agent_start, SSE bookkeeping, etc.)."""
    if event_type == "claim_submitted":
        return "submitted"
    if event_type == "agent_complete":
        agent = (data or {}).get("agent", "")
        if agent == "DOCUMENT_AGENT":
            return "documents_received"
        if agent == "DECISION_AGENT":
            return "decision_ready"
    if event_type == "payout_recorded":
        return "payout_recorded"
    return None


async def record_and_send(claim_id: str, milestone: str, recipient_email: str) -> dict | None:
    """Persist the notification (idempotent per claim+milestone) and hand it to
    the driver. Returns the stored doc, or None when it already existed.

    `recipient_email` is already resolved (claim contact, else policy holder);
    empty means there is nobody to notify — the record is still kept so the
    in-app center can show the milestone once a recipient exists.
    """
    existing = await database.notifications_col.find_one(
        {"claim_id": claim_id, "milestone": milestone}
    )
    if existing:
        return None

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": f"ntf_{secrets.token_hex(8)}",
        "claim_id": claim_id,
        "recipient_email": (recipient_email or "").strip().lower(),
        "milestone": milestone,
        "title": MILESTONE_TITLES[milestone],
        "body": _BODIES[milestone].format(claim=claim_id),
        "read": False,
        "read_at": None,
        "created_at": now,
    }
    await database.notifications_col.insert_one(doc)
    notification = Notification(
        id=doc["id"],
        claim_id=claim_id,
        recipient_email=doc["recipient_email"],
        milestone=milestone,
        title=doc["title"],
        body=doc["body"],
        created_at=now,
    )
    try:
        await get_driver().send(notification)
    except Exception:
        logger.exception(
            "notification_delivery_failed", claim_id=claim_id, milestone=milestone
        )
    return doc


async def resolve_recipient(claim_id: str, data: dict) -> str:
    """Who to notify: the claim's contact email, falling back to the policy
    holder email. Missing claim rows (e.g. events for historical claims)
    simply notify nobody."""
    claim = await database.claims_col.find_one(
        {"id": claim_id}, {"contact_email": 1, "policy_number": 1}
    )
    if not claim:
        return ""
    if claim.get("contact_email"):
        return claim["contact_email"]
    policy = await database.policies_col.find_one(
        {"policy_number": claim.get("policy_number", "")}, {"holder_email": 1}
    )
    return (policy or {}).get("holder_email", "")


async def dispatch_milestone(claim_id: str, event_type: str, data: dict) -> None:
    """Entry point called from the event store after each insert."""
    milestone = milestone_for_event(event_type, data)
    if milestone is None:
        return
    recipient = await resolve_recipient(claim_id, data)
    await record_and_send(claim_id, milestone, recipient)
