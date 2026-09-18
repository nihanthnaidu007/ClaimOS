"""Document checklists — what the adjuster asks a claimant to provide (spec F3).

The document_requests collection is claim-scoped with an independent
lifecycle: an adjuster requests a document, then the request is received
(F4 customer upload) or waived. Requests never touch the LLM.

Every mutation appends an append-only audit_log row and emits a durable
event on the claim's stream, mirroring the workbench override and
settlement write paths. All routes are adjuster-gated — customers have no
relationship with this collection except through the F4 portal upload,
which is served by its own access-code-authenticated router later.

Collections are read through the `database` module at call time (not
imported at module load) so test patching and multi-process deployments
bind correctly — the same pattern app.workbench_routes uses.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pymongo import ReturnDocument
from pydantic import BaseModel, Field, field_validator

import database
from app.deps import UserRecord, require_adjuster
from app.events import emit_event

router = APIRouter(dependencies=[Depends(require_adjuster)])

# Length limits mirror the create form client-side (DocumentRequests.jsx):
# a checklist item is a short ask, not a letter — the description tells the
# claimant what a good upload looks like.
DOCUMENT_REQUEST_TITLE_MAX = 120
DOCUMENT_REQUEST_DESCRIPTION_MAX = 2000
WAIVE_REASON_MAX = 500

RequestStatus = Literal["requested", "received", "waived"]


class DocumentRequestCreate(BaseModel):
    title: str = Field(min_length=1, max_length=DOCUMENT_REQUEST_TITLE_MAX)
    description: str = Field(default="", max_length=DOCUMENT_REQUEST_DESCRIPTION_MAX)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A title is required to request a document")
        return value


class DocumentRequestPatch(BaseModel):
    """Edit the ask and/or waive the request — the adjuster's only transitions.

    'received' is set exclusively by the F4 customer upload; this route can
    never forge it. A waived request is closed: the lifecycle has no reopen.
    """

    title: Optional[str] = Field(
        default=None, min_length=1, max_length=DOCUMENT_REQUEST_TITLE_MAX
    )
    description: Optional[str] = Field(
        default=None, max_length=DOCUMENT_REQUEST_DESCRIPTION_MAX
    )
    waive: bool = False
    reason: str = Field(default="", max_length=WAIVE_REASON_MAX)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Title cannot be blank")
        return value


class DocumentRequestOut(BaseModel):
    id: str
    claim_id: str
    title: str
    description: str
    status: RequestStatus
    requested_by: str
    document_id: Optional[str] = None
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_request_or_404(claim_id: str, request_id: str) -> dict:
    doc = await database.document_requests_col.find_one(
        {"id": request_id, "claim_id": claim_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document request not found")
    return doc


async def _claim_or_404(claim_id: str) -> dict:
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


async def _append_audit_entry(entry: dict) -> dict:
    doc = {"id": f"aud_{uuid.uuid4().hex[:12]}", **entry}
    await database.audit_log_col.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.post("/claims/{claim_id}/document-requests", response_model=DocumentRequestOut, status_code=201)
async def create_document_request(
    claim_id: str, body: DocumentRequestCreate, adjuster: UserRecord = Depends(require_adjuster)
):
    """Ask the claimant for one document (spec F3). Audited + event-emitted."""
    await _claim_or_404(claim_id)
    now = _now_iso()
    doc = {
        "id": f"dreq_{uuid.uuid4().hex[:12]}",
        "claim_id": claim_id,
        "title": body.title.strip(),
        "description": body.description.strip(),
        "status": "requested",
        "requested_by": adjuster.id,
        "document_id": None,
        "created_at": now,
        "updated_at": now,
    }
    await database.document_requests_col.insert_one(doc.copy())

    await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "document_request_created",
            "before": {},
            "after": {"title": doc["title"], "status": doc["status"]},
            # The ask itself is the why-bearer for a create.
            "reason": f"Requested: {doc['title']}",
            "at": now,
        }
    )
    await emit_event(
        claim_id,
        {
            "event": "document_requested",
            "requestId": doc["id"],
            "title": doc["title"],
            "requestedBy": adjuster.email,
            "at": now,
        },
    )
    return doc


@router.get("/claims/{claim_id}/document-requests", response_model=list[DocumentRequestOut])
async def list_document_requests(claim_id: str):
    """The case view checklist, oldest first (a checklist reads top-down)."""
    await _claim_or_404(claim_id)
    requests = (
        await database.document_requests_col.find({"claim_id": claim_id}, {"_id": 0})
        .sort("created_at", 1)
        .to_list(200)
    )
    return requests


@router.patch(
    "/claims/{claim_id}/document-requests/{request_id}", response_model=DocumentRequestOut
)
async def update_document_request(
    claim_id: str,
    request_id: str,
    body: DocumentRequestPatch,
    adjuster: UserRecord = Depends(require_adjuster),
):
    """Edit title/description and/or waive the request. One PATCH, one audit row."""
    await _claim_or_404(claim_id)
    current = await _get_request_or_404(claim_id, request_id)
    now = _now_iso()
    before = {"title": current["title"], "description": current["description"], "status": current["status"]}

    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip()
    if body.description is not None:
        updates["description"] = body.description.strip()

    waiving = body.waive
    if waiving and current["status"] != "requested":
        state = "already received" if current["status"] == "received" else "already waived"
        raise HTTPException(
            status_code=409,
            detail=f"This document request is {state} — it can no longer be waived",
        )
    if waiving:
        updates["status"] = "waived"

    if not updates:
        raise HTTPException(status_code=422, detail="Nothing to update")

    if current["status"] == "waived" and "status" not in updates:
        raise HTTPException(
            status_code=409, detail="This document request is waived and can no longer be edited"
        )

    updated = await database.document_requests_col.find_one_and_update(
        {"id": request_id, "claim_id": claim_id},
        {"$set": {**updates, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    after = {
        "title": updated["title"],
        "description": updated["description"],
        "status": updated["status"],
    }

    if waiving:
        await _append_audit_entry(
            {
                "claim_id": claim_id,
                "actor": adjuster.id,
                "actor_email": adjuster.email,
                "action": "document_request_waived",
                "before": before,
                "after": after,
                "reason": body.reason.strip() or "Waived by adjuster",
                "at": now,
            }
        )
        await emit_event(
            claim_id,
            {
                "event": "document_request_waived",
                "requestId": request_id,
                "title": updated["title"],
                "reason": body.reason.strip(),
                "waivedBy": adjuster.email,
                "at": now,
            },
        )
    else:
        changed = [field for field in ("title", "description") if field in updates]
        await _append_audit_entry(
            {
                "claim_id": claim_id,
                "actor": adjuster.id,
                "actor_email": adjuster.email,
                "action": "document_request_updated",
                "before": before,
                "after": after,
                "reason": f"Updated {', '.join(sorted(changed))}",
                "at": now,
            }
        )
        await emit_event(
            claim_id,
            {
                "event": "document_request_updated",
                "requestId": request_id,
                "title": updated["title"],
                "updatedBy": adjuster.email,
                "at": now,
            },
        )

    return updated
