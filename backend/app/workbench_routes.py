"""Adjuster workbench API — queue, case summary, override, audit trail.

Every route is adjuster-gated (require_adjuster). Read surfaces assemble
deterministically from stored claim data — the queue and case summary never
call the LLM. The override endpoint is the workbench's one write path: it
requires a reason (422 without one), stamps the claim, and appends an
immutable audit_log entry.

Spec F12 adds two more write surfaces, both audit-first:
- Saved views (`workbench_views`): named queue-filter presets, private to
  their owner — every read is scoped by owner_id, and delete/apply 404 other
  owners' views without leaking that they exist.
- Bulk actions (`POST /claims/bulk`): reassign or flag-for-review applied as
  a loop of audited single actions — every touched claim gets its own
  audit_log entry, and per-claim failures are reported in the response
  instead of being silently dropped.

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
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

import database
from app.assignment import assignment_fields
from app.config import settings
from app.deps import UserRecord, require_adjuster
from app.events import emit_event, get_claim_events
from app.rate_limit import limiter
from app.schemas import (
    AuditEntry,
    BulkActionRequest,
    BulkActionResponse,
    BulkActionResultItem,
    CaseSummaryResponse,
    OverrideRequest,
    OverrideResponse,
    ReassignRequest,
    ReassignResponse,
    SavedViewApplyResponse,
    SavedViewCreate,
    SavedViewOut,
    WorkbenchQueueResponse,
)
from app.workbench import (
    REVIEWABLE_STATUSES,
    DECIDED_STATUSES,
    VIEW_FILTER_KEYS,
    build_case_summary,
    matches_search,
    normalize_view_filters,    queue_row,
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
        search: str = "",
        assignee: str = "",
    ):
        self.statuses = [s.strip() for s in status.split(",") if s.strip()] or list(
            QUEUE_DEFAULT_STATUSES
        )
        self.severities = [s.strip().lower() for s in severity.split(",") if s.strip()]
        self.min_age_hours = min_age_hours
        self.max_age_hours = max_age_hours
        self.search = search.strip()
        if assignee not in ("", "mine", "unassigned"):
            raise HTTPException(status_code=400, detail="assignee must be mine, unassigned, or empty")
        self.assignee = assignee
        # Set by the routes (never parsed from the query string): the acting
        # adjuster that the "mine" chip resolves against.
        self.current_user: UserRecord | None = None
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
    # Assignment chips (spec F10): "mine" resolves against the acting adjuster
    # server-side — a client-supplied user id is never trusted; "unassigned"
    # matches an empty/absent assignee_id (legacy rows included).
    if params.assignee == "mine":
        if params.current_user is None:  # defensive: routes always set it
            return []
        filtered = [row for row in filtered if row.get("assignee_id") == params.current_user.id]
    elif params.assignee == "unassigned":
        filtered = [row for row in filtered if not row.get("assignee_id")]
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


def _apply_search_filter(rows: list[dict], params: QueueParams) -> list[dict]:
    """Search narrows the enriched rows (spec F8); a blank query is a no-op."""
    if not params.search:
        return rows
    return [row for row in rows if matches_search(row, params.search)]


async def load_queue_rows(params: QueueParams) -> list[dict]:
    """Fetch the status-filtered claims and enrich each with severity + SLA."""
    claims = await database.claims_col.find(
        {"status": {"$in": params.statuses}}, {"_id": 0}
    ).to_list(500)
    rows = _apply_row_filters([queue_row(claim) for claim in claims], params)
    rows = _apply_search_filter(rows, params)
    return _sort_rows(rows, params)


def queue_digest(rows: list[dict]) -> str:
    """Stable digest of the fields a queue renders — the SSE stream only emits
    a frame when this changes (aging state moves, statuses change, rows appear,
    a claim is reassigned)."""
    material = [
        [
            row["id"],
            row["status"],
            row["sla"]["state"],
            round(row["sla"]["hoursElapsed"], 1),
            row.get("assignee_id") or "",  # spec F10: reassignment refetches
        ]
        for row in rows
    ]
    return hashlib.sha1(json.dumps(material).encode()).hexdigest()


@router.get("/queue", response_model=WorkbenchQueueResponse)
async def get_queue(
    params: QueueParams = Depends(), current_user: UserRecord = Depends(require_adjuster)
):
    params.current_user = current_user
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
async def stream_queue(
    params: QueueParams = Depends(), current_user: UserRecord = Depends(require_adjuster)
):
    params.current_user = current_user
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


# ---- Manual reassignment (spec F10) ----


@router.post("/claims/{claim_id}/assignee", response_model=ReassignResponse)
async def reassign_claim(
    claim_id: str, request: ReassignRequest, adjuster: UserRecord = Depends(require_adjuster)
):
    """Audited manual assignment change (spec F10.2).

    Mirror of the override write path: adjuster-gated, reason required (422
    via the schema), unknown claim or target user → 404, and every change
    lands in the same immutable audit trail. The customer portal path never
    reads assignee fields, so this write cannot leak into it.
    """
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    target = await database.users_col.find_one({"id": request.assigneeId}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Assignee not found")
    target_user = UserRecord(**target)
    if target_user.role != "adjuster" or not target_user.active:
        raise HTTPException(
            status_code=409, detail="Assignee must be an active adjuster"
        )

    at = _now_iso()
    before = {"assigneeId": claim.get("assignee_id"), "assignedBy": claim.get("assigned_by")}
    audit_entry = await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "reassign",
            "before": before,
            "after": {"assigneeId": target_user.id, "assignedBy": adjuster.email},
            "reason": request.reason.strip(),
            "at": at,
        }
    )
    await database.claims_col.update_one(
        {"id": claim_id},
        {
            "$set": {
                # assignment_fields stamps assigned_by with the human actor's
                # email — this assignment was made by a person, not the picker.
                **assignment_fields(target_user.id, datetime.now(timezone.utc)),
                "assigned_by": adjuster.email,
                "updated_at": at,
            }
        },
    )
    await emit_event(
        claim_id,
        {
            "event": "claim_reassigned",
            "assigneeId": target_user.id,
            "actor": adjuster.email,
            "reason": request.reason.strip(),
            "at": at,
        },
    )
    logger.info(
        "claim_reassigned claim_id=%s actor=%s assignee=%s",
        claim_id,
        adjuster.email,
        target_user.email,
    )
    return {
        "claimId": claim_id,
        "assigneeId": target_user.id,
        "assignedAt": at,
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

# ============ SAVED VIEWS (spec F12) ============


def _view_out(doc: dict) -> SavedViewOut:
    """Stored view doc -> response model (filters_json parsed for the client)."""
    try:
        filters = json.loads(doc.get("filters_json") or "{}")
    except ValueError:
        filters = {}
    return SavedViewOut(
        id=doc.get("id", ""),
        name=doc.get("name", ""),
        filters=filters if isinstance(filters, dict) else {},
        owner_id=doc.get("owner_id", ""),
        createdAt=doc.get("created_at", ""),
    )


@router.post("/views", response_model=SavedViewOut, status_code=201)
@limiter.limit("30/minute")
async def save_view(
    request: Request,
    body: SavedViewCreate,
    adjuster: UserRecord = Depends(require_adjuster),
):
    """Save the caller's queue filters under a name (owner-private).

    Saving the same name twice replaces the stored preset — a view is a
    bookmark, not an event log, so re-saving converges instead of forking
    (the unique (owner_id, name) index backs this).
    """
    try:
        cleaned = normalize_view_filters(body.filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    now = _now_iso()
    existing = await database.workbench_views_col.find_one(
        {"owner_id": adjuster.id, "name": body.name}, {"_id": 0}
    )
    if existing:
        await database.workbench_views_col.update_one(
            {"id": existing["id"]},
            {"$set": {"filters_json": json.dumps(cleaned), "updated_at": now}},
        )
        existing["filters_json"] = json.dumps(cleaned)
        return _view_out(existing)

    doc = {
        "id": f"vw_{secrets.token_hex(6)}",
        "owner_id": adjuster.id,
        "name": body.name,
        "filters_json": json.dumps(cleaned),
        "created_at": now,
    }
    await database.workbench_views_col.insert_one(doc.copy())
    doc.pop("_id", None)
    logger.info("workbench_view_saved owner=%s name=%s", adjuster.email, body.name)
    return _view_out(doc)


@router.get("/views", response_model=list[SavedViewOut])
async def list_views(adjuster: UserRecord = Depends(require_adjuster)):
    """The caller's own views, newest first. Owner-scoped by construction."""
    docs = (
        await database.workbench_views_col.find(
            {"owner_id": adjuster.id}, {"_id": 0}
        )
        .sort("created_at", -1)
        .to_list(200)
    )
    return [_view_out(doc) for doc in docs]


