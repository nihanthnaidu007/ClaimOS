"""F6 decision transparency: deny-by-default customer projection over traces.

The customer status portal must explain HOW a decision was reached without
exposing anything internal. The keystone is ``CUSTOMER_VISIBLE_TRACE_FIELDS``:
a projection built ONLY from allowlisted trace fields — any field a future
feature adds to an internal trace is invisible by default. Porting new data to
customers is a deliberate act: add the field to the allowlist AND to this
module's explicit reads AND to the regression test.

Everything customer-facing here is pre-written copy (``PORTAL_STAGE_COPY``) or
the decision output's own ``summary``/``citations`` (which the decision agent
already writes for the customer letter). Fraud signals, similarity matches,
risk scores, thresholds, policy math, model metadata, and F11 notes have NO
path into a projection.
"""

from pydantic import BaseModel

# The ONLY trace fields that may cross the projection boundary. Everything
# else — known today or added tomorrow — is denied by default.
CUSTOMER_VISIBLE_TRACE_FIELDS = frozenset(
    {
        "stage",  # stage key (intake, policy, documents, fraud, eligibility, decision)
        "title",  # display title (resolved from PORTAL_STAGE_COPY)
        "summary",  # stage summary (resolved from PORTAL_STAGE_COPY pre-written copy)
        "status",  # completed | failed | skipped (derived from agent logs)
        "citations",  # customer-safe citations, sanitized to the three wire keys
        "decided_at",  # decision-stage completion timestamp (from agent logs)
    }
)


# Pipeline order is the display order: trace key -> agent log name.
# Explicit because the trace keys are not derivable from agent names
# (DOCUMENT_AGENT stores its output under "documents").
_STAGE_AGENT_BY_KEY = {
    "intake": "INTAKE_AGENT",
    "policy": "POLICY_AGENT",
    "documents": "DOCUMENT_AGENT",
    "fraud": "FRAUD_AGENT",
    "eligibility": "ELIGIBILITY_AGENT",
    "decision": "DECISION_AGENT",
}

# Editable customer copy per stage — the projection never renders agent
# summaries for stages, so this is the single place to tune the wording.
# Each entry serves two surfaces: `title`/`summary` feed the decision-
# transparency projection (F6) and `nextStep` feeds the portal's "what
# happens next" list (F2). One constant, two consumers — next-step copy and
# stage summaries must never drift apart.
PORTAL_STAGE_COPY = {
    "INTAKE_AGENT": {
        "title": "Reviewing your claim",
        "summary": "We received your claim and checked that all the required details were included.",
        "nextStep": "We review your claim details and make sure we have everything we need.",
    },
    "POLICY_AGENT": {
        "title": "Verifying your coverage",
        "summary": "We confirmed your policy was active and the incident is the kind it covers.",
        "nextStep": "We check your policy to confirm what's covered.",
    },
    "DOCUMENT_AGENT": {
        "title": "Reviewing your documents",
        "summary": "We reviewed the documents you provided and checked them for consistency.",
        "nextStep": "We review the photos, estimates, and documents you submitted.",
    },
    "FRAUD_AGENT": {
        "title": "Running standard checks",
        "summary": "We completed the routine verification checks that are part of every claim.",
        "nextStep": "We run routine consistency checks that help keep things fair for everyone.",
    },
    "ELIGIBILITY_AGENT": {
        "title": "Checking eligibility",
        "summary": "We reviewed how your claim fits the standard approval guidelines.",
        "nextStep": "We make a final check of everything against your policy.",
    },
    "DECISION_AGENT": {
        "title": "Making the decision",
        "summary": "We brought everything together and made the final decision on your claim.",
        "nextStep": "We prepare your decision and let you know as soon as it's ready.",
    },
    # Portal states outside the six pipeline stages.
    "decided": {
        "title": "Decision ready",
        "summary": "A decision has been made on your claim — see the summary below.",
        "nextStep": "A decision has been made on your claim. You can read the outcome and download your decision letter below.",
    },
    "reopened": {
        "title": "Claim reopened",
        "summary": "Your claim has been reopened and is being reviewed again.",
        "nextStep": "We've reopened your claim and our team is taking another look. We'll keep you posted here.",
    },
    "failed": {
        "title": "Processing issue",
        "summary": "Something went wrong on our end while processing your claim.",
        "nextStep": "Something went wrong on our end while processing your claim. We're fixing it and will update you here.",
    },
}

# agent_logs status -> customer-safe status (verbs a customer understands).
_STAGE_STATUS_BY_LOG = {
    "done": "completed",
    "error": "failed",
}


