"""Claim support APIs: trace timeline and evidence pack.

Document upload/list live in server.py — one canonical route pair (a second,
shadowed implementation here was unreachable dead code and has been removed).

Collections are read through the `database` module at call time (not imported
at module load) so test patching and multi-process deployments bind correctly —
the same pattern app.auth_store and the workbench routes use. This router is
kept separate from server.py so parallel work on the core routes stays
merge-clean; server.py includes it with one line. FNOL drafts live in their
own router (app.fnol_drafts).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from app.deps import UserRecord, require_adjuster
from app.evidence_pack import generate_evidence_pack
from app.events import get_claim_events

router = APIRouter()


async def _require_claim(claim_id: str) -> dict:
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


# ============ TRACE ============

class TraceResponse(BaseModel):
    """Everything the claim-detail timeline renders, from durable state only."""

    claimId: str
    claimStatus: str
    events: list[dict]
    runs: list[dict]
    agentLogs: list[dict] = []
    agentTrace: dict = {}
    decision: dict = {}


def _run_view(run: dict) -> dict:
    return {
        "attempt": run.get("attempt"),
        "status": run.get("status"),
        "createdAt": run.get("created_at", ""),
        "finishedAt": run.get("finished_at", ""),
        "failureReason": run.get("failure_reason"),
        "escalationReason": run.get("escalation_reason"),
        "stp": run.get("stp"),
    }


@router.get("/claims/{claim_id}/trace", response_model=TraceResponse)
async def get_claim_trace(claim_id: str, current_user: UserRecord = Depends(require_adjuster)):
    """Per-agent trace timeline data: durable events + claim_runs + agent logs."""
    claim = await _require_claim(claim_id)
    events = await get_claim_events(claim_id)
    runs = await database.claim_runs_col.find(
        {"claim_id": claim_id}, {"_id": 0}
    ).sort("attempt", 1).to_list(50)

    trace = claim.get("agent_trace", {}) or {}
    return {
        "claimId": claim_id,
        "claimStatus": claim.get("status", "pending"),
        "events": [
            {"seq": e["seq"], "event": e.get("event", "unknown"), "data": e.get("data") or {},
             "createdAt": e.get("created_at", "")}
            for e in events
        ],
        "runs": [_run_view(r) for r in runs],
        "agentLogs": claim.get("agent_logs", []) or [],
        "agentTrace": trace,
        "decision": trace.get("decision", {}) or {},
    }



# ============ EVIDENCE PACK ============

class EvidencePackResponse(BaseModel):
    claimId: str
    filename: str
    pdf: str


@router.get("/claims/{claim_id}/evidence-pack", response_model=EvidencePackResponse)
async def get_evidence_pack(claim_id: str, current_user: UserRecord = Depends(require_adjuster)):
    """Full adjudication record as a downloadable PDF (traces + decision + events)."""
    claim = await _require_claim(claim_id)
    events = await get_claim_events(claim_id)
    runs = await database.claim_runs_col.find(
        {"claim_id": claim_id}, {"_id": 0}
    ).sort("attempt", 1).to_list(50)
    pdf_base64 = generate_evidence_pack(claim, events, runs)
    return {"claimId": claim_id, "filename": f"evidence-pack-{claim_id}.pdf", "pdf": pdf_base64}