@router.delete("/views/{view_id}")
async def delete_view(view_id: str, adjuster: UserRecord = Depends(require_adjuster)):
    """Delete one of the caller's views. Another owner's view is a 404 —
    scoped by owner_id so existence is never leaked across accounts."""
    result = await database.workbench_views_col.delete_one(
        {"id": view_id, "owner_id": adjuster.id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="View not found")
    return {"deleted": True, "id": view_id}


@router.get("/views/{view_id}/apply", response_model=SavedViewApplyResponse)
async def apply_view(view_id: str, adjuster: UserRecord = Depends(require_adjuster)):
    """Run the queue machinery over a view's stored filters (owner-scoped)."""
    doc = await database.workbench_views_col.find_one(
        {"id": view_id, "owner_id": adjuster.id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="View not found")

    view = _view_out(doc)
    try:
        stored = json.loads(doc.get("filters_json") or "{}")
        if not isinstance(stored, dict):
            raise ValueError("filters must be an object")
        # Lenient on read: save-time normalization already rejects unknown
        # keys; a corrupt legacy doc degrades to the default queue instead of
        # a 500, and the warning makes the degradation visible.
        cleaned = normalize_view_filters(stored)
    except ValueError:
        logger.warning("workbench_view_filters_invalid view_id=%s", view_id)
        cleaned = {}

    params = QueueParams(**{k: cleaned[k] for k in cleaned if k in VIEW_FILTER_KEYS})
    rows = await load_queue_rows(params)
    return {"view": view, "rows": rows, "generatedAt": _now_iso()}


# ============ BULK ACTIONS (spec F12) ============


class _BulkClaimError(Exception):
    """One claim's bulk step failed; `detail` is the per-claim outcome text."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


async def _bulk_reassign_one(
    claim_id: str, target: dict, adjuster: UserRecord, reason: str, at: str
) -> dict:
    """Reassign one claim to a validated adjuster; one audit entry, one event."""
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise _BulkClaimError("Claim not found")

    before_assignee = claim.get("assignee_id") or ""
    audit_entry = await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "reassign",
            "before": {"assignee_id": before_assignee},
            "after": {"assignee_id": target["id"], "assignee_email": target["email"]},
            "reason": reason,
            "at": at,
        }
    )
    await database.claims_col.update_one(
        {"id": claim_id},
        {
            "$set": {
                "assignee_id": target["id"],
                "assignee_email": target["email"],
                "updated_at": at,
            }
        },
    )
    await emit_event(
        claim_id,
        {
            "event": "claim_reassigned",
            "assigneeId": target["id"],
            "assigneeEmail": target["email"],
            "actor": adjuster.email,
            "reason": reason,
            "at": at,
        },
    )
    return audit_entry


async def _bulk_flag_one(
    claim_id: str, adjuster: UserRecord, reason: str, at: str
) -> dict:
    """Flag one claim for review; one audit entry, one event."""
    claim = await database.claims_col.find_one({"id": claim_id}, {"_id": 0})
    if not claim:
        raise _BulkClaimError("Claim not found")

    existing_flags = claim.get("flags") or []
    flag = {"reason": reason, "flagged_by": adjuster.email, "flagged_at": at}
    audit_entry = await _append_audit_entry(
        {
            "claim_id": claim_id,
            "actor": adjuster.id,
            "actor_email": adjuster.email,
            "action": "flag_for_review",
            "before": {"flagged": bool(existing_flags)},
            "after": {"flagged": True, "flag": flag},
            "reason": reason,
            "at": at,
        }
    )
    await database.claims_col.update_one(
        {"id": claim_id}, {"$push": {"flags": flag}, "$set": {"updated_at": at}}
    )
    await emit_event(
        claim_id,
        {
            "event": "claim_flagged",
            "reason": reason,
            "actor": adjuster.email,
            "at": at,
        },
    )
    return audit_entry


@router.post("/claims/bulk", response_model=BulkActionResponse)
@limiter.limit("30/minute")
async def bulk_claims_action(
    request: Request,
    body: BulkActionRequest,
    adjuster: UserRecord = Depends(require_adjuster),
):
    """Apply one action to many claims as a loop of audited single actions.

    Never one opaque write: each claim gets its own audit entry, and a claim
    that fails (unknown id) reports a per-claim outcome while the rest still
    apply. The reassign target is validated up front so a typo'd adjuster
    cannot produce a half-applied run.
    """
    # Dedupe preserving order — the same id twice must not double-apply.
    claim_ids = list(dict.fromkeys(body.claimIds))
    reason = body.reason.strip()
    at = _now_iso()

    target: dict | None = None
    if body.action == "reassign":
        target_value = body.target.strip()
        if not target_value:
            raise HTTPException(
                status_code=422, detail="A target adjuster is required to reassign"
            )
        target = await database.users_col.find_one(
            {"$or": [{"id": target_value}, {"email": target_value.lower()}]},
            {"_id": 0},
        )
        if not target or target.get("role") != "adjuster":
            raise HTTPException(
                status_code=404, detail=f"Target adjuster not found: {target_value}"
            )

    async def _apply_one(claim_id: str) -> BulkActionResultItem:
        try:
            if body.action == "reassign":
                audit_entry = await _bulk_reassign_one(
                    claim_id, target, adjuster, reason, at
                )
            else:
                audit_entry = await _bulk_flag_one(claim_id, adjuster, reason, at)
            return BulkActionResultItem(
                claimId=claim_id, status="updated", auditId=audit_entry["id"]
            )
        except _BulkClaimError as exc:
            # Per-claim failure: reported, never silently dropped. The loop
            # continues so one bad id cannot eat the batch.
            return BulkActionResultItem(
                claimId=claim_id, status="failed", detail=exc.detail
            )

    results = [await _apply_one(claim_id) for claim_id in claim_ids]
    updated = sum(1 for item in results if item.status == "updated")
    failed = len(results) - updated
    logger.info(
        "bulk_action_applied action=%s actor=%s updated=%d failed=%d",
        body.action,
        adjuster.email,
        updated,
        failed,
    )
    return BulkActionResponse(
        action=body.action, results=results, updated=updated, failed=failed
    )
