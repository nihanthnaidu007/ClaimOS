"""Claim support APIs: trace timeline, evidence pack, and audited reopen.

Document upload/list live in server.py — one canonical route pair (a second,
shadowed implementation here was unreachable dead code and has been removed).

Collections are read through the `database` module at call time (not imported
at module load) so test patching and multi-process deployments bind correctly —
the same pattern app.auth_store and the workbench routes use. This router is
kept separate from server.py so parallel work on the core routes stays
merge-clean; server.py includes it with one line. FNOL drafts live in their
own router (app.fnol_drafts).
"""

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from app.deps import UserRecord, require_adjuster
from app.evidence_pack import generate_evidence_pack
from app.events import emit_event, get_claim_events
from app.schemas import AuditEntry, ReopenRequest, ReopenResponse
from app.workbench import DECIDED_STATUSES

logger = structlog.get_logger("claimos.claims")

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============ REOPEN ============


@router.post("/claims/{claim_id}/reopen", response_model=ReopenResponse)
async def reopen_claim(
    claim_id: str,
    body: ReopenRequest,
    current_user: UserRecord = Depends(require_adjuster),
):
    """Reopen a decided claim for another round of human review (spec F14).

    Valid only from a decided state — anything else is a 409 that names the
    current status. Reopen is a REVIEW state, not an execution: the pipeline
    is never re-enqueued here; re-adjudication is the adjuster's explicit
    next action (override from the workbench). The reason is audited.
    """
    reason = body.reason.strip()
    claim = await _require_claim(claim_id)

    status = claim.get("status")
    if status not in DECIDED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Claim status '{status}' cannot be reopened — only decided claims "
                "(approved, rejected, overridden, settled, or failed) can reopen"
            ),
        )

    at = _utc_now()
    audit = AuditEntry(
        id=f"aud_{uuid.uuid4().hex[:12]}",
        claim_id=claim_id,
        actor=current_user.id,
        actor_email=current_user.email,
        action="reopen",
        before={"status": status},
        after={"status": "reopened"},
        reason=reason,
        at=at,
    )
    await database.audit_log_col.insert_one(audit.model_dump().copy())

    await database.claims_col.update_one(
        {"id": claim_id},
        {
            "$set": {
                "status": "reopened",
                "reopen": {
                    "actor": current_user.id,
                    "actor_email": current_user.email,
                    "action": "reopen",
                    "reason": reason,
                    "at": at,
                    "auditId": audit.id,
                },
                "updated_at": at,
            }
        },
    )

    # Durable timeline event: the case view, the workbench stream, and the
    # customer portal's milestone timeline all read this store.
    await emit_event(
        claim_id,
        {
            "event": "claim_reopened",
            "reason": reason,
            "actor": current_user.email,
            "at": at,
        },
    )
    logger.info("claim_reopened claim_id=%s actor=%s", claim_id, current_user.email)
    return ReopenResponse(claimId=claim_id, status="reopened", auditEntry=audit)



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
