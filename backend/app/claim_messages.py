"""Claim message threads (Feature Waves F5).

A claim conversation between the assigned adjuster and the customer. Messages
live in the `claim_messages` collection — {claim_id, author_role, author_id,
body, created_at, read_at} — appended chronologically and never edited or
deleted, so the thread is part of the claim's record.

Bodies are plain text only. Every stored body passes through
`normalize_body`: surrounding whitespace stripped, control characters (other
than tab and newline) removed, and a configurable maximum length enforced.
Rendering is React text nodes on every surface, so hostile HTML/script
strings are inert by construction — nothing here is ever interpolated into
HTML on the way out.

Notifications ride the F1 delivery boundary: a new message emails the other
party through `send_reply_notice_email` (the template F1 pre-wrote for
exactly this), and a customer message rings the adjuster bell by appending
records to the `notifications` collection. Delivery failures are logged
degradation, never message-send failures.
"""

import re
import secrets
from datetime import datetime, timezone

import structlog

import database
from app.notifications.emails import send_reply_notice_email
from app.notifications.fanout import resolve_recipient

logger = structlog.get_logger("claimos.messages")

# Every C0 control char except tab (\x09) and line feed (\x0a); carriage
# returns are normalized away with the same class.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\r]")

# Email preview budget — the template truncates further and escapes anyway.
_SNIPPET_MAX_CHARS = 120


def normalize_body(body: str, max_chars: int) -> str:
    """Plain-text hygiene for one message body: strip, drop control chars
    (tab/newline survive). Raises ValueError outside 1..max_chars after
    normalization — API layers map that to a 422."""
    cleaned = _CONTROL_CHARS.sub("", (body or "")).strip()
    if not cleaned:
        raise ValueError("Message must not be empty")
    if len(cleaned) > max_chars:
        raise ValueError(f"Message must be at most {max_chars} characters")
    return cleaned


def _snippet(body: str) -> str:
    return " ".join(body.split())[:_SNIPPET_MAX_CHARS]


async def list_thread(claim_id: str) -> list[dict]:
    """The thread in chronological order (oldest first), ids projected out."""
    cursor = database.claim_messages_col.find({"claim_id": claim_id}, {"_id": 0}).sort(
        "created_at", 1
    )
    return await cursor.to_list(1000)


async def mark_read(claim_id: str, reader_role: str) -> int:
    """Stamp read_at on the other party's unread messages. Idempotent:
    already-read docs do not match the filter, so re-listing is free."""
    now = datetime.now(timezone.utc).isoformat()
    result = await database.claim_messages_col.update_many(
        {"claim_id": claim_id, "author_role": {"$ne": reader_role}, "read_at": None},
        {"$set": {"read_at": now}},
    )
    return result.modified_count


async def post_message(claim_id: str, author_role: str, author_id: str, body: str) -> dict:
    """Append one message, journal the event, and return the stored doc.

    The event write uses the same append-only store the SSE stream replays,
    so the thread shows up in the claim's durable history.
    `claim_message_posted` is deliberately not a milestone key — fan-out for
    messages is explicit (notify_other_party), not event-derived.
    """
    doc = {
        "id": f"msg_{secrets.token_hex(8)}",
        "claim_id": claim_id,
        "author_role": author_role,
        "author_id": author_id,
        "body": body,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read_at": None,
    }
    await database.claim_messages_col.insert_one(doc)
    try:
        from app.events import emit_event  # local import: events pulls the fan-out chain

        await emit_event(
            claim_id,
            {
                "event": "claim_message_posted",
                "message_id": doc["id"],
                "author_role": author_role,
            },
        )
    except Exception:
        logger.exception("message_event_write_failed", claim_id=claim_id)
    return doc


async def notify_other_party(claim_id: str, author_role: str, sender_name: str, body: str) -> None:
    """Email the party who did not just write, via the F1 driver boundary.

    Adjuster author -> the customer (claim contact email, falling back to the
    policy holder). Customer author -> every adjuster user. Every send is
    best-effort: `_deliver` inside the email helper logs driver failures, and
    an empty recipient list simply emails nobody.
    """
    snippet = _snippet(body)
    if author_role == "adjuster":
        recipient = await resolve_recipient(claim_id, {})
        recipients = [recipient] if recipient else []
    else:
        docs = await database.users_col.find(
            {"role": "adjuster"}, {"email": 1, "_id": 0}
        ).to_list(100)
        recipients = [d["email"] for d in docs if d.get("email")]

    for recipient in recipients:
        await send_reply_notice_email(claim_id, recipient, sender_name, snippet)


async def notify_adjusters_bell(claim_id: str, body: str) -> None:
    """In-app bell for adjusters when a customer writes.

    One record per claim, upserted on every new customer message: the bell
    always shows the latest unread customer snippet. `recipient_email` holds
    the whole adjuster team — Mongo equality filters match array elements, so
    the notification center's `{"recipient_email": email}` filter serves the
    record to every adjuster. The (claim_id, milestone) unique index behind
    milestone fan-out enforces exactly one record per claim+key, which is what
    makes the upsert idempotent. `new_message` is a bell-only milestone — it
    is never sent through the SMTP driver, whose template map does not know it.
    """
    snippet = _snippet(body)
    now = datetime.now(timezone.utc).isoformat()
    docs = await database.users_col.find(
        {"role": "adjuster"}, {"email": 1, "_id": 0}
    ).to_list(100)
    recipient_emails = [d["email"].strip().lower() for d in docs if d.get("email")]
    if not recipient_emails:
        return
    try:
        await database.notifications_col.update_one(
            {"claim_id": claim_id, "milestone": "new_message"},
            {
                "$set": {
                    "recipient_email": recipient_emails,
                    "title": f"New message on claim {claim_id}",
                    "body": f"Customer: {snippet}",
                    "read": False,
                    "read_at": None,
                    "created_at": now,
                },
                "$setOnInsert": {"id": f"ntf_{secrets.token_hex(8)}"},
            },
            upsert=True,
        )
    except Exception:
        logger.exception("adjuster_bell_write_failed", claim_id=claim_id)
