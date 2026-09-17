import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware

from agents import ClaimOrchestrator
from app.config import settings
from app.counters import next_claim_number
from app.logging_setup import configure_logging
from app.middleware import RequestIdMiddleware
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
from database import claims_col, db, policies_col, seed_database
from pdf_generator import generate_claim_pdf

app = FastAPI()
api_router = APIRouter(prefix="/api")

# Store active SSE queues
sse_queues = {}

configure_logging(settings.environment)
logger = structlog.get_logger("claimos.server")


# ============ CLAIM ID GENERATOR ============

async def generate_claim_id():
    """Derive the next claim ID from the atomic Mongo counter (audit finding: shared counter)."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    seq = await next_claim_number(date_str)
    return f"CLM-{date_str}-{seq:03d}"


# ============ SSE ENDPOINT ============

@api_router.get("/claims/stream/{claim_id}")
async def stream_claim(claim_id: str, request: Request):
    queue = asyncio.Queue()
    sse_queues[claim_id] = queue

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("event") in ("pipeline_complete", "pipeline_halted"):
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
        finally:
            sse_queues.pop(claim_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no"
        }
    )


# ============ CLAIMS ============

@api_router.post("/claims", response_model=SubmitClaimResponse)
async def submit_claim(submission: ClaimSubmission):
    claim_id = await generate_claim_id()

    # Return claim ID immediately
    response = {"claimId": claim_id, "message": "Claim received. Connect to stream endpoint."}

    # Schedule pipeline to run after response is sent
    async def run_pipeline():
        await asyncio.sleep(0.5)  # Give client time to connect SSE
        queue = sse_queues.get(claim_id)
        if not queue:
            # Wait a bit more for SSE connection
            await asyncio.sleep(1.5)
            queue = sse_queues.get(claim_id)

        if not queue:
            queue = asyncio.Queue()
            sse_queues[claim_id] = queue

        orchestrator = ClaimOrchestrator(claim_id, queue)
        try:
            await orchestrator.run(submission.model_dump())
        except Exception as exc:
            logger.exception("pipeline_failed", claim_id=claim_id, error=str(exc))
            await queue.put({"event": "pipeline_error", "error": "Internal pipeline error. Please try again."})

    asyncio.create_task(run_pipeline())
    return response

@api_router.get("/claims", response_model=list[ClaimRecord])
async def get_claims():
    claims = await claims_col.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return claims


@api_router.get("/claims/{claim_id}", response_model=ClaimRecord)
async def get_claim(claim_id: str):
    claim = await claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@api_router.get("/claims/{claim_id}/pdf", response_model=ClaimPdfResponse)
async def get_claim_pdf(claim_id: str):
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
async def get_policies():
    policies = await policies_col.find({}, {"_id": 0}).to_list(100)
    return policies


@api_router.get("/policies/lookup", response_model=PolicyRecord)
async def lookup_policy(policy_number: str = ""):
    if not policy_number:
        raise HTTPException(status_code=400, detail="Policy number required")
    policy = await policies_col.find_one({"policy_number": policy_number}, {"_id": 0})
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@api_router.get("/policies/search", response_model=list[PolicyRecord])
async def search_policies(q: str = ""):
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
async def get_dashboard_stats():
    total_claims = await claims_col.count_documents({})
    approved = await claims_col.count_documents({"status": "approved"})
    rejected = await claims_col.count_documents({"status": "rejected"})
    under_review = await claims_col.count_documents({"status": {"$in": ["under_review", "escalate"]}})
    pending = await claims_col.count_documents({"status": "pending"})

    # Get total payout
    pipeline_agg = [
        {"$match": {"status": "approved"}},
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

    # Recent claims
    recent = await claims_col.find(
        {},
        {"_id": 0, "id": 1, "policy_number": 1, "status": 1, "claimed_amount": 1,
         "risk_score": 1, "holder_name": 1, "incident_type": 1, "created_at": 1},
    ).sort("created_at", -1).to_list(5)

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

app.include_router(api_router)

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
    logger.info("claimos_api_ready", environment=settings.environment, agents=5)


@app.on_event("shutdown")
async def shutdown():
    from database import client
    client.close()
