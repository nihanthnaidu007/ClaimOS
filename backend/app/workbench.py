"""Adjuster workbench logic — pure functions over stored claim data.

Queue rows, SLA aging, and the case summary are all assembled deterministically
from the persisted claim document (agent traces, agent logs, run state). No LLM
call happens anywhere in this module: the workbench is a read-only lens over
evidence the pipeline already saved (spec glass-box rule), plus the override
action's own writes.
"""

from datetime import datetime, timezone

from app.config import settings
from app.stp import ELEVATED, LOW, assess_claim_severity

# Statuses a human still has to act on — the workbench queue's default scope.
REVIEWABLE_STATUSES = frozenset({"escalated", "pending", "under_review"})

# Once a claim carries one of these statuses a decision is on record;
# overriding it again is a re-open flow, not a queue action.
DECIDED_STATUSES = frozenset(
    {"auto_approved", "approved", "rejected", "overridden", "settled", "failed"}
)

SLA_OK = "ok"
SLA_AT_RISK = "at_risk"
SLA_BREACHED = "breached"


def parse_sla_hours(raw: str) -> dict[str, float]:
    """Parse the SLA_HOURS_PER_SEVERITY env format "low:72,elevated:24".

    Invalid pairs raise ValueError so a typo'd env var fails loudly at
    configuration time instead of silently defaulting every severity.
    """
    mapping: dict[str, float] = {}
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        severity, _, hours = entry.partition(":")
        if not hours:
            raise ValueError(f"SLA_HOURS_PER_SEVERITY entry needs 'severity:hours': {entry!r}")
        mapping[severity.strip().lower()] = float(hours)
    return mapping


