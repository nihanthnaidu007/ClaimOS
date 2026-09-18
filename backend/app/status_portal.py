"""Public claim-status portal: access-code model and masked payload building.

Access model (spec security section): a claim number plus a high-entropy access
code is the only credential for the public status page. The code is generated
at submission, stored on the claim document, and *hashed* for lookup — the
public endpoint matches on the SHA-256 hash, so a database dump cannot be
replayed against the portal and the lookup never handles the plaintext for
comparison. Wrong claim number and wrong code return the identical generic 404:
the portal must not confirm that a claim number exists.
"""

import secrets

from app.security import sha256_hex

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
    "FRAUD_AGENT": "Fraud Cross-Check",
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


def milestone_timeline(events: list[dict]) -> tuple[list[dict], str | None]:
    """Product milestones and the current stage, derived from claim events.

    The events collection is the timeline source (not the claim row): the
    worker rewrites the claim document only at run end, while events land the
    moment each milestone happens — so the public page stays live mid-run.

    Returns (milestones, current_stage_label). Milestones always carry all four
    product slots so the timeline renders future stages as pending.
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

    current_stage = None
    for agent, _ in reversed(started):
        if agent not in completed and agent in _STAGE_LABELS:
            current_stage = _STAGE_LABELS[agent]
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
    return milestones, current_stage


def public_status_payload(claim: dict, events: list[dict]) -> dict:
    """Masked, portal-safe view of one claim. Pure — unit-testable.

    Identity data is reduced to the holder's first name; the decision verdict
    is the customer's own outcome and is included, while payout amounts,
    contact details, and the policy number never appear.
    """
    milestones, current_stage = milestone_timeline(events)
    decision = (claim.get("agent_trace") or {}).get("decision") or {}
    status = claim.get("status") or "pending"
    return {
        "claimNumber": claim.get("id", ""),
        "firstName": first_name(claim.get("holder_name", "")),
        "status": status,
        "statusLabel": status_label(status),
        "currentStage": current_stage,
        "incidentType": claim.get("incident_type", ""),
        "decisionOutcome": decision.get("verdict"),
        "decisionReady": bool(decision),
        "pdfAvailable": bool(decision),
        "milestones": milestones,
    }
