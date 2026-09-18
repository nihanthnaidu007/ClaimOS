"""Public claim-status endpoints — the customer portal's API surface.

All routes are credential-bearing POSTs (claim number + access code in the
body): keeping the code out of URLs and access logs matters more than cache
friendliness on an endpoint that is never shared or bookmarked. Lookup is
rate-limited per client IP (slowapi) and non-enumerable — wrong number, wrong
code, and historical claims without a code all return the same generic 404.
Access-code recovery matches on claim number + contact email and answers with
a fixed neutral response regardless of whether anything matched, so it cannot
be used to discover which claim numbers or emails exist.
"""

from typing import Literal

import re as _re

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.notifications import emails
from app.rate_limit import limiter
from app.schemas import (
    StatusLetterRequest,
    StatusLetterResponse,
    StatusLookupRequest,
    StatusLookupResponse,
)
from app.status_portal import (
    access_code_hash,
    generate_access_code,
    public_status_payload,
    status_not_found,
)
import database
from pdf_generator import generate_claim_pdf

router = APIRouter(prefix="/status", tags=["status-portal"])

logger = structlog.get_logger("claimos.status_portal")

_GENERIC_NOT_FOUND = HTTPException(status_code=404, detail=status_not_found()["detail"])

# Fixed response body for recovery: identical whether or not a claim matched,
# so responses never leak which claim numbers or email addresses exist.
_RECOVERY_NEUTRAL_MESSAGE = (
    "If this claim number and email match a claim on file, a new access code "
    "has been sent to that address."
)


class AccessCodeRecoveryRequest(BaseModel):
    """Router-local request model for access-code recovery."""

    claimNumber: str = Field(min_length=4, max_length=40)
    contactEmail: str = Field(min_length=3, max_length=254)

    @field_validator("claimNumber", "contactEmail")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class AccessCodeRecoveryResponse(BaseModel):
    """Constant neutral response — no field varies with the match outcome."""

    status: Literal["ok"] = "ok"
    message: str = _RECOVERY_NEUTRAL_MESSAGE


async def _claim_for_access(claim_number: str, access_code: str) -> dict:
    """The one claim matching claim number AND access-code hash, else generic 404."""
    claim = await database.claims_col.find_one(
        {"id": claim_number, "access_code_hash": access_code_hash(access_code)},
        {"_id": 0},
    )
    if not claim:
        # Identical response shape for unknown number, wrong code, and revoked
        # legacy claims: nothing about the failure mode is distinguishable.
        raise _GENERIC_NOT_FOUND
    return claim


@router.post(
    "/lookup",
    response_model=StatusLookupResponse,
    # response_model_exclude_unset passes the projection's key omission through
    # to the wire: expectedResolution is absent (not null) when the SLA state
    # cannot back an honest ETA. Fields the projection always sets still render.
    response_model_exclude_unset=True,
)
@limiter.limit(settings.status_lookup_rate_limit)
async def lookup_claim_status(request: Request, payload: StatusLookupRequest):
    claim = await _claim_for_access(payload.claimNumber, payload.accessCode)
    events = (
        await database.events_col.find({"claim_id": claim["id"]}, {"_id": 0})
        .sort("seq", 1)
        .to_list(1000)
    )
    logger.info("status_lookup", claim_id=claim["id"])
    return public_status_payload(claim, events)


@router.post("/decision-letter", response_model=StatusLetterResponse)
@limiter.limit(settings.status_lookup_rate_limit)
async def download_decision_letter(request: Request, payload: StatusLetterRequest):
    claim = await _claim_for_access(payload.claimNumber, payload.accessCode)
    decision = (claim.get("agent_trace") or {}).get("decision") or {}
    if not decision:
        # No decision yet — same generic 404 (there is nothing to reveal).
        raise _GENERIC_NOT_FOUND

    state = {
        "claimId": claim["id"],
        "input": {"policyNumber": claim.get("policy_number", "")},
        "intake": claim.get("agent_trace", {}).get("intake", {}),
        "policy": claim.get("agent_trace", {}).get("policy", {}),
        "documents": claim.get("agent_trace", {}).get("documents", {}),
        "eligibility": claim.get("agent_trace", {}).get("eligibility", {}),
        "decision": decision,
    }
    policy_doc = await database.policies_col.find_one(
        {"policy_number": claim.get("policy_number", "")}, {"_id": 0}
    )
    if policy_doc:
        state["policy"]["policyData"] = policy_doc

    pdf_base64 = generate_claim_pdf(state)
    return {"pdf": pdf_base64, "claimId": claim["id"]}


@router.post("/recover-access-code", response_model=AccessCodeRecoveryResponse)
@limiter.limit(settings.access_code_recovery_rate_limit)
async def recover_access_code(request: Request, payload: AccessCodeRecoveryRequest):
    """Rotate and re-deliver the portal access code (AC-1.3).

    Exact match on claim number + contact email (case-insensitive on the
    email — customers type addresses in any case). Any mismatch — unknown
    number, unknown email, claim filed without an email — returns the same
    fixed response as a match, and no email is sent on mismatch.
    """
    claim = await database.claims_col.find_one(
        {
            "id": payload.claimNumber,
            # Anchored, escaped, case-insensitive: exact address, any casing.
            "contact_email": {"$regex": f"^{_re.escape(payload.contactEmail)}$", "$options": "i"},
        },
        {"_id": 0, "id": 1, "holder_name": 1, "contact_email": 1},
    )
    if not claim or not claim.get("contact_email"):
        # Same response as success; the log carries no identifiers either.
        logger.info("access_code_recovery_attempt", outcome="no_match")
        return AccessCodeRecoveryResponse()

    new_code = generate_access_code()
    await database.claims_col.update_one(
        {"id": claim["id"]},
        # Replacing the hash invalidates the old code immediately — recovery
        # is also the "I think someone else has my code" path.
        {"$set": {"access_code_hash": access_code_hash(new_code)}},
    )
    await emails.send_access_code_email(
        claim["id"],
        recipient_email=claim["contact_email"],
        access_code=new_code,
        holder_name=claim.get("holder_name", ""),
    )
    logger.info("access_code_recovery_sent", claim_id=claim["id"])
    return AccessCodeRecoveryResponse()
