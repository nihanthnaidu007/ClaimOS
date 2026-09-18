"""Customer document uploads through the public portal (spec F4).

The access code IS the customer's credential here — the same claim-number +
code pair the status lookup requires, checked with the same hashed lookup and
the same generic 404. Scope rule: every document-request query matches on its
id AND the authenticated claim's id, so a valid code for claim A cannot list,
upload to, or even confirm the existence of claim B's checklist — a request
id from another claim is indistinguishable from a missing one.

On success the blob is stored behind the shared validation contract
(app.uploads — 10 MiB cap, content-type allowlist, unchanged), the request
flips to received with the document linked, the action is audited, a durable
event lands on the claim's stream, and the requesting adjuster gets an
in-app notification (bell only — no email; the bell is the F4 surface).

Collections are read through the `database` module at call time, mirroring
the other app.* route modules, so test patching binds correctly.
"""

import hashlib
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import database
from app.config import settings
from app.events import emit_event
from app.rate_limit import limiter
from app.status_routes import claim_for_access
from app.storage import get_provider, new_storage_key
from app.uploads import read_validated_upload

router = APIRouter(prefix="/status", tags=["status-portal"])

logger = structlog.get_logger("claimos.portal_uploads")

# The one generic 404 the portal returns for a wrong code, an unknown claim,
# and a request id that belongs to a different claim — nothing is enumerable.
_GENERIC_NOT_FOUND = HTTPException(
    status_code=404, detail="No claim found for that claim number and access code"
)

# Bell notification key for the requesting adjuster. In-app only: the row is
# written directly (not via the milestone fan-out, which emails customers and
# dedupes per claim+milestone — wrong shape for per-request events).
_ADJUSTER_MILESTONE = "document_request_received"


class PortalUploadResponse(BaseModel):
    """What the customer's upload confirms: the request is now received."""

    requestId: str
    status: str
    documentId: str
    filename: str
    sizeBytes: int


@router.post("/upload-document", response_model=PortalUploadResponse, status_code=201)
@limiter.limit(settings.portal_upload_rate_limit)
async def upload_portal_document(
    request: Request,
    claimNumber: str = Form(...),
    accessCode: str = Form(...),
    requestId: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload one requested document from the portal (spec F4).

    Credential-bearing multipart POST (claim number + access code in the
    body, like every /status route). The request must belong to the
    authenticated claim and still be `requested`; receiving is the customer's
    only transition and can never be undone from the portal.
    """
    claim = await claim_for_access(claimNumber, accessCode)
    claim_id = claim["id"]

    doc_request = await database.document_requests_col.find_one(
        {"id": requestId, "claim_id": claim_id}, {"_id": 0}
    )
    if not doc_request:
        # A request on another claim, a request on no claim, and a wrong code
        # all land here: identical body, nothing distinguishable.
        raise _GENERIC_NOT_FOUND

    if doc_request["status"] != "requested":
        state = (
            "already received — you don't need to upload it again"
            if doc_request["status"] == "received"
            else "waived — no upload is needed"
        )
        raise HTTPException(
            status_code=409, detail=f"This document request is {state}"
        )

    content, content_type, file_name = await read_validated_upload(file)

    now = datetime.now(timezone.utc).isoformat()
    storage_key = new_storage_key(claim_id, file_name)
    stored = await get_provider().save(
        key=storage_key, content=content, content_type=content_type
    )
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    uploaded_by = (claim.get("contact_email") or "").strip().lower() or "portal-customer"
    await database.claim_documents_col.insert_one(
        {
            "id": doc_id,
            "claim_id": claim_id,
            "document_type": "customer_upload",
            "document_request_id": requestId,
            "file_name": file_name,
            "content_type": content_type,
            "size_bytes": stored.size_bytes,
            "storage_key": stored.storage_key,
            "sha256": hashlib.sha256(content).hexdigest(),
            "uploaded_at": now,
            "uploaded_by": uploaded_by,
        }
    )

    # Conditional flip: `requested` is part of the filter, so a concurrent
    # waive or a second upload can never double-receive — one of them wins.
    result = await database.document_requests_col.update_one(
        {"id": requestId, "claim_id": claim_id, "status": "requested"},
        {
            "$set": {
                "status": "received",
                "document_id": doc_id,
                "updated_at": now,
            }
        },
    )
    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="This document request was just updated — refresh and try again",
        )

    await database.audit_log_col.insert_one(
        {
            "id": f"aud_{uuid.uuid4().hex[:12]}",
            "claim_id": claim_id,
            "actor": "customer",
            "actor_email": uploaded_by,
            "action": "document_request_received",
            "before": {"status": "requested", "document_id": None},
            "after": {"status": "received", "document_id": doc_id},
            "reason": f"Customer uploaded {file_name}",
            "at": now,
        }
    )
    await emit_event(
        claim_id,
        {
            "event": "document_request_received",
            "requestId": requestId,
            "title": doc_request["title"],
            "documentId": doc_id,
            "uploadedBy": uploaded_by,
        },
    )
    await _notify_requesting_adjuster(
        claim_id, doc_request["title"], doc_request.get("requested_by"), now
    )

    logger.info(
        "portal_document_uploaded", claim_id=claim_id, request_id=requestId
    )
    return PortalUploadResponse(
        requestId=requestId,
        status="received",
        documentId=doc_id,
        filename=file_name,
        sizeBytes=stored.size_bytes,
    )


async def _notify_requesting_adjuster(
    claim_id: str, request_title: str, requested_by: str | None, now: str
) -> None:
    """Bell notification for the adjuster who asked for the document.

    The request's `requested_by` is the assignee proxy until claim assignment
    (F10) lands — the adjuster who requested the document is the one waiting
    on it. A missing requester (seeded rows, deleted user) notifies nobody.
    """
    requester = None
    if requested_by:
        requester = await database.users_col.find_one(
            {"id": requested_by}, {"_id": 0, "email": 1}
        )
    if not requester or not requester.get("email"):
        logger.info(
            "adjuster_notification_skipped", claim_id=claim_id, reason="no_requester"
        )
        return

    email = requester["email"].strip().lower()
    await database.notifications_col.insert_one(
        {
            "id": f"ntf_{uuid.uuid4().hex[:12]}",
            "claim_id": claim_id,
            "recipient_email": email,
            "milestone": _ADJUSTER_MILESTONE,
            "title": "Document received",
            "body": f'"{request_title}" was uploaded by the customer on claim {claim_id}.',
            "read": False,
            "read_at": None,
            "created_at": now,
        }
    )