def sla_target_hours(severity: str) -> float:
    """SLA hours for a severity from settings; unknown severities get the default."""
    return parse_sla_hours(settings.sla_hours_per_severity).get(
        severity.strip().lower(), settings.sla_default_hours
    )


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    """Parse a stored ISO timestamp; naive values are treated as UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sla_state(
    created_at: str | datetime | None,
    severity: str,
    *,
    now: datetime | None = None,
) -> dict:
    """SLA aging for one claim, computed from data (never a constant).

    Returns {targetHours, hoursElapsed, hoursRemaining, breached, state}:
    breached when the elapsed age passes the severity's SLA target (red),
    at_risk past SLA_AT_RISK_FRACTION of the target (amber), ok otherwise.
    """
    target_hours = sla_target_hours(severity)
    created = _parse_timestamp(created_at)
    now = now or datetime.now(timezone.utc)
    if created is None or created > now:
        # Unparseable or future-dated rows cannot age honestly; treat as fresh.
        return {
            "targetHours": target_hours,
            "hoursElapsed": 0.0,
            "hoursRemaining": round(target_hours, 2),
            "breached": False,
            "state": SLA_OK,
        }
    hours_elapsed = (now - created).total_seconds() / 3600.0
    remaining = target_hours - hours_elapsed
    breached = remaining <= 0
    if breached:
        state = SLA_BREACHED
    elif hours_elapsed >= settings.sla_at_risk_fraction * target_hours:
        state = SLA_AT_RISK
    else:
        state = SLA_OK
    return {
        "targetHours": target_hours,
        "hoursElapsed": round(hours_elapsed, 2),
        "hoursRemaining": round(remaining, 2),
        "breached": breached,
        "state": state,
    }


def severity_for_claim(claim: dict) -> str:
    """Severity for a stored claim: the run's STP verdict, else re-derived.

    The pipeline persists the derived severity on the run document (stp.severity);
    rows that predate a decision (pending/in-flight) or historical seeds have
    none, so severity is re-derived deterministically from the intake's
    normalized data — the same function the gate uses, never the LLM.
    """
    stp = claim.get("stp") or {}
    if stp.get("severity") in (LOW, ELEVATED):
        return str(stp["severity"])
    intake = (claim.get("agent_trace") or {}).get("intake") or {}
    normalized = intake.get("normalizedData") or {}
    return assess_claim_severity(
        claimed_amount=float(claim.get("claimed_amount", 0) or 0),
        incident_type=str(normalized.get("incidentType", "") or claim.get("incident_type", "")),
        low_amount_threshold=settings.stp_low_severity_amount,
        low_types={
            part.strip().lower()
            for part in settings.stp_low_severity_types.split(",")
            if part.strip()
        },
    )


QUEUE_ROW_FIELDS = (
    "id",
    "policy_number",
    "holder_name",
    "incident_type",
    "claimed_amount",
    "status",
    "risk_score",
    "created_at",
    "escalation_reason",
    "failure_reason",
)

# Seeded or legacy claim docs may be missing fields the row renders; coerce
# instead of leaking None into the response model.
_STRING_FIELDS = frozenset(
    {"id", "policy_number", "holder_name", "incident_type", "status", "created_at"}
)
_NUMBER_FIELDS = frozenset({"claimed_amount", "risk_score"})


def queue_row(claim: dict, *, now: datetime | None = None) -> dict:
    """One queue row: the claim fields the list renders plus severity and SLA."""
    severity = severity_for_claim(claim)
    row = {"id": ""}
    for field in QUEUE_ROW_FIELDS:
        value = claim.get(field)
        if value is None:
            value = 0.0 if field in _NUMBER_FIELDS else ""
        row[field] = value
    row["severity"] = severity
    row["sla"] = sla_state(claim.get("created_at"), severity, now=now)
    return row


def _confidence(trace: dict) -> float | None:
    eligibility = trace.get("eligibility") or {}
    decision = trace.get("decision") or {}
    for value in (decision.get("confidence"), eligibility.get("confidence")):
        if isinstance(value, (int, float)):
            return round(float(value), 2)
    return None


STAGE_LABELS = {
    "intake": "Intake",
    "policy": "Policy verification",
    "documents": "Document analysis",
    "eligibility": "Eligibility & risk",
    "decision": "Decision",
}


def build_case_summary(claim: dict) -> dict:
    """Deterministic case summary assembled from the stored agent traces.

    No extra LLM call — this reshapes evidence the pipeline already persisted
    (agent_trace per stage, agent_logs timings, escalation/failure reasons) into
    the shape the case view renders. Absent stages render as "not reached".
    """
    trace = claim.get("agent_trace") or {}
    logs = claim.get("agent_logs") or []
    log_by_agent = {}
    for entry in logs:
        log_by_agent.setdefault(entry.get("agent"), entry)

    stages = []
    for key, label in STAGE_LABELS.items():
        stage_trace = trace.get(key) or {}
        log = log_by_agent.get(key) or {}
        stages.append(
            {
                "agent": key,
                "label": label,
                "reached": bool(stage_trace) or bool(log),
                "status": log.get("status") or ("complete" if stage_trace else "pending"),
                "durationMs": log.get("durationMs"),
                "reasoning": stage_trace.get("reasoning"),
            }
        )

    decision = trace.get("decision") or {}
    eligibility = trace.get("eligibility") or {}
    documents = trace.get("documents") or {}
    policy = trace.get("policy") or {}
    intake = trace.get("intake") or {}
    confidence = _confidence(trace)
    severity = severity_for_claim(claim)

    return {
        "claimId": claim.get("id", ""),
        "holderName": claim.get("holder_name", ""),
        "policyNumber": claim.get("policy_number", ""),
        "incidentType": claim.get("incident_type", ""),
        "incidentDate": claim.get("incident_date", ""),
        "claimedAmount": claim.get("claimed_amount", 0.0),
        "status": claim.get("status", ""),
        "severity": severity,
        "riskScore": claim.get("risk_score", 0.0),
        "recommendation": eligibility.get("recommendation") or None,
        "confidence": confidence,
        "eligibility": {
            "eligible": eligibility.get("eligible"),
            "riskFactors": eligibility.get("riskFactors") or [],
            "fraudIndicators": eligibility.get("fraudIndicators") or [],
        },
        "coverage": {
            "found": policy.get("found"),
            "statusCheck": policy.get("statusCheck") or None,
            "withinLimits": policy.get("withinLimits"),
            "adjustedPayout": policy.get("adjustedPayout"),
        },
        "documents": {
            "consistencyScore": documents.get("consistencyScore"),
            "redFlags": documents.get("redFlags") or [],
        },
        "decision": {
            "verdict": decision.get("verdict") or None,
            "payoutAmount": decision.get("payoutAmount"),
            "letterSubject": decision.get("letterSubject") or None,
            "hasLetterBody": bool(decision.get("letterBody")),
        },
        "intakeValid": intake.get("valid"),
        "stages": stages,
        "sla": sla_state(claim.get("created_at"), severity),
        "escalationReason": claim.get("escalation_reason") or None,
        "failureReason": claim.get("failure_reason") or None,
        "override": claim.get("override") or None,
        "source": "stored agent traces",
    }
