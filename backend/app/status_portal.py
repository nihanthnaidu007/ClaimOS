"""Public claim-status portal: access-code model and masked payload building.

Access model (spec security section): a claim number plus a high-entropy access
code is the only credential for the public status page. The code is generated
at submission, stored on the claim document, and *hashed* for lookup — the
public endpoint matches on the SHA-256 hash, so a database dump cannot be
replayed against the portal and the lookup never handles the plaintext for
comparison. Wrong claim number and wrong code return the identical generic 404:
the portal must not confirm that a claim number exists.
"""

import math
import secrets
from datetime import datetime

from app.security import sha256_hex

# workbench is the SLA authority: the portal reuses its target/state math and
# severity derivation so the customer's ETA and the adjuster queue can never
# disagree. The projection stays LLM-free — importing agents/pipeline here
# would drag the model stack into the API boot path for copy it must not read.
from app.workbench import _parse_timestamp, severity_for_claim, sla_state

# ~192 bits of entropy — not brute-forceable and not guessable from the claim
# number, which is the enumeration defense the spec asks for.
ACCESS_CODE_BYTES = 24

# The public payload's PII budget: first name + claim status. Everything else
# (full name, contact details, policy number, amounts) stays behind the
# authenticated adjuster surface.
_PUBLIC_STATUS_DETAIL = {"detail": "No claim found for that claim number and access code"}

_STAGE_LABELS = {
    "INTAKE_AGENT": "Intake & Validation",
    "POLICY_AGENT": "Policy Verification",
    "DOCUMENT_AGENT": "Document Analysis",
    "ELIGIBILITY_AGENT": "Eligibility & Risk",
    "DECISION_AGENT": "Decision & Communication",
}

_STATUS_LABELS = {
    "pending": "In progress",
    "auto_approved": "Approved",
    "approved": "Approved",
    "escalated": "In review",
    "under_review": "In review",
    "rejected": "Rejected",
    "failed": "Processing error",
}

# Pipeline order for the customer's "what happens next" list. Mirrors
# PIPELINE_STAGES in agents.py — test_status_portal asserts the two agree, so
# a stage added there without copy here fails loudly instead of silently
# shrinking the customer's card.
_PORTAL_STAGE_ORDER = (
    "INTAKE_AGENT",
    "POLICY_AGENT",
    "DOCUMENT_AGENT",
    "FRAUD_AGENT",
    "ELIGIBILITY_AGENT",
    "DECISION_AGENT",
)

# The customer-facing stage copy (F2): one entry per pipeline stage plus the
# states a claim can settle in. Plain language on purpose — the UX quality bar
# bans insurance jargon on the portal, so stages read as human actions ("we
# check your policy"), never agent names. This constant is the ONLY source of
# that copy: endpoints and components must not hardcode their own.
PORTAL_STAGE_COPY = {
    "INTAKE_AGENT": "We review your claim details and make sure we have everything we need.",
    "POLICY_AGENT": "We check your policy to confirm what's covered.",
    "DOCUMENT_AGENT": "We review the photos, estimates, and documents you submitted.",
    "FRAUD_AGENT": "We run routine consistency checks that help keep things fair for everyone.",
    "ELIGIBILITY_AGENT": "We make a final check of everything against your policy.",
    "DECISION_AGENT": "We prepare your decision and let you know as soon as it's ready.",
    "decided": "A decision has been made on your claim. You can read the outcome and download your decision letter below.",
    "reopened": "We've reopened your claim and our team is taking another look. We'll keep you posted here.",
    "failed": "Something went wrong on our end while processing your claim. We're fixing it and will update you here.",
}

# Statuses that mean the claim has settled out of the pipeline even if the
# decision trace were somehow missing — the customer sees the decided copy.
_DECIDED_STATUSES = frozenset({
    "auto_approved", "approved", "rejected", "escalated", "overridden", "settled",
})

# Statuses where nothing is pending, so no ETA is honest.
_TERMINAL_STATUSES = frozenset({"reopened", "failed"}) | _DECIDED_STATUSES


def generate_access_code() -> str:
    """High-entropy URL-safe access code shown to the submitting customer."""
    return secrets.token_urlsafe(ACCESS_CODE_BYTES)


def access_code_hash(code: str) -> str:
    """Lookup key stored beside the claim; the plaintext never lives in a query."""
    return sha256_hex(code)


def status_not_found() -> dict:
    """The one generic 404 body every failed portal lookup returns."""
    return dict(_PUBLIC_STATUS_DETAIL)


def first_name(full_name: str) -> str:
    """First token of the holder name — the only personal data the portal shows."""
    return (full_name or "").strip().split(" ")[0] if (full_name or "").strip() else ""


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status or "", "In progress")


