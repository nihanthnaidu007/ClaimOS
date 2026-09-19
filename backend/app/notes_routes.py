"""Adjuster-only internal note routes (F11).

Internal-only by construction: notes live in the dedicated `claim_notes`
collection and nothing here writes note data onto claim documents, agent
traces, or the event stream — the surfaces the customer-facing status portal
reads. Every create is audited; every mention notifies exactly the named
adjuster through the bell-notification collection, whose carriers never
contain note content.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

import database
from app.deps import UserRecord, require_adjuster
from app.notes import mention_notification_doc, parse_mentions, sanitize_note_body
from app.schemas import (
    NOTE_MAX_LENGTH,
    NoteCreate,
    NoteCreateResponse,
    NoteListResponse,
    NoteOut,
)

router = APIRouter(tags=["notes"])


async def _append_audit_entry(entry: dict) -> dict:
    """Same audit shape as the workbench override path — one insert, no updates."""
    doc = {"id": f"aud_{secrets.token_hex(6)}", **entry}
    await database.audit_log_col.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


async def _claim_exists(claim_id: str) -> bool:
    found = await database.claims_col.count_documents({"id": claim_id}, limit=1)
    return found > 0


async def _adjusters() -> list[dict]:
    return await database.users_col.find(
        {"role": "adjuster"}, {"_id": 0, "id": 1, "email": 1}
    ).to_list(1000)


def _new_note_id() -> str:
    return f"note_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


def _note_to_public(doc: dict) -> NoteOut:
    """Project a claim_notes document to the adjuster-facing shape."""
    return NoteOut(
        id=doc["id"],
        claimId=doc["claim_id"],
        authorId=doc["author_id"],
        authorEmail=doc["author_email"],
        body=doc["body"],
        mentions=doc.get("mentions", []),
        createdAt=doc["created_at"],
        auditEntryId=doc["audit_entry_id"],
    )


@router.get("/workbench/claims/{claim_id}/notes")
async def list_claim_notes(claim_id: str, adjuster: UserRecord = Depends(require_adjuster)):
    if not await _claim_exists(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    docs = (
        await database.claim_notes_col.find({"claim_id": claim_id}, {"_id": 0})
        .sort("created_at", 1)
        .to_list(1000)
    )
    return NoteListResponse(claimId=claim_id, notes=[_note_to_public(doc) for doc in docs])


@router.post("/workbench/claims/{claim_id}/notes", status_code=201)
async def create_claim_note(
    claim_id: str, payload: NoteCreate, adjuster: UserRecord = Depends(require_adjuster)
):
    if not await _claim_exists(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")

    body = sanitize_note_body(payload.body, NOTE_MAX_LENGTH)
    if not body:
        raise HTTPException(status_code=422, detail="Note body cannot be blank")

    note_id = _new_note_id()
    created_at = datetime.now(timezone.utc).isoformat()
    mentioned = parse_mentions(body, await _adjusters())
    audit_entry = await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "note",
            # AuditEntry.before is strictly a dict — an empty dict is the
            # schema-honest "no prior state" for a created note.
            "before": {},
            "after": {"noteId": note_id},
            "reason": body,
            "at": created_at,
        }
    )

    handles = [m["email"].split("@", 1)[0] for m in mentioned]
    await database.claim_notes_col.insert_one(
        {
            "id": note_id,
            "claim_id": claim_id,
            "author_id": adjuster.id,
            "author_email": adjuster.email,
            "body": body,
            "mentions": handles,
            "created_at": created_at,
            "audit_entry_id": audit_entry["id"],
        }
    )
    for recipient in mentioned:
        await database.notifications_col.insert_one(
            mention_notification_doc(claim_id=claim_id, note_id=note_id, recipient=recipient)
        )

    return NoteCreateResponse(
        id=note_id,
        claimId=claim_id,
        authorId=adjuster.id,
        authorEmail=adjuster.email,
        body=body,
        mentions=handles,
        createdAt=created_at,
        auditEntryId=audit_entry["id"],
    )
