"""Golden-value tests for the deterministic rating functions.

The audit's AC-4 requires the money math to be reproducible: these tests pin
the exact formula so a prompt tweak can never silently change a payout or a
risk score.
"""

from datetime import datetime, timedelta, timezone

from agents import _claim_frequency_flag
from app.rating import (
    AUTO_APPROVE,
    AUTO_REJECT,
    CONTRADICTS,
    CONSISTENT,
    ESCALATE,
    NO_DOCUMENTS,
    PARTIALLY_CONSISTENT,
    base_score_for_claim_size,
    calculate_adjusted_payout,
    compute_risk_assessment,
    consistency_score_for,
)

BASE = dict(
    policy_status="active",
    incident_type="accident",
    covered_events=["accident", "theft"],
    claimed_amount=1000.0,
    coverage_limit=50000.0,
    deductible=500.0,
    claim_frequency_flag=False,
    consistency=None,
    red_flag_count=0,
)


def assessment(**overrides):
    return compute_risk_assessment(**{**BASE, **overrides})


def test_payout_within_limits():
    result = calculate_adjusted_payout(1000, 50000, 500)
    assert result.within_limits is True
    assert result.adjusted_payout == 500.0
    assert result.deductible_applied == 500.0


def test_payout_at_boundary_counts_as_within():
    result = calculate_adjusted_payout(49500, 50000, 500)
    assert result.within_limits is True
    assert result.adjusted_payout == 49000.0


def test_payout_over_limit_caps_at_coverage_minus_deductible():
    result = calculate_adjusted_payout(60000, 50000, 500)
    assert result.within_limits is False
    assert result.adjusted_payout == 49500.0


def test_payout_never_negative():
    result = calculate_adjusted_payout(100, 50000, 500)
    assert result.within_limits is True
    assert result.adjusted_payout == 0.0


def test_base_score_scales_with_claim_size():
    assert base_score_for_claim_size(1000, 50000) == 5
    assert base_score_for_claim_size(25000, 50000) == 10
    assert base_score_for_claim_size(50000, 50000) == 15
    assert base_score_for_claim_size(100000, 50000) == 15
    assert base_score_for_claim_size(1000, 0) == 15  # degenerate limit


def test_clean_small_claim_auto_approves():
    result = assessment()
    assert (result.risk_score, result.recommendation, result.eligible) == (
        5,
        AUTO_APPROVE,
        True,
    )


def test_expired_policy_adds_40_and_auto_rejects():
    result = assessment(policy_status="expired")
    assert result.risk_score == 45
    assert result.recommendation == AUTO_REJECT
    assert result.eligible is False


def test_uncovered_incident_adds_35_and_auto_rejects():
    result = assessment(incident_type="flood")
    assert result.risk_score == 40
    assert result.recommendation == AUTO_REJECT
    assert result.eligible is False


def test_over_limit_adds_25_and_breaks_eligibility():
    result = assessment(claimed_amount=60000.0)
    assert result.risk_score == 40  # base 15 (at/above limit) + 25
    assert result.recommendation == ESCALATE
    assert result.eligible is False


def test_consistency_categories():
    """Category -> points: contradicts +20, partially_consistent +10, else +0."""
    assert assessment(consistency=CONTRADICTS).risk_score == 25  # +20
    assert assessment(consistency=PARTIALLY_CONSISTENT).risk_score == 15  # +10
    assert assessment(consistency=CONSISTENT).risk_score == 5  # +0
    assert assessment(consistency=NO_DOCUMENTS).risk_score == 5  # +0
    assert assessment(consistency=None).risk_score == 5  # +0


def test_consistency_score_for_maps_category_to_ui_score():
    """The stored numeric score is derived in code, never emitted by the model."""
    assert consistency_score_for(CONTRADICTS) == 20
    assert consistency_score_for(PARTIALLY_CONSISTENT) == 60
    assert consistency_score_for(CONSISTENT) == 90
    assert consistency_score_for(NO_DOCUMENTS) is None
    assert consistency_score_for(None) is None
    assert consistency_score_for("garbage") is None


def test_red_flags_cap_at_24_points():
    assert assessment(red_flag_count=1).risk_score == 13
    assert assessment(red_flag_count=5).risk_score == 29  # 5 + 24


def test_frequency_flag_adds_25_and_escalates():
    result = assessment(claim_frequency_flag=True)
    assert result.risk_score == 30
    assert result.recommendation == ESCALATE


def test_score_caps_at_100():
    result = assessment(
        policy_status="suspended",
        incident_type="flood",
        claim_frequency_flag=True,
        consistency=CONTRADICTS,
        red_flag_count=3,
    )
    assert result.risk_score == 100
    assert result.recommendation == AUTO_REJECT


def test_base_score_interpolates_between_bands():
    # 30000/50000 -> 0.6 ratio: 5 + int(0.6 * 10) = 11, still within limits.
    assert base_score_for_claim_size(30000, 50000) == 11


def _claim(days_ago: float) -> dict:
    claim_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"claim_date": claim_date.isoformat()}


def test_claim_frequency_flag_twelve_month_window():
    assert _claim_frequency_flag([_claim(30), _claim(60), _claim(90)]) is True
    assert _claim_frequency_flag([_claim(30), _claim(60)]) is False
    # Claims older than the window do not count.
    assert _claim_frequency_flag([_claim(30), _claim(400), _claim(410)]) is False
    # Malformed dates are skipped, not fatal.
    assert (
        _claim_frequency_flag([{"claim_date": "not-a-date"}, _claim(30), _claim(60), _claim(90)])
        is True
    )
    assert _claim_frequency_flag([]) is False
