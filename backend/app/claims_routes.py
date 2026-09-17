"""Claim support APIs: trace timeline, document uploads, evidence pack.

Collections are read through the `database` module at call time (not imported
at module load) so test patching and multi-process deployments bind correctly —
the same pattern app.auth_store and the workbench routes use. This router is
kept separate from server.py so parallel work on the core routes stays
merge-clean; server.py includes it with one line. FNOL drafts live in their
own router (app.fnol_drafts).
"""

import hashlib
import re
from datetime import datetime, timezone

from bson import Binary
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

import database
from app.deps import UserRecord, require_adjuster
from app.evidence_pack import generate_evidence_pack
from app.events import emit_event, get_claim_events

router = APIRouter()

# Upload guardrails: an allowlist beats content sniffing for the demo's threat
# model, and 10 MiB keeps every document inside BSON's 16 MiB document cap.
ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(name: str) -> str:
    base = (name or "document").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_SAFE.sub("_", base).strip("._")[:120]
    return cleaned or "document"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ============ CLAIM DOCUMENTS ============

class DocumentMeta(BaseModel):
    id: str
    filename: str
    contentType: str
    sizeBytes: int
    sha256: str
    uploadedBy: str = ""
    uploadedAt: str = ""
    documentType: str = "adjuster_upload"


class DocumentListResponse(BaseModel):
    documents: list[DocumentMeta] = []


@router.get("/claims/{claim_id}/documents", response_model=DocumentListResponse)
async def list_claim_documents(claim_id: str, current_user: UserRecord = Depends(require_adjuster)):
    await _require_claim(claim_id)
    docs = await database.claim_documents_col.find(
        {"claim_id": claim_id, "content_type": {"$exists": True}}, {"_id": 0, "blob": 0}
    ).sort("uploaded_at", -1).to_list(100)
    return {"documents": [_document_view(d) for d in docs]}


def _document_view(doc: dict) -> dict:
    return {
        "id": str(doc.get("id", doc.get("_id", ""))),
        "filename": doc.get("filename", ""),
        "contentType": doc.get("content_type", ""),
        "sizeBytes": doc.get("size_bytes", 0),
        "sha256": doc.get("sha256", ""),
        "uploadedBy": doc.get("uploaded_by", ""),
        "uploadedAt": doc.get("uploaded_at", ""),
        "documentType": doc.get("document_type", "adjuster_upload"),
    }


@router.post("/claims/{claim_id}/documents", response_model=DocumentMeta)
async def upload_claim_document(
    claim_id: str,
    file: UploadFile = File(...),
    current_user: UserRecord = Depends(require_adjuster),
):
    """Upload evidence for a claim: allowlisted types, 10 MiB cap, SHA-256 hash.

    The blob is stored with its metadata so downloads can verify integrity;
    a durable `document_uploaded` event lands on the claim's event log.
    """
    await _require_claim(claim_id)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: {content_type or 'unknown'}")

    payload = await file.read()
    if len(payload) == 0:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=422, detail="File exceeds the 10 MiB upload cap")

    filename = _sanitize_filename(file.filename or "document")
    digest = hashlib.sha256(payload).hexdigest()
    now = _utc_now()

    doc_id = f"{claim_id}-{digest[:12]}"
    existing = await database.claim_documents_col.find_one({"id": doc_id}, {"_id": 0})
    if existing:
        return _document_view(existing)  # idempotent re-upload

    await database.claim_documents_col.insert_one({
        "id": doc_id,
        "claim_id": claim_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(payload),
        "sha256": digest,
        "blob": Binary(payload),
        "uploaded_by": current_user.email,
        "uploaded_at": now,
        "document_type": "adjuster_upload",
    })
    await emit_event(claim_id, {
        "event": "document_uploaded",
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": len(payload),
        "sha256": digest,
        "uploadedBy": current_user.email,
    })
    return {
        "id": doc_id,
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": len(payload),
        "sha256": digest,
        "uploadedBy": current_user.email,
        "uploadedAt": now,
        "documentType": "adjuster_upload",
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
