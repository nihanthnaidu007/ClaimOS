"""Straight-through-processing gate (production-upgrade spec AC-5).

A claim finalizes automatically only when ALL gate legs hold: the Decision
agent's self-reported confidence clears the configured threshold, the claim's
severity is low, and eligibility came back clean. Every failing leg is
collected so the escalation reason names exactly why a human must review it.

Severity is derived deterministically here — never by the LLM — from the
normalized claim data: a low-severity incident type at or under the configured
amount threshold. Everything else is treated as elevated and routed to a human.
"""

from dataclasses import dataclass, field

from app.rating import AUTO_APPROVE

LOW = "low"
ELEVATED = "elevated"


def assess_claim_severity(
    *,
    claimed_amount: float,
    incident_type: str,
    low_amount_threshold: float,
    low_types: set[str],
) -> str:
    """Binary severity for the gate: LOW, or ELEVATED for everything else."""
    normalized = (incident_type or "").strip().lower()
    if normalized in low_types and claimed_amount <= low_amount_threshold:
        return LOW
    return ELEVATED


def eligibility_is_clean(eligibility: dict) -> bool:
    """Clean = eligible, recommended for auto-approval, no fraud indicators."""
    return bool(
        eligibility.get("eligible")
        and eligibility.get("recommendation") == AUTO_APPROVE
        and not (eligibility.get("fraudIndicators") or [])
    )


@dataclass
class STPDecision:
    """Gate outcome: finalize, or the enumerated legs that blocked it."""

    auto_finalize: bool
    failed_legs: list[str] = field(default_factory=list)

    def escalation_reason(self) -> str:
        return "; ".join(self.failed_legs)


def evaluate_stp_gate(
    *,
    confidence: float,
    severity: str,
    eligibility: dict,
    threshold: float,
) -> STPDecision:
    """All legs must hold for straight-through finalization; else escalate."""
    failed: list[str] = []
    if confidence < threshold:
        failed.append(f"decision confidence {confidence:.2f} below threshold {threshold:.2f}")
    if severity != LOW:
        failed.append(f"claim severity is {severity}")
    if not eligibility_is_clean(eligibility):
        failed.append("eligibility is not clean")
    return STPDecision(auto_finalize=not failed, failed_legs=failed)