class CustomerCitation(BaseModel):
    """A customer-safe citation — exactly three wire fields, nothing else."""

    fact: str
    sourceRef: str
    customerFriendlyExplanation: str


class CustomerStageSummary(BaseModel):
    """One pipeline stage as the customer sees it."""

    stage: str
    title: str
    summary: str
    status: str
    citations: list[CustomerCitation] = []


class CustomerDecisionSummary(BaseModel):
    """The final decision in plain language — the decision output's own
    customer-facing summary and citations, no verdict metadata."""

    summary: str
    citations: list[CustomerCitation] = []
    decidedAt: str | None = None


class CustomerClaimProjection(BaseModel):
    """Everything a customer endpoint may say about one claim's adjudication."""

    claim_number: str
    status: str
    current_stage: str | None = None
    decision: CustomerDecisionSummary | None = None
    stage_summaries: list[CustomerStageSummary] = []


def _safe_text(value: object) -> str:
    """Only a real string passes; anything else is empty (never stringified)."""
    return value if isinstance(value, str) else ""


def _customer_citations(raw: object) -> list[CustomerCitation]:
    """Build customer citations from an allowlisted ``citations`` trace field.

    Only the three customer-safe keys survive; malformed entries are dropped
    rather than passed through.
    """
    if not isinstance(raw, list):
        return []
    citations = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        citations.append(
            CustomerCitation(
                fact=_safe_text(entry.get("fact")),
                sourceRef=_safe_text(entry.get("sourceRef")),
                customerFriendlyExplanation=_safe_text(
                    entry.get("customerFriendlyExplanation")
                ),
            )
        )
    return citations


def _stage_logs(claim: dict) -> dict[str, dict]:
    """Latest log entry per agent, keyed by agent name."""
    logs: dict[str, dict] = {}
    for entry in claim.get("agent_logs") or []:
        if isinstance(entry, dict) and entry.get("agent"):
            logs[entry["agent"]] = entry
    return logs


def _current_stage(logs: dict[str, dict]) -> str | None:
    """Customer copy for the stage currently running, if any."""
    for log in logs.values():
        if log.get("status") != "running":
            continue
        agent = log.get("agent", "")
        if agent in PORTAL_STAGE_COPY:
            return PORTAL_STAGE_COPY[agent]["title"]
    return None


def build_customer_projection(claim: dict, trace: dict) -> CustomerClaimProjection:
    """Project a claim into its customer-visible form.

    ``trace`` is the stored ``agent_trace``. The projection copies ONLY
    allowlisted fields (``CUSTOMER_VISIBLE_TRACE_FIELDS``) and resolves them
    through fixed models — no raw trace dict ever reaches the output.
    """
    logs = _stage_logs(claim)
    stage_summaries = []
    for stage_key, agent in _STAGE_AGENT_BY_KEY.items():
        stage_output = trace.get(stage_key)
        log = logs.get(agent)
        log_status = log.get("status") if log else None
        if not stage_output and log_status not in _STAGE_STATUS_BY_LOG:
            # Nothing ran here yet (or the stage is mid-flight) — say nothing.
            continue
        copy = PORTAL_STAGE_COPY[agent]
        visible = {
            key: stage_output.get(key)
            for key in CUSTOMER_VISIBLE_TRACE_FIELDS
            if stage_output and key in stage_output
        }
        stage_summaries.append(
            CustomerStageSummary(
                stage=stage_key,
                title=copy["title"],
                summary=copy["summary"],
                status=_STAGE_STATUS_BY_LOG.get(log_status or "done", "skipped"),
                # Citations flow only where an allowlisted citations field
                # exists — for non-decision stages this is [].
                citations=(
                    _customer_citations(visible.get("citations")) if stage_key == "decision" else []
                ),
            )
        )

    decision = None
    decision_output = trace.get("decision")
    if decision_output:
        visible = {
            key: decision_output[key]
            for key in CUSTOMER_VISIBLE_TRACE_FIELDS
            if key in decision_output
        }
        decided_at = (logs.get("DECISION_AGENT") or {}).get("endTime")
        decision = CustomerDecisionSummary(
            summary=_safe_text(visible.get("summary")),
            citations=_customer_citations(visible.get("citations")),
            decidedAt=_safe_text(decided_at) or None,
        )

    return CustomerClaimProjection(
        claim_number=str(claim.get("id", "") or ""),
        status=str(claim.get("status", "") or ""),
        current_stage=_current_stage(logs),
        decision=decision,
        stage_summaries=stage_summaries,
    )
