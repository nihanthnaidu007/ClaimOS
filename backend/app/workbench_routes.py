"""Adjuster workbench API — queue, case summary, override, audit trail.

Every route is adjuster-gated (require_adjuster). Read surfaces assemble
deterministically from stored claim data — the queue and case summary never
call the LLM. The override endpoint is the workbench's one write path: it
requires a reason (422 without one), stamps the claim, and appends an
immutable audit_log entry.

Collections are read through the `database` module at call time (not imported
at module load) so test patching and multi-process deployments both bind
correctly — the same pattern app.auth_store uses.
"""

import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

import database
from app.config import settings
from app.deps import UserRecord, require_adjuster
from app.events import emit_event, get_claim_events
from app.schemas import (
    AuditEntry,
    CaseSummaryResponse,
    OverrideRequest,
    OverrideResponse,
    WorkbenchQueueResponse,
)
from app.workbench import (
    REVIEWABLE_STATUSES,
    DECIDED_STATUSES,
    build_case_summary,
    queue_row,
)

logger = structlog.get_logger("claimos.workbench")

router = APIRouter(prefix="/workbench", dependencies=[Depends(require_adjuster)])

QUEUE_DEFAULT_STATUSES = sorted(REVIEWABLE_STATUSES)

_SORT_KEYS = {"age", "severity", "risk", "created_at"}

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueParams:
    """Parsed queue filters, shared by the REST endpoint and the SSE stream."""

    def __init__(
        self,
        status: str = ",".join(QUEUE_DEFAULT_STATUSES),
        severity: str = "",
        min_age_hours: float | None = None,
        max_age_hours: float | None = None,
        sort: str = "age",
        direction: str = "asc",
    ):
        self.statuses = [s.strip() for s in status.split(",") if s.strip()] or list(
            QUEUE_DEFAULT_STATUSES
        )
        self.severities = [s.strip().lower() for s in severity.split(",") if s.strip()]
        self.min_age_hours = min_age_hours
        self.max_age_hours = max_age_hours
        if sort not in _SORT_KEYS:
            raise HTTPException(status_code=400, detail=f"sort must be one of {sorted(_SORT_KEYS)}")
        if sort == "created_at":  # alias: age and created_at order the same way
            sort = "age"
        self.sort = sort
        if direction not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="direction must be asc or desc")
        self.direction = direction


def _severity_rank(severity: str) -> int:
    return 0 if severity == "elevated" else 1  # elevated sorts as more urgent


def _apply_row_filters(rows: list[dict], params: QueueParams) -> list[dict]:
    """Severity and age filters run on enriched rows (post-SLA computation)."""
    filtered = rows
    if params.severities:
        filtered = [row for row in filtered if row["severity"] in params.severities]
    if params.min_age_hours is not None:
        filtered = [row for row in filtered if row["sla"]["hoursElapsed"] >= params.min_age_hours]
    if params.max_age_hours is not None:
        filtered = [row for row in filtered if row["sla"]["hoursElapsed"] <= params.max_age_hours]
    return filtered


def _sort_rows(rows: list[dict], params: QueueParams) -> list[dict]:
    """direction=asc means most-urgent-first for every key: oldest for age,
    elevated for severity, highest score for risk. desc reverses that."""
    urgent_last = params.direction == "desc"
    if params.sort == "severity":
        rows.sort(
            key=lambda r: (_severity_rank(r["severity"]), -r["sla"]["hoursElapsed"]),
            reverse=urgent_last,
        )
    elif params.sort == "risk":
        rows.sort(key=lambda r: r.get("risk_score") or 0, reverse=not urgent_last)
    else:  # age: ascending elapsed hours = oldest first
        rows.sort(key=lambda r: r["sla"]["hoursElapsed"], reverse=not urgent_last)
    return rows


async def load_queue_rows(params: QueueParams) -> list[dict]:
    """Fetch the status-filtered claims and enrich each with severity + SLA."""
    claims = await database.claims_col.find(
        {"status": {"$in": params.statuses}}, {"_id": 0}
    ).to_list(500)
    rows = _apply_row_filters([queue_row(claim) for claim in claims], params)
    return _sort_rows(rows, params)


def queue_digest(rows: list[dict]) -> str:
    """Stable digest of the fields a queue renders — the SSE stream only emits
    a frame when this changes (aging state moves, statuses change, rows appear)."""
    material = [
        [
            row["id"],
            row["status"],
            row["sla"]["state"],
            round(row["sla"]["hoursElapsed"], 1),
        ]
        for row in rows
    ]
    return hashlib.sha1(json.dumps(material).encode()).hexdigest()


@router.get("/queue", response_model=WorkbenchQueueResponse)
async def get_queue(params: QueueParams = Depends()):
    rows = await load_queue_rows(params)
    return {"rows": rows, "generatedAt": _now_iso()}


# ============ QUEUE STREAM (SSE) ============