def next_steps(claim: dict, current_agent: str | None) -> list[str]:
    """Plain-language "what happens next" — copy from PORTAL_STAGE_COPY only.

    In-flight claims list the remaining pipeline stages from the one now
    running; terminal states get their own single line. Agent output text is
    never copied here: the projection summarizes the pipeline, it does not
    quote it.
    """
    status = claim.get("status") or "pending"
    if status == "reopened":
        return [PORTAL_STAGE_COPY["reopened"]]
    if status == "failed":
        return [PORTAL_STAGE_COPY["failed"]]
    if status in _DECIDED_STATUSES or (claim.get("agent_trace") or {}).get("decision"):
        return [PORTAL_STAGE_COPY["decided"]]
    # In flight: from the current stage onward. A claim whose events haven't
    # started yet (or whose current agent isn't a known stage) shows the run.
    start = 0
    if current_agent in _PORTAL_STAGE_ORDER:
        start = _PORTAL_STAGE_ORDER.index(current_agent)
    return [PORTAL_STAGE_COPY[stage] for stage in _PORTAL_STAGE_ORDER[start:]]


def expected_resolution(claim: dict, *, now: datetime | None = None) -> str | None:
    """Honest ETA as a human phrase, or None — never a promised date.

    Derives from the same SLA state the adjuster queue ages against. Absent
    when the SLA clock can't be computed (no or unparseable created_at) or
    when nothing is pending (decided, failed, reopened): the caller omits the
    field entirely rather than render an empty string or a fake date.
    """
    status = claim.get("status") or "pending"
    if status in _TERMINAL_STATUSES or (claim.get("agent_trace") or {}).get("decision"):
        return None
    if _parse_timestamp(claim.get("created_at")) is None:
        return None
    state = sla_state(claim.get("created_at"), severity_for_claim(claim), now=now)
    if state["breached"]:
        return (
            "This claim is taking longer than we usually aim for. "
            "We're still working on it and will keep you posted here."
        )
    days = max(1, math.ceil(state["hoursRemaining"] / 24))
    day_word = "day" if days == 1 else "days"
    return (
        f"We aim to reach a decision within about {days} business {day_word} "
        "of receiving your claim."
    )


def milestone_timeline(events: list[dict]) -> tuple[list[dict], str | None]:
    """Product milestones and the current stage agent, derived from events.

    The events collection is the timeline source (not the claim row): the
    worker rewrites the claim document only at run end, while events land the
    moment each milestone happens — so the public page stays live mid-run.

    Returns (milestones, current_agent). Milestones always carry all four
    product slots so the timeline renders future stages as pending. The
    current agent is the raw stage name (e.g. "POLICY_AGENT") — callers map it
    to a display label, which keeps FRAUD_AGENT (deliberately unlabeled for
    customers) behaving exactly as before.
    """
    done_at: dict[str, str] = {}
    started: list[tuple[str, str]] = []  # (agent, timestamp) in start order
    completed: set[str] = set()
    submitted_at: str | None = None
    payout_at: str | None = None

    for event in sorted(events, key=lambda e: (e.get("seq", 0), e.get("created_at", ""))):
        event_type = event.get("event")
        data = event.get("data") or {}
        created = event.get("created_at") or ""
        if event_type == "claim_submitted":
            submitted_at = submitted_at or created
        elif event_type == "agent_start":
            started.append((data.get("agent", ""), created))
        elif event_type == "agent_complete":
            agent = data.get("agent", "")
            completed.add(agent)
            if agent == "DOCUMENT_AGENT":
                done_at.setdefault("documents_received", created)
            elif agent == "DECISION_AGENT":
                done_at.setdefault("decision_ready", created)
        elif event_type == "payout_recorded":
            payout_at = payout_at or created

    current_agent = None
    for agent, _ in reversed(started):
        if agent not in completed:
            current_agent = agent
            break

    milestones = [
        {"key": "submitted", "label": "Claim submitted",
         "at": submitted_at, "done": submitted_at is not None},
        {"key": "documents_received", "label": "Documents received",
         "at": done_at.get("documents_received"), "done": "documents_received" in done_at},
        {"key": "decision_ready", "label": "Decision ready",
         "at": done_at.get("decision_ready"), "done": "decision_ready" in done_at},
        {"key": "payout_recorded", "label": "Payout recorded",
         "at": payout_at, "done": payout_at is not None},
    ]
    return milestones, current_agent


def public_status_payload(claim: dict, events: list[dict], *, now: datetime | None = None) -> dict:
    """Masked, portal-safe view of one claim. Pure — unit-testable.

    Identity data is reduced to the holder's first name; the decision verdict
    is the customer's own outcome and is included, while payout amounts,
    contact details, and the policy number never appear. The payload is built
    field by field (deny by default): internal trace data has no path in.
    """
    milestones, current_agent = milestone_timeline(events)
    decision = (claim.get("agent_trace") or {}).get("decision") or {}
    status = claim.get("status") or "pending"
    payload = {
        "claimNumber": claim.get("id", ""),
        "firstName": first_name(claim.get("holder_name", "")),
        "status": status,
        "statusLabel": status_label(status),
        "currentStage": _STAGE_LABELS.get(current_agent),
        "incidentType": claim.get("incident_type", ""),
        "decisionOutcome": decision.get("verdict"),
        "decisionReady": bool(decision),
        "pdfAvailable": bool(decision),
        "milestones": milestones,
        # F2: pre-written stage copy + an ETA derived from existing SLA state.
        "nextSteps": next_steps(claim, current_agent),
    }
    eta = expected_resolution(claim, now=now)
    if eta is not None:
        # Absent means absent: the key is omitted, never an empty string.
        payload["expectedResolution"] = eta
    return payload
