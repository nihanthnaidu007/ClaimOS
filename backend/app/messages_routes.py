"""Adjuster <-> customer claim message endpoints (Feature Waves F5).

Two credential models, one scoping rule — a caller only ever reaches the
thread of the claim they can authenticate to:

- Adjusters: JWT bearer + adjuster role, claim addressed by id, generic 404
  for unknown claims.
- Customers: the portal pair (claim number + access code) in the request
  body — the same hashed-lookup model as the status portal, so codes stay
  out of URLs and a wrong code is indistinguishable from an unknown claim
  (identical generic 404). Portal sends are rate-limited by their own
  setting; unsafe methods also pass the app-wide origin (CSRF) check.

A new message notifies the other party through the F1 driver boundary and,
when the customer writes, rings the adjuster bell. Notification failures are
logged degradation — the message is stored either way.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

import database
from app.claim_messages import (
    list_thread,
    mark_read,
    normalize_body,
    notify_adjusters_bell,
    notify_other_party,
    post_message,
)
from app.config import settings
from app.deps import require_adjuster
from app.rate_limit import limiter
from app.schemas import (
    MessageListResponse,
    MessageRequest,
    MessageSendResponse,
    PortalCredentialsRequest,
    PortalMessageRequest,
)
from app.status_portal import access_code_hash, first_name, status_not_found

router = APIRouter(tags=["messages"])

logger = structlog.get_logger("claimos.messages")

_GENERIC_NOT_FOUND = HTTPException(status_code=404, detail=status_not_found()["detail"])


def _message_item(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "claimId": doc["claim_id"],
        "authorRole": doc["author_role"],
        "authorId": doc.get("author_id", ""),
        "body": doc["body"],
        "createdAt": doc["created_at"],
        "readAt": doc.get("read_at"),
    }


async def _claim_or_404(claim_id: str) -> dict:
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise _GENERIC_NOT_FOUND
    return claim


async def _portal_claim_or_404(claim_number: str, access_code: str) -> dict:
    claim = await database.claims_col.find_one(
        {"id": claim_number, "access_code_hash": access_code_hash(access_code)},
        {"_id": 0},
    )
    if not claim:
        raise _GENERIC_NOT_FOUND
    return claim


# Adjuster surface (JWT, adjuster role).


@router.get("/workbench/claims/{claim_id}/messages", response_model=MessageListResponse)
async def list_claim_messages(claim_id: str, current_user=Depends(require_adjuster)):
    await _claim_or_404(claim_id)
    await mark_read(claim_id, "adjuster")
    docs = await list_thread(claim_id)
    return {"claimId": claim_id, "messages": [_message_item(d) for d in docs]}


@router.post(
    "/workbench/claims/{claim_id}/messages",
    response_model=MessageSendResponse,
    status_code=201,
)
async def send_claim_message(
    claim_id: str, payload: MessageRequest, current_user=Depends(require_adjuster)
):
    await _claim_or_404(claim_id)
    try:
        body = normalize_body(payload.body, settings.claim_message_max_chars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    doc = await post_message(claim_id, "adjuster", current_user.email, body)
    # The customer reads the thread in the portal; sender name is not an
    # adjuster PII leak vector, but the template's team fallback is friendlier.
    await notify_other_party(claim_id, "adjuster", "", body)
    logger.info("claim_message_sent", claim_id=claim_id, author_role="adjuster")
    return {"message": _message_item(doc)}


# Customer surface (claim number + access code, rate-limited).


@router.post(
    "/portal/claims/{claim_number}/messages",
    response_model=MessageSendResponse,
    status_code=201,
)
@limiter.limit(settings.portal_message_rate_limit)
async def portal_send_message(
    request: Request, claim_number: str, payload: PortalMessageRequest
):
    claim = await _portal_claim_or_404(claim_number, payload.accessCode)
    try:
        body = normalize_body(payload.body, settings.claim_message_max_chars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    doc = await post_message(claim_number, "customer", "portal-customer", body)
    await notify_other_party(
        claim_number, "customer", first_name(claim.get("holder_name", "")), body
    )
    await notify_adjusters_bell(claim_number, body)
    logger.info("claim_message_sent", claim_id=claim_number, author_role="customer")
    return {"message": _message_item(doc)}


@router.post(
    "/portal/claims/{claim_number}/messages/list", response_model=MessageListResponse
)
@limiter.limit(settings.portal_message_rate_limit)
async def portal_list_messages(
    request: Request, claim_number: str, payload: PortalCredentialsRequest
):
    await _portal_claim_or_404(claim_number, payload.accessCode)
    await mark_read(claim_number, "customer")
    docs = await list_thread(claim_number)
    return {"claimId": claim_number, "messages": [_message_item(d) for d in docs]}
