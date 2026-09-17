"""Unit tests for the deterministic STP gate (spec AC-5) — no Mongo, no LLM."""

from app.rating import AUTO_APPROVE
from app.stp import (
    ELEVATED,
    LOW,
    assess_claim_severity,
    eligibility_is_clean,
    evaluate_stp_gate,
)

LOW_TYPES = {"theft", "weather_damage", "vandalism"}
CLEAN = {"eligible": True, "recommendation": AUTO_APPROVE, "fraudIndicators": []}


def test_severity_low_requires_both_type_and_amount():
    assert (
        assess_claim_severity(
            claimed_amount=9999,
            incident_type="theft",
            low_amount_threshold=10_000,
            low_types=LOW_TYPES,
        )
        == LOW
    )
    assert (
        assess_claim_severity(
            claimed_amount=10_001,
            incident_type="theft",
            low_amount_threshold=10_000,
            low_types=LOW_TYPES,
        )
        == ELEVATED
    )
    assert (
        assess_claim_severity(
            claimed_amount=100,
            incident_type="fire",
            low_amount_threshold=10_000,
            low_types=LOW_TYPES,
        )
        == ELEVATED
    )


def test_severity_normalizes_case_and_whitespace():
    assert (
        assess_claim_severity(
            claimed_amount=500,
            incident_type="  Theft ",
            low_amount_threshold=10_000,
            low_types=LOW_TYPES,
        )
        == LOW
    )
    assert (
        assess_claim_severity(
            claimed_amount=500,
            incident_type="",
            low_amount_threshold=10_000,
            low_types=LOW_TYPES,
        )
        == ELEVATED
    )


def test_gate_auto_finalizes_when_all_legs_hold():
    gate = evaluate_stp_gate(
        confidence=0.85, severity=LOW, eligibility=CLEAN, threshold=0.85
    )
    assert gate.auto_finalize is True
    assert gate.failed_legs == []


def test_gate_collects_every_failed_leg():
    gate = evaluate_stp_gate(
        confidence=0.10,
        severity=ELEVATED,
        eligibility={"eligible": False, "recommendation": "manual_review", "fraudIndicators": ["duplicate"]},
        threshold=0.85,
    )
    assert gate.auto_finalize is False
    reason = gate.escalation_reason()
    assert reason.count(";") == 2  # all three legs enumerated
    assert "below threshold" in reason
    assert "severity" in reason
    assert "eligibility" in reason


def test_eligibility_clean_rejects_fraud_and_wrong_recommendation():
    assert eligibility_is_clean(CLEAN) is True
    assert eligibility_is_clean({**CLEAN, "fraudIndicators": ["duplicate"]}) is False
    assert eligibility_is_clean({**CLEAN, "recommendation": "manual_review"}) is False
    assert (
        eligibility_is_clean(
            {"eligible": False, "recommendation": AUTO_APPROVE, "fraudIndicators": []}
        )
        is False
    )
