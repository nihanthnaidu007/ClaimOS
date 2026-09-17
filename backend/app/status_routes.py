"""Public claim-status endpoints — the customer portal's API surface.

Both routes are credential-bearing POSTs (claim number + access code in the
body): keeping the code out of URLs and access logs matters more than cache
friendliness on an endpoint that is never shared or bookmarked. Lookup is
rate-limited per client IP (slowapi) and non-enumerable — wrong number, wrong
code, and historical claims without a code all return the same generic 404.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.rate_limit import limiter
from app.schemas import (
    StatusLetterRequest,
    StatusLetterResponse,
    StatusLookupRequest,
    StatusLookupResponse,
)
from app.status_portal import (
    access_code_hash,
    public_status_payload,
    status_not_found,
)
import database
from pdf_generator import generate_claim_pdf

router = APIRouter(prefix="/status", tags=["status-portal"])

logger = structlog.get_logger("claimos.status_portal")

_GENERIC_NOT_FOUND = HTTPException(status_code=404, detail=status_not_found()["detail"])


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


@router.post("/lookup", response_model=StatusLookupResponse)
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
