"""SLA escalation (spec F9) — a per-claim record that a breach was left to grow.

Escalation is a record, not a state toggle: `escalated_at` is set exactly once
(idempotent across repeated evaluations, never cleared) when a claim's age
crosses its SLA window times the SLA_ESCALATION_FACTOR multiplier, and stays on
the claim forever after. The worker's poll loop is the evaluator — the same
durable-processor shape as adjudication itself — so escalations land whether or
not anyone is looking at the queue.

Routing rule (spec F9): a bell notification goes ONLY to the assigned adjuster
(F10's assignee). Unassigned escalated claims surface solely in the ops
analytics escalation count — no queue-wide notification spam.
"""

from datetime import datetime, timezone
import secrets

import structlog

import database
from app.analytics import parse_timestamp
from app.config import settings
from app.notifications.provider import Notification, get_driver
from app.workbench import REVIEWABLE_STATUSES, severity_for_claim, sla_target_hours

logger = structlog.get_logger("claimos.escalation")

# Notification milestone key for the in-app bell; dedupes per claim alongside
# the customer milestones in the same collection.
ESCALATION_MILESTONE = "sla_escalated"

ESCALATION_TITLE = "SLA escalation"
ESCALATION_BODY = "Claim {claim} has crossed its SLA escalation threshold and needs attention."


def escalation_threshold_hours(severity: str) -> float:
    """The escalation threshold for one severity: SLA window × SLA_ESCALATION_FACTOR.

    Factor 1.0 (default) escalates exactly at-breach; 1.5 lets a breach age by
    half its window again before escalating. Config validation rejects
    non-positive factors at boot.
    """
    return sla_target_hours(severity) * settings.sla_escalation_factor


def escalation_due(claim: dict, *, now: datetime | None = None) -> bool:
    """Whether one claim should escalate on this evaluation (pure).

    Due when the claim is still actionable (reviewable status — decided claims
    no longer need an alert), its age has crossed the threshold, and no
    escalation is on record yet. The existing-record guard is what makes
    repeated evaluations idempotent; clears never happen because this function
    can only ever propose setting the field.
    """
    if claim.get("escalated_at"):
        return False
    if claim.get("status") not in REVIEWABLE_STATUSES:
        return False
    created = parse_timestamp(claim.get("created_at"))
    if created is None:
        return False  # unparseable rows cannot age honestly (same rule as sla_state)
    now = now or datetime.now(timezone.utc)
    if created > now:
        return False  # future-dated rows are treated as fresh, never overdue
    elapsed_hours = (now - created).total_seconds() / 3600.0
    threshold = escalation_threshold_hours(severity_for_claim(claim))
    return elapsed_hours >= threshold


async def notify_assignee_of_escalation(claim: dict) -> None:
    """Bell-notify the assigned adjuster only (spec F9 routing rule).

    Unassigned claims notify nobody here — their visibility is the ops
    analytics escalation count, so an unassigned backlog cannot spam the bell
    of every adjuster who happens to open the queue. Delivery failures are
    logged, never raised: the notification is derived data and must not fail
    the escalation write that triggered it (the same rule the milestone
    fan-out follows).
    """
    assignee_id = claim.get("assignee_id")
    if not assignee_id:
        return
    assignee = await database.users_col.find_one({"id": assignee_id}, {"_id": 0})
    if not assignee:
        logger.warning(
            "escalation_assignee_not_found", claim_id=claim.get("id"), assignee_id=assignee_id
        )
        return

    claim_id = str(claim.get("id", ""))
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": f"ntf_{secrets.token_hex(8)}",
        "claim_id": claim_id,
        "recipient_email": str(assignee.get("email", "")).strip().lower(),
        "milestone": ESCALATION_MILESTONE,
        "title": ESCALATION_TITLE,
        "body": ESCALATION_BODY.format(claim=claim_id),
        "read": False,
        "read_at": None,
        "created_at": now,
    }
    try:
        await database.notifications_col.insert_one(doc)
    except Exception:
        # A duplicate key means the record already exists — the idempotent
        # outcome, not an error. Anything else still must not fail the sweep.
        logger.exception("escalation_notification_persist_failed", claim_id=claim_id)
        return
    notification = Notification(
        id=doc["id"],
        claim_id=doc["claim_id"],
        recipient_email=doc["recipient_email"],
        milestone=doc["milestone"],
        title=doc["title"],
        body=doc["body"],
        created_at=doc["created_at"],
    )
    try:
        await get_driver().send(notification)
    except Exception:
        logger.exception("escalation_notification_delivery_failed", claim_id=claim_id)
    logger.info(
        "escalation_notified",
        claim_id=claim_id,
        assignee_id=assignee_id,
        recipient_email=doc["recipient_email"],
    )


async def sweep_escalations(
    claims: list[dict] | None = None, *, now: datetime | None = None
) -> list[str]:
    """Evaluate candidate claims and persist escalations (spec F9).

    Candidates default to the reviewable-status claims — the queue's actionable
    scope; a caller may pass pre-fetched claim docs (the queue read path does).
    The write is atomic set-once: the update filter matches only claims whose
    `escalated_at` is still absent, so repeated evaluations — from any number
    of concurrent sweeps — flip modified_count for exactly one of them, and
    only that winner notifies the assignee. Returns the ids escalated by THIS
    sweep (empty when everything was already on record).
    """
    if claims is None:
        claims = await database.claims_col.find(
            {"status": {"$in": sorted(REVIEWABLE_STATUSES)}}, {"_id": 0}
        ).to_list(500)

    escalated_ids: list[str] = []
    for claim in claims:
        if not escalation_due(claim, now=now):
            continue
        claim_id = str(claim.get("id", ""))
        escalated_at = (now or datetime.now(timezone.utc)).isoformat()
        result = await database.claims_col.update_one(
            {"id": claim_id, "escalated_at": None},
            {"$set": {"escalated_at": escalated_at}},
        )
        if result.modified_count != 1:
            continue  # another evaluation set it first — idempotent no-op
        escalated_ids.append(claim_id)
        logger.info(
            "claim_escalated",
            claim_id=claim_id,
            escalated_at=escalated_at,
            factor=settings.sla_escalation_factor,
        )
        await notify_assignee_of_escalation(claim)
    return escalated_ids
