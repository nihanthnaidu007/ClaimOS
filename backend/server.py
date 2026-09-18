import hashlib
import json
import uuid
from datetime import datetime, timezone
import structlog
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware

from app.analytics import collect_ops_analytics
from app.auth_routes import router as auth_router
from app.claims_routes import router as claims_router
from app.config import settings
from app.counters import next_claim_number
from app.deps import (
    UserRecord,
    require_adjuster,
    require_authenticated,
    verify_csrf,
)
from app.document_requests import router as document_requests_router
from app.events import emit_event, tail_claim_events
from app.fnol_drafts import router as fnol_drafts_router
from app.logging_setup import configure_logging
from app.middleware import RequestIdMiddleware
from app.rate_limit import limiter
from app.schemas import (
    AuditEntry,
    ClaimPdfResponse,
    ClaimRecord,
    ClaimSubmission,
    DashboardStatsResponse,
    HealthResponse,
    OpsAnalyticsResponse,
    PolicyRecord,
    ReadyResponse,
    RootStatusResponse,
    SettlementCreate,
    SettlementResponse,
    SettlementRecordOut,
    SubmitClaimResponse,
    UploadedDocumentResponse,
)
from app.workbench_routes import router as workbench_router
from app.status_portal import access_code_hash, generate_access_code
from app.status_routes import router as status_router
from app.notify_routes import router as notify_router
from app.notifications.emails import send_access_code_email
from app.storage import get_provider, new_storage_key, sanitize_filename
from agents import PIPELINE_STAGES
from database import (
    audit_log_col,
    claim_documents_col,
    claims_col,
    db,
    policies_col,
    seed_database,
    seed_demo_users,
)
from pipeline import enqueue_claim_run
from pdf_generator import generate_claim_pdf

app = FastAPI(
    # CSRF defense applies app-wide to unsafe methods (Origin/Referer check);
    # cookie-authenticated routes add the session-bound token check in-route.
    dependencies=[Depends(verify_csrf)]
)
api_router = APIRouter(prefix="/api")

# Rate limiting: slowapi needs the limiter on app.state; the login/FNOL
# decorators enforce per-route limits.
app.state.limiter = limiter

configure_logging(settings.environment)
logger = structlog.get_logger("claimos.server")


# ============ CLAIM ID GENERATOR ============

