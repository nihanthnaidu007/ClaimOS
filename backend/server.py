import json
from datetime import datetime, timezone
import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware

from app.auth_routes import router as auth_router
from app.config import settings
from app.counters import next_claim_number
from app.deps import (
    UserRecord,
    require_adjuster,
    require_authenticated,
    verify_csrf,
)
from app.events import emit_event, tail_claim_events
from app.logging_setup import configure_logging
from app.middleware import RequestIdMiddleware
from app.rate_limit import limiter
from app.schemas import (
    ClaimPdfResponse,
    ClaimRecord,
    ClaimSubmission,
    DashboardStatsResponse,
    HealthResponse,
    PolicyRecord,
    ReadyResponse,
    RootStatusResponse,
    SubmitClaimResponse,
)
from database import claims_col, db, policies_col, seed_database, seed_demo_users
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

    # Return claim ID immediately
    response = {"claimId": claim_id, "message": "Claim queued for processing."}

    # Durable dispatch: the claim_runs row IS the queue. A worker claims it
    # atomically; the API process never executes the pipeline itself.
    await enqueue_claim_run(claim_id, submission.model_dump())
    logger.info("claim_submitted", claim_id=claim_id)

    return response

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
             "risk_score": 1, "holder_name": 1, "incident_type": 1, "created_at": 1},
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

@api_router.get("/", response_model=RootStatusResponse)
async def root():
    return {"status": "ok", "service": "ClaimOS API", "agents": 5}


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