async def _queue_stream(params: QueueParams):
    """Snapshot stream over the durable store.

    The events collection is per-claim sequenced (counters are namespaced by
    claim), so there is no global order to tail; instead the stream re-reads
    the queue on an interval and emits a frame only when the rendered digest
    changes. Like the per-claim SSE tail, it reads only the durable store —
    no in-process state — so any API replica can serve any subscriber.
    """
    last_digest: str | None = None
    while True:
        try:
            rows = await load_queue_rows(params)
            digest = queue_digest(rows)
            if digest != last_digest:
                last_digest = digest
                payload = {"rows": rows, "generatedAt": _now_iso()}
                yield f"data: {json.dumps({'event': 'queue_update', **payload})}\n\n"
            else:
                yield ": keep-alive\n\n"
        except Exception:  # noqa: BLE001 — a failed tick degrades to a heartbeat
            logger.exception("queue_stream_tick_failed")
            yield ": keep-alive\n\n"
        await asyncio.sleep(settings.workbench_stream_interval_seconds)


@router.get("/stream")
async def stream_queue(params: QueueParams = Depends()):
    return StreamingResponse(_queue_stream(params), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


# ============ CASE VIEW ============


async def _claim_or_404(claim_id: str) -> dict:
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@router.get("/claims/{claim_id}/summary", response_model=CaseSummaryResponse)
async def get_case_summary(claim_id: str):
    claim = await _claim_or_404(claim_id)
    return build_case_summary(claim)


@router.get("/claims/{claim_id}/events")
async def get_case_events(claim_id: str):
    await _claim_or_404(claim_id)
    events = await get_claim_events(claim_id, after_seq=0, limit=1000)
    return {"claimId": claim_id, "events": events}


@router.get("/claims/{claim_id}/letter")
async def get_decision_letter(claim_id: str):
    """Decision letter as persisted by the decision agent — read-only, no LLM."""
    claim = await _claim_or_404(claim_id)
    decision = (claim.get("agent_trace") or {}).get("decision") or {}
    body = decision.get("letterBody")
    if not body:
        raise HTTPException(status_code=404, detail="No decision letter recorded for this claim")
    return {"claimId": claim_id, "subject": decision.get("letterSubject"), "body": body}


# ============ OVERRIDE + AUDIT ============


async def _append_audit_entry(entry: dict) -> dict:
    doc = {"id": f"aud_{secrets.token_hex(6)}", **entry}
    await database.audit_log_col.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.post("/claims/{claim_id}/override", response_model=OverrideResponse)
async def override_claim(
    claim_id: str, request: OverrideRequest, adjuster: UserRecord = Depends(require_adjuster)
):
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    status = claim.get("status")
    if status not in REVIEWABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Claim is already decided (status '{status}') — re-open it before overriding"
                if status in DECIDED_STATUSES
                else f"Claim status '{status}' cannot be overridden"
            ),
        )

    trace = claim.get("agent_trace") or {}
    decision_trace = trace.get("decision") or {}
    before = {
        "status": status,
        "verdict": decision_trace.get("verdict"),
        "payoutAmount": decision_trace.get("payoutAmount"),
        "recommendation": (trace.get("eligibility") or {}).get("recommendation"),
    }
    after = {
        "status": "overridden",
        "verdict": request.decision,
        "payoutAmount": request.payoutAmount,
    }
    at = _now_iso()

    audit_entry = await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "override",
            "before": before,
            "after": after,
            "reason": request.reason.strip(),
            "at": at,
        }
    )

    override_block = {
        "actor": adjuster.id,
        "actor_email": adjuster.email,
        "action": "override",
        "decision": request.decision,
        "payoutAmount": request.payoutAmount,
        "reason": request.reason.strip(),
        "at": at,
        "auditId": audit_entry["id"],
    }
    await database.claims_col.update_one(
        {"id": claim_id},
        {
            "$set": {
                "status": "overridden",
                "decision_reason": request.reason.strip(),
                "override": override_block,
                "updated_at": at,
            }
        },
    )

    # Live viewers (queue + case page) learn about the decision through the
    # claim's durable event stream — same store the pipeline writes.
    await emit_event(
        claim_id,
        {
            "event": "claim_overridden",
            "decision": request.decision,
            "payoutAmount": request.payoutAmount,
            "actor": adjuster.email,
            "reason": request.reason.strip(),
            "at": at,
        },
    )
    logger.info(
        "claim_overridden claim_id=%s actor=%s decision=%s",
        claim_id,
        adjuster.email,
        request.decision,
    )
    return {
        "claimId": claim_id,
        "status": "overridden",
        "auditEntry": audit_entry,
    }


@router.get("/claims/{claim_id}/audit", response_model=list[AuditEntry])
async def get_claim_audit(claim_id: str):
    await _claim_or_404(claim_id)
    cursor = (
        database.audit_log_col.find({"claim_id": claim_id}, {"_id": 0})
        .sort("at", -1)
        .limit(200)
    )
    return await cursor.to_list(200)