async def generate_claim_id():
    """Derive the next claim ID from the atomic Mongo counter (audit finding: shared counter)."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    seq = await next_claim_number(date_str)
    return f"CLM-{date_str}-{seq:03d}"


# ============ DURABLE SSE ============

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Access-Control-Allow-Origin": "*",
    "X-Accel-Buffering": "no",
}


def _sse_frame(seq: int, doc: dict) -> str:
    """SSE frame with a numeric id — the Last-Event-ID replay cursor.

    The stored doc is {event, data, ...}; the frame payload restores the
    original flat shape ({"event": ..., **data}) so the existing
    onmessage-based frontend keeps working unchanged.
    """
    payload = {"event": doc.get("event", "unknown"), **(doc.get("data") or {})}
    return f"id: {seq}\ndata: {json.dumps(payload)}\n\n"


async def _event_stream(claim_id: str, last_event_id: int):
    """Tail the claim's durable event log, replaying after last_event_id.

    Served straight from the events collection: any API replica can serve any
    client, and a reconnect resumes exactly where its Last-Event-ID left off —
    there is no per-process queue left to lose. The tail yields None as a
    heartbeat tick when idle and ends itself after a terminal event.
    """
    async for tick in tail_claim_events(claim_id, after_seq=last_event_id):
        if tick is None:
            yield ": heartbeat\n\n"
            continue
        yield _sse_frame(tick["seq"], tick)


@api_router.get("/events/streams/{claim_id}")
async def stream_claim_events(
    claim_id: str, request: Request, current_user: UserRecord = Depends(require_authenticated)
):
    last_event_id = 0
    header = request.headers.get("last-event-id")
    if header is not None:
        try:
            last_event_id = int(header)
        except ValueError:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer")

    return StreamingResponse(
        _event_stream(claim_id, last_event_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@api_router.get("/claims/stream/{claim_id}")
async def stream_claim(
    claim_id: str, request: Request, current_user: UserRecord = Depends(require_authenticated)
):
    """Compatibility alias for the pre-worker SSE route (same durable stream)."""
    return await stream_claim_events(claim_id, request)


# ============ CLAIMS ============

@api_router.post("/claims", response_model=SubmitClaimResponse)
@limiter.limit(settings.fnol_rate_limit)
async def submit_claim(
    request: Request,
    submission: ClaimSubmission,
    current_user: UserRecord = Depends(require_authenticated),
):
    claim_id = await generate_claim_id()
    access_code = generate_access_code()
    now = datetime.now(timezone.utc).isoformat()

    # The claim record must exist the instant the API responds — for two
    # reasons: wizard attachments upload against it right away and SSE
    # consumers poll it while the worker runs, AND the public status page
    # resolves the claim number + access code immediately. $setOnInsert can
    # never clobber a terminal document the worker has already replaced
    # (upsert races resolve in favor of the worker's rollup).
    await claims_col.update_one(
        {"id": claim_id},
        {
            "$setOnInsert": {
                "id": claim_id,
                "policy_number": submission.policyNumber,
                "claim_date": now,
                "incident_date": submission.incidentDate,
                "incident_type": submission.incidentType,
                "claimed_amount": submission.claimedAmount,
                "status": "pending",
                "risk_score": 0,
                "decision_reason": "",
                "agent_trace": {},
                "agent_logs": [],
                "holder_name": submission.holderName,
                "contact_email": submission.contactEmail,
                "is_historical": False,
                "created_at": now,
                "access_code": access_code,
                "access_code_hash": access_code_hash(access_code),
            }
        },
        upsert=True,
    )

    # Durable dispatch: the claim_runs row IS the queue. A worker claims it
    # atomically; the API process never executes the pipeline itself.
    await enqueue_claim_run(claim_id, submission.model_dump(), access_code=access_code)
    await emit_event(claim_id, {"event": "claim_submitted", "claim_id": claim_id})
    # Access code by email (AC-1.2): claims with a contact email get the code
    # delivered; claims without one skip silently. Delivery failures are
    # logged, never raised — the submission must not fail on derived data.
    await send_access_code_email(
        claim_id,
        recipient_email=submission.contactEmail,
        access_code=access_code,
        holder_name=submission.holderName,
    )
    logger.info("claim_submitted", claim_id=claim_id)

    return {
        "claimId": claim_id,
        "message": "Claim queued for processing.",
        "accessCode": access_code,
    }

@api_router.get("/claims", response_model=list[ClaimRecord])
async def get_claims(current_user: UserRecord = Depends(require_adjuster)):
    claims = await claims_col.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return claims


@api_router.get("/claims/{claim_id}", response_model=ClaimRecord)
async def get_claim(
    claim_id: str, current_user: UserRecord = Depends(require_adjuster)
):
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@api_router.get("/claims/{claim_id}/pdf", response_model=ClaimPdfResponse)
async def get_claim_pdf(
    claim_id: str, current_user: UserRecord = Depends(require_adjuster)
):
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    # Reconstruct state from stored data
    state = {
        "claimId": claim["id"],
        "input": {"policyNumber": claim.get("policy_number", "")},
        "intake": claim.get("agent_trace", {}).get("intake", {}),
        "policy": claim.get("agent_trace", {}).get("policy", {}),
        "documents": claim.get("agent_trace", {}).get("documents", {}),
        "eligibility": claim.get("agent_trace", {}).get("eligibility", {}),
        "decision": claim.get("agent_trace", {}).get("decision", {}),
    }

    # Lookup policy data for the PDF
    policy_number = claim.get("policy_number", "")
    policy_doc = await policies_col.find_one({"policy_number": policy_number}, {"_id": 0})
    if policy_doc:
        state["policy"]["policyData"] = policy_doc

    pdf_base64 = generate_claim_pdf(state)
    return {"pdf": pdf_base64, "claimId": claim_id}


# ============ POLICIES ============

@api_router.get("/policies", response_model=list[PolicyRecord])
async def get_policies(current_user: UserRecord = Depends(require_adjuster)):
    policies = await policies_col.find({}, {"_id": 0}).to_list(100)
    return policies


@api_router.get("/policies/lookup", response_model=PolicyRecord)
async def lookup_policy(
    policy_number: str = "", current_user: UserRecord = Depends(require_adjuster)
):
    if not policy_number:
        raise HTTPException(status_code=400, detail="Policy number required")
    policy = await policies_col.find_one({"policy_number": policy_number}, {"_id": 0})
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@api_router.get("/policies/search", response_model=list[PolicyRecord])
async def search_policies(
    q: str = "", current_user: UserRecord = Depends(require_adjuster)
):
    if not q:
        return await policies_col.find({}, {"_id": 0}).to_list(100)

    results = await policies_col.find(
        {"$or": [
            {"policy_number": {"$regex": q, "$options": "i"}},
            {"holder_name": {"$regex": q, "$options": "i"}},
            {"policy_type": {"$regex": q, "$options": "i"}}
        ]},
        {"_id": 0}
    ).to_list(100)
    return results


# ============ DOCUMENTS ============

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upload_content_type_allowlist() -> set[str]:
    return {
        part.strip().lower()
        for part in settings.upload_allowed_content_types.split(",")
        if part.strip()
    }


@api_router.post(
    "/claims/{claim_id}/documents",
    response_model=UploadedDocumentResponse,
    status_code=201,
)
@limiter.limit("30/minute")
async def upload_claim_document(
    claim_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: UserRecord = Depends(require_adjuster),
):
    """Attach one document to a claim (spec Tier 3).

    Validation fails closed before storage: 413 over the size cap, 415 on a
    non-allowlisted content type, 422 for an empty body. Filenames are
    sanitized to safe basenames; blobs land under the storage provider with
    a collision-proof per-claim key.
    """
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in _upload_content_type_allowlist():
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content type: {content_type or 'unknown'}. "
            f"Allowed: {settings.upload_allowed_content_types}",
        )

    max_bytes = settings.upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {max_bytes} byte upload cap",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if total == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    file_name = sanitize_filename(file.filename)
    storage_key = new_storage_key(claim_id, file_name)
    stored = await get_provider().save(
        key=storage_key, content=content, content_type=content_type
    )

    doc = {
        "id": f"doc_{uuid.uuid4().hex[:12]}",
        "claim_id": claim_id,
        "document_type": "upload",
        "file_name": file_name,
        "content_type": content_type,
        "size_bytes": stored.size_bytes,
        "storage_key": stored.storage_key,
        "sha256": hashlib.sha256(content).hexdigest(),
        "uploaded_at": _now(),
        "uploaded_by": current_user.email,
    }
    await claim_documents_col.insert_one(doc.copy())
    await emit_event(
        claim_id,
        {
            "event": "document_uploaded",
            "documentId": doc["id"],
            "fileName": file_name,
            "contentType": content_type,
            "sizeBytes": stored.size_bytes,
            "uploadedBy": current_user.email,
        },
    )
    logger.info("document_uploaded claim_id=%s key=%s", claim_id, storage_key)
    return UploadedDocumentResponse(**doc)


@api_router.get(
    "/claims/{claim_id}/documents",
    response_model=list[UploadedDocumentResponse],
)
async def list_claim_documents(
    claim_id: str, current_user: UserRecord = Depends(require_adjuster)
):
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    docs = (
        await claim_documents_col.find(
            {"claim_id": claim_id, "document_type": "upload"}, {"_id": 0}
        )
        .sort("uploaded_at", -1)
        .to_list(100)
    )
    return docs


# ============ SETTLEMENT (record-only) ============


@api_router.post("/claims/{claim_id}/settlement", response_model=SettlementResponse, status_code=201)
@limiter.limit("30/minute")
async def record_claim_settlement(
    claim_id: str,
    request: Request,
    body: SettlementCreate,
    current_user: UserRecord = Depends(require_adjuster),
):
    """Record settlement facts on a claim (spec: record-only).

    No money moves and no payment provider is contacted — this endpoint is
    the system of record: it stamps who recorded what, appends an audit_log
    row, and emits a durable event for the case timeline.
    """
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.get("settlement"):
        raise HTTPException(
            status_code=409,
            detail="Settlement already recorded for this claim",
        )

    record = {
        "amount": body.amount,
        "method": body.method,
        "reference": body.reference.strip(),
        "settled_at": _now(),
        "recorded_by": current_user.email,
    }
    await claims_col.update_one(
        {"id": claim_id}, {"$set": {"settlement": record}}
    )

    # Append-only audit: the settlement's reference doubles as the audit
    # reason (the why-bearer for a record-only action).
    audit = AuditEntry(
        id=f"aud_{uuid.uuid4().hex[:12]}",
        claim_id=claim_id,
        actor=current_user.id,
        actor_email=current_user.email,
        action="settlement_recorded",
        before={},
        after=dict(record),
        reason=record["reference"] or "Settlement recorded (no reference provided)",
        at=record["settled_at"],
    )
    await audit_log_col.insert_one(audit.model_dump().copy())

    await emit_event(
        claim_id,
        {
            "event": "settlement_recorded",
            "amount": record["amount"],
            "method": record["method"],
            "reference": record["reference"],
            "recordedBy": record["recorded_by"],
        },
    )
    logger.info(
        "settlement_recorded claim_id=%s amount=%s method=%s",
        claim_id,
        record["amount"],
        record["method"],
    )
    return SettlementResponse(
        claimId=claim_id,
        status=claim.get("status", ""),
        settlement=SettlementRecordOut(**record),
        auditEntry=audit,
    )


# ============ DASHBOARD ============

@api_router.get("/dashboard/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(current_user: UserRecord = Depends(require_adjuster)):
    total_claims = await claims_col.count_documents({})
    # auto_approved (STP-gated) counts as approved; escalated lands in review.
    approved = await claims_col.count_documents(
        {"status": {"$in": ["approved", "auto_approved"]}}
    )
    rejected = await claims_col.count_documents({"status": "rejected"})
    under_review = await claims_col.count_documents(
        {"status": {"$in": ["under_review", "escalate", "escalated"]}}
    )
    pending = await claims_col.count_documents({"status": "pending"})

    # Get total payout
    pipeline_agg = [
        {"$match": {"status": {"$in": ["approved", "auto_approved"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$claimed_amount"}}}
    ]
    payout_result = await claims_col.aggregate(pipeline_agg).to_list(1)
    total_payout = payout_result[0]["total"] if payout_result else 0

    # Average risk score
    risk_pipeline = [
        {"$match": {"risk_score": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$risk_score"}}}
    ]
    risk_result = await claims_col.aggregate(risk_pipeline).to_list(1)
    avg_risk = round(risk_result[0]["avg"], 1) if risk_result else 0

    # Recent claims; explicit .limit() keeps the cap testable (mongomock's
    # to_list(length) is unbounded) and identical on real Motor.
    recent = (
        await claims_col.find(
            {},
            {"_id": 0, "id": 1, "policy_number": 1, "status": 1, "claimed_amount": 1,
             "risk_score": 1, "holder_name": 1, "incident_type": 1, "created_at": 1,
             "fraud_flags": 1},
        )
        .sort("created_at", -1)
        .limit(5)
        .to_list(None)
    )

    active_policies = await policies_col.count_documents({"status": "active"})

    return {
        "totalClaims": total_claims,
        "approved": approved,
        "rejected": rejected,
        "underReview": under_review,
        "pending": pending,
        "totalPayout": total_payout,
        "avgRiskScore": avg_risk,
        "activePolicies": active_policies,
        "recentClaims": recent
    }


# ============ HEALTH ============

@api_router.get("/analytics/ops", response_model=OpsAnalyticsResponse)
async def get_ops_analytics(current_user: UserRecord = Depends(require_adjuster)):
    """The five ops metric groups (spec Tier 3, AC-9): adjuster-only by role."""
    return await collect_ops_analytics()


@api_router.get("/", response_model=RootStatusResponse)
async def root():
    return {"status": "ok", "service": "ClaimOS API", "agents": len(PIPELINE_STAGES)}


@api_router.get("/health", response_model=HealthResponse)
async def health():
    """Liveness probe: the process is up. Never touches the database."""
    return {"status": "ok"}


@api_router.get("/ready", response_model=ReadyResponse)
async def ready():
    """Readiness probe: verifies MongoDB connectivity with a ping command."""
    try:
        await db.command("ping")
        return {"status": "ready", "database": "connected"}
    except Exception:
        logger.exception("readiness_probe_failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None


# ============ APP SETUP ============

api_router.include_router(auth_router)  # /auth/* under the /api prefix
api_router.include_router(workbench_router)  # adjuster-gated workbench under /api
api_router.include_router(status_router)  # /status/* public portal endpoints
api_router.include_router(notify_router)  # /notifications/* authenticated
api_router.include_router(claims_router)  # trace, documents, evidence pack
api_router.include_router(document_requests_router)  # adjuster document checklists (spec F3)
api_router.include_router(fnol_drafts_router)  # resumable FNOL drafts
app.include_router(api_router)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """slowapi's default handler returns plain text; make 429s JSON like the rest of the API."""
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})


# CORS: origins come from settings (CORS_ORIGINS as a list). Wildcard origins
# cannot be combined with credentials per the CORS spec, so credentials are
# only enabled for an explicit origin allowlist.
allow_origins = settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_credentials=allow_origins != ["*"],
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)


@app.on_event("startup")
async def startup():
    await seed_database()
    await seed_demo_users()
    logger.info("claimos_api_ready", environment=settings.environment, agents=5)


@app.on_event("shutdown")
async def shutdown():
    from database import client
    client.close()
