"""Pure helpers for internal claim notes (F11): sanitization and @mentions.

The separation is deliberate — `app/notes.py` holds logic with no I/O so the
parsing and sanitization rules are unit-testable without MongoDB, and the
route layer (`app/notes_routes.py`) owns storage, notifications, and audit.

Customer-visibility contract: NOTHING in this module ever attaches note data
to a claim document, an agent trace, or an event-stream record. The public
status portal's deny-by-default projection (see `app/status_portal.py`,
guarded by the F6 regression test) only ever copies explicitly allowlisted
fields, so notes stay internal by construction.
"""

import re
import secrets
from collections import OrderedDict
from datetime import datetime, timezone

from app.schemas import MAX_MENTIONS_PER_NOTE

# A mention is "@" + handle where handle chars survive an email local-part.
# The lookbehind rejects the "@" of an email address (alex.rivera@example.com
# is never a mention of "example"); the char class stops at punctuation.
_MENTION_RE = re.compile(r"(?<![a-zA-Z0-9._-])@([a-zA-Z0-9._-]+)")

# C0 control characters except \t and \n — stripped from note bodies so notes
# are plain, printable text (no ANSI escapes, no NULs, no terminal injection).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ANSI/CSI escape sequences (\x1b[...m and friends) — stripped whole so note
# bodies are readable text rather than ESC-less bracket residue.
_ANSI_CSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sanitize_note_body(body: str, max_length: int) -> str:
    """Reduce a note body to capped, printable plain text with newlines kept."""
    cleaned = _ANSI_CSI.sub("", body)
    cleaned = _CONTROL_CHARS.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned.strip()[:max_length]


def mention_handles(body: str) -> list[str]:
    """Distinct @handles in reading order — candidates, not verified adjusters.

    Trailing dots are sentence punctuation, not local-part characters, so
    "@sam." mentions "sam" while "alex.rivera" keeps its internal dot.
    """
    handles = (handle.rstrip(".") for handle in _MENTION_RE.findall(body))
    return list(OrderedDict.fromkeys(handle for handle in handles if handle))


def parse_mentions(body: str, adjusters: list[dict]) -> list[dict]:
    """Resolve @handles against real adjuster accounts, case-insensitively.

    Adjuster identity has no username field, so the mention handle is the
    email local-part (e.g. "@alex.rivera" matches alex.rivera@...).
    Duplicates collapse; fan-out is capped so one note cannot bell-spam.
    """
    by_handle = {}
    for adjuster in adjusters:
        email = (adjuster.get("email") or "").strip()
        local_part = email.split("@", 1)[0].lower()
        if local_part:
            by_handle.setdefault(local_part, adjuster)

    matched: list[dict] = []
    seen_ids: set = set()
    for handle in mention_handles(body):
        if len(matched) >= MAX_MENTIONS_PER_NOTE:
            break
        adjuster = by_handle.get(handle.lower())
        if adjuster and adjuster.get("id") not in seen_ids:
            seen_ids.add(adjuster["id"])
            matched.append(adjuster)
    return matched


def mention_notification_doc(*, claim_id: str, note_id: str, recipient: dict) -> dict:
    """The bell notification for one mentioned adjuster.

    The carrier deliberately carries no note content — the notifications
    endpoint is customer-reachable, so the title/body only point at the claim.
    The milestone embeds the note id: the notifications collection enforces
    one bell per (claim_id, milestone), so replaying a note never double-bells
    and a second mention on the same claim cannot collide with the first.
    """
    return {
        "id": f"ntf_{secrets.token_hex(6)}",
        "recipient_email": recipient["email"],
        "claim_id": claim_id,
        "note_id": note_id,
        "milestone": f"internal_note_mention:{note_id}",
        "title": f"You were mentioned in a note on claim {claim_id}",
        "body": "An adjuster mentioned you in an internal note. Open the case to read it.",
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
