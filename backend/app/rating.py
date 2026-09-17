"""Deterministic coverage math and risk scoring.

Extracted verbatim from the eligibility and policy agent prompts
(agents.py:231-245): in a money domain the arithmetic must be reproducible and
unit-testable. LLM outputs feed these functions as inputs — the LLM never
performs the arithmetic.
"""

from dataclasses import dataclass, field

# Routing recommendation constants (mirror the eligibility prompt's vocabulary).
AUTO_APPROVE = "auto_approve"
ESCALATE = "escalate"
AUTO_REJECT = "auto_reject"

# Prompt formula: additive points, capped at 100.
APPLIED_EXPIRED_OR_SUSPENDED = 40
APPLIED_NOT_COVERED = 35
APPLIED_OVER_LIMIT = 25
APPLIED_CLAIM_FREQUENCY = 25
APPLIED_LOW_CONSISTENCY = 20  # document analysis contradicts the claim
APPLIED_BORDERLINE_CONSISTENCY = 10  # document analysis partially consistent
APPLIED_PER_RED_FLAG = 8
RED_FLAG_POINTS_CAP = 24  # 3 red flags max
BASE_SCORE_MIN = 5
BASE_SCORE_MAX = 15
MAX_RISK_SCORE = 100

_REJECTING_POLICY_STATUSES = {"expired", "suspended"}
_ACTIVE_POLICY_STATUSES = {"active"}

# Document consistency categories (DocumentAnalysis.consistency). The model
# outputs a category, never a number (review §4.3); these mappings live in code.
CONSISTENT = "consistent"
PARTIALLY_CONSISTENT = "partially_consistent"
CONTRADICTS = "contradicts"
NO_DOCUMENTS = "no_documents"

# Numeric UI/evidence-pack score derived per category — chosen so the old
# eligibility bands still hold: 20 -> +20 points, 60 -> +10 points, 90 -> +0.
CONSISTENCY_SCORE_BY_CATEGORY = {
    CONTRADICTS: 20,
    PARTIALLY_CONSISTENT: 60,
    CONSISTENT: 90,
}


def consistency_score_for(category: str | None) -> int | None:
    """Numeric score for the stored document output, derived in code.

    None (no_documents or an unrecognized category) means "no document
    analysis signal" — downstream scoring skips document evidence.
    """
    return CONSISTENCY_SCORE_BY_CATEGORY.get(category) if category else None


@dataclass
class AdjustedPayout:
    within_limits: bool
    adjusted_payout: float
    deductible_applied: float


def calculate_adjusted_payout(
    claimed_amount: float, coverage_limit: float, deductible: float
) -> AdjustedPayout:
    """Policy-agent payout math.

    Within limits when claimed <= coverage_limit - deductible; the payout is
    claimed - deductible when within, coverage_limit - deductible when over,
    never negative.
    """
    within = claimed_amount <= (coverage_limit - deductible)
    payout = (claimed_amount - deductible) if within else (coverage_limit - deductible)
    return AdjustedPayout(
        within_limits=within,
        adjusted_payout=max(payout, 0.0),
        deductible_applied=deductible,
    )


def base_score_for_claim_size(claimed_amount: float, coverage_limit: float) -> int:
    """Clean-claim base: 5 for a tiny claim scaling linearly to 15 at/above the limit."""
    if coverage_limit <= 0:
        return BASE_SCORE_MAX
    ratio = claimed_amount / coverage_limit
    clamped = min(max(ratio, 0.0), 1.0)
    return BASE_SCORE_MIN + int(clamped * (BASE_SCORE_MAX - BASE_SCORE_MIN))


@dataclass
class RiskAssessment:
    risk_score: int
    recommendation: str
    eligible: bool
    risk_factors: list[str] = field(default_factory=list)


def compute_risk_assessment(
    *,
    policy_status: str,
    incident_type: str,
    covered_events: list[str],
    claimed_amount: float,
    coverage_limit: float,
    deductible: float,
    claim_frequency_flag: bool,
    consistency: str | None,
    red_flag_count: int,
) -> RiskAssessment:
    """Additive risk formula + routing from the eligibility prompt.

    consistency is the Document agent's category: CONTRADICTS adds 20 points,
    PARTIALLY_CONSISTENT adds 10, and CONSISTENT / NO_DOCUMENTS / None (no
    document analysis signal) carry no consistency penalty. An uncovered
    incident or a non-active policy forces auto_reject regardless of the score.
    """
    factors: list[str] = []
    score = base_score_for_claim_size(claimed_amount, coverage_limit)

    if policy_status in _REJECTING_POLICY_STATUSES:
        score += APPLIED_EXPIRED_OR_SUSPENDED
        factors.append(f"Policy status is {policy_status}")

    is_covered = incident_type in (covered_events or [])
    if not is_covered:
        score += APPLIED_NOT_COVERED
        factors.append(f"Incident type '{incident_type}' is not in covered events")

    payout = calculate_adjusted_payout(claimed_amount, coverage_limit, deductible)
    if not payout.within_limits:
        score += APPLIED_OVER_LIMIT
        factors.append("Claimed amount exceeds available coverage")

    if claim_frequency_flag:
        score += APPLIED_CLAIM_FREQUENCY
        factors.append("3+ claims on this policy in the past 12 months")

    if consistency == CONTRADICTS:
        score += APPLIED_LOW_CONSISTENCY
        factors.append("Document analysis contradicts the claim")
    elif consistency == PARTIALLY_CONSISTENT:
        score += APPLIED_BORDERLINE_CONSISTENCY
        factors.append("Document analysis only partially consistent")

    if red_flag_count > 0:
        capped_points = min(red_flag_count, RED_FLAG_POINTS_CAP // APPLIED_PER_RED_FLAG)
        score += capped_points * APPLIED_PER_RED_FLAG
        factors.append(f"{red_flag_count} document red flag(s)")

    score = min(score, MAX_RISK_SCORE)

    if not is_covered or policy_status in _REJECTING_POLICY_STATUSES:
        recommendation = AUTO_REJECT
    elif score >= 70:
        recommendation = AUTO_REJECT
    elif score >= 30:
        recommendation = ESCALATE
    else:
        recommendation = AUTO_APPROVE

    eligible = policy_status in _ACTIVE_POLICY_STATUSES and is_covered and payout.within_limits

    return RiskAssessment(
        risk_score=score, recommendation=recommendation, eligible=eligible, risk_factors=factors
    )
