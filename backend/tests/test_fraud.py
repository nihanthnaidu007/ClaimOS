"""Fraud cross-check tests (spec Tier 3): exact rule values, risk bump, agent wiring."""

from datetime import date

import pytest

import agents
from agents import FraudSimilarityOutput, fraud_agent
from app.fraud import (
    CODE_CLAIMED_AMOUNT_RATIO,
    CODE_DUPLICATE_INCIDENT,
    CODE_FUTURE_INCIDENT_DATE,
    CODE_INCIDENT_BEFORE_POLICY_START,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    detect_fraud_flags,
    incident_fingerprint,
    prior_claim_fingerprints,
)
from app.rating import compute_risk_assessment
from app.usage import UsageLogger

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 9, 17)


def _fingerprints(*claim_ids: str) -> dict[str, str]:
    return {f"fp-{cid}": cid for cid in claim_ids}


# ============ fingerprint ============


class TestIncidentFingerprint:
    def test_deterministic_across_calls(self):
        first = incident_fingerprint("AUTO-2024-001847", "2026-09-01", "theft")
        second = incident_fingerprint("AUTO-2024-001847", "2026-09-01", "theft")
        assert first == second

    def test_normalizes_case_and_whitespace(self):
        assert incident_fingerprint(" AUTO-1 ", "2026-09-01", "Theft") == incident_fingerprint(
            "auto-1", "2026-09-01", "theft"
        )

    def test_different_fields_give_different_fingerprints(self):
        base = incident_fingerprint("AUTO-1", "2026-09-01", "theft")
        assert base != incident_fingerprint("AUTO-1", "2026-09-02", "theft")
        assert base != incident_fingerprint("AUTO-1", "2026-09-01", "vandalism")
        assert base != incident_fingerprint("AUTO-2", "2026-09-01", "theft")


class TestPriorClaimFingerprints:
    def test_uses_stored_fingerprint_when_present(self):
        rows = [{"id": "C1", "incident_fingerprint": "fp-stored"}]
        assert prior_claim_fingerprints(rows) == {"fp-stored": "C1"}

    def test_recomputes_for_seeded_rows_without_stored_fingerprint(self):
        rows = [
            {
                "id": "C2",
                "policy_number": "AUTO-1",
                "incident_date": "2026-09-01",
                "incident_type": "theft",
            }
        ]
        assert prior_claim_fingerprints(rows) == {
            incident_fingerprint("AUTO-1", "2026-09-01", "theft"): "C2"
        }


# ============ deterministic rules ============


class TestDetectFraudFlags:
    def test_clean_claim_has_no_flags(self):
        flags = detect_fraud_flags(
            claimed_amount=1200.0,
            coverage_limit=50000.0,
            incident_date="2026-09-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints=_fingerprints("C1"),
            today=TODAY,
        )
        assert flags == []

    def test_duplicate_fingerprint_flags_high_with_evidence(self):
        flags = detect_fraud_flags(
            claimed_amount=1200.0,
            coverage_limit=50000.0,
            incident_date="2026-09-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-C1",
            prior_incident_fingerprints=_fingerprints("C1"),
            today=TODAY,
        )
        assert [f.code for f in flags] == [CODE_DUPLICATE_INCIDENT]
        assert flags[0].severity == SEVERITY_HIGH
        assert flags[0].evidence["duplicate_of_claim_id"] == "C1"

    def test_future_incident_date_flags_high(self):
        flags = detect_fraud_flags(
            claimed_amount=100.0,
            coverage_limit=50000.0,
            incident_date="2999-01-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints={},
            today=TODAY,
        )
        assert [f.code for f in flags] == [CODE_FUTURE_INCIDENT_DATE]
        assert flags[0].severity == SEVERITY_HIGH
        assert flags[0].evidence["incident_date"] == "2999-01-01"

    def test_incident_before_policy_start_flags_medium(self):
        flags = detect_fraud_flags(
            claimed_amount=100.0,
            coverage_limit=50000.0,
            incident_date="2025-01-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints={},
            today=TODAY,
        )
        assert [f.code for f in flags] == [CODE_INCIDENT_BEFORE_POLICY_START]
        assert flags[0].severity == SEVERITY_MEDIUM

    def test_claim_at_coverage_limit_ratio_flags_medium(self):
        flags = detect_fraud_flags(
            claimed_amount=50000.0,
            coverage_limit=50000.0,
            incident_date="2026-09-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints={},
            today=TODAY,
        )
        assert [f.code for f in flags] == [CODE_CLAIMED_AMOUNT_RATIO]
        assert flags[0].severity == SEVERITY_MEDIUM
        assert flags[0].evidence["ratio"] == 1.0

    def test_claim_under_ratio_threshold_has_no_flag(self):
        flags = detect_fraud_flags(
            claimed_amount=25000.0,
            coverage_limit=50000.0,
            incident_date="2026-09-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints={},
            today=TODAY,
        )
        assert flags == []

    def test_zero_coverage_limit_never_divides(self):
        flags = detect_fraud_flags(
            claimed_amount=5000.0,
            coverage_limit=0.0,
            incident_date="2026-09-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-new",
            prior_incident_fingerprints={},
            today=TODAY,
        )
        assert flags == []

    def test_multiple_rules_accumulate(self):
        flags = detect_fraud_flags(
            claimed_amount=60000.0,
            coverage_limit=50000.0,
            incident_date="2999-01-01",
            policy_start_date="2026-01-01",
            fingerprint="fp-C1",
            prior_incident_fingerprints=_fingerprints("C1"),
            today=TODAY,
        )
        assert {f.code for f in flags} == {
            CODE_DUPLICATE_INCIDENT,
            CODE_FUTURE_INCIDENT_DATE,
            CODE_CLAIMED_AMOUNT_RATIO,
        }


# ============ risk-score bump (deterministic math) ============


class TestFraudRiskBump:
    def _assessment(self, severities):
        return compute_risk_assessment(
            policy_status="active",
            incident_type="theft",
            covered_events=["theft"],
            claimed_amount=1000.0,
            coverage_limit=50000.0,
            deductible=500.0,
            claim_frequency_flag=False,
            consistency_score=95,
            red_flag_count=0,
            fraud_flag_severities=severities,
        )

    def test_no_flags_no_bump(self):
        assert self._assessment([]).risk_score == self._assessment(None).risk_score

    def test_high_flag_adds_twenty(self):
        clean = self._assessment([]).risk_score
        assert self._assessment(["high"]).risk_score == clean + 20

    def test_severities_add_and_cap_at_forty(self):
        clean = self._assessment([]).risk_score
        # 2 high + 1 medium = 50 raw, capped at 40.
        assert self._assessment(["high", "high", "medium"]).risk_score == clean + 40

    def test_bump_can_flip_recommendation_to_escalate(self):
        clean = self._assessment([]).risk_score
        assert clean < 30
        bumped = self._assessment(["high", "high"])  # +40
        assert bumped.risk_score >= 30
        assert bumped.recommendation == "escalate"

    def test_factor_line_names_the_cross_check(self):
        assert any("Fraud cross-check" in f for f in self._assessment(["low"]).risk_factors)


# ============ agent wiring (mocked adapter) ============


class _FakeUsage:
    input_tokens = 11
    output_tokens = 42


class _ParsedMessage:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.stop_reason = "end_turn"
        self.usage = _FakeUsage()


class _RecordingMessages:
    """Fake messages API: returns a canned similarity verdict, records parse calls."""

    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.schemas = []

    async def parse(self, **kwargs):
        self.schemas.append(kwargs["output_format"].__name__)
        return _ParsedMessage(self.parsed_output)


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def _fraud_state(**overrides):
    state = {
        "claimId": "CLM-FRAUD-1",
        "input": {"policyNumber": "AUTO-1"},
        "intake": {
            "normalizedData": {
                "policyNumber": "AUTO-1",
                "incidentDate": "2026-09-01",
                "incidentType": "theft",
                "claimedAmount": 1200.0,
            }
        },
        "policy": {
            "policyData": {
                "policy_number": "AUTO-1",
                "coverage_limit": 50000.0,
                "start_date": "2026-01-01",
            }
        },
    }
    state.update(overrides)
    return state


def _install_adapter(monkeypatch, parsed_output):
    messages = _RecordingMessages(parsed_output)
    fake = agents.LLMAdapter(client=_FakeClient(messages), usage_logger=UsageLogger())
    monkeypatch.setattr(agents, "adapter", fake)
    return messages


class TestFraudAgent:
    async def test_duplicate_candidate_triggers_llm_judgment(
        self, patched_mongo, monkeypatch
    ):
        await patched_mongo.claims.insert_one(
            {
                "id": "CLM-PRIOR-1",
                "policy_number": "AUTO-1",
                "incident_date": "2026-09-01",
                "incident_type": "theft",
                "claimed_amount": 1100.0,
                "status": "approved",
            }
        )
        messages = _install_adapter(
            monkeypatch,
            FraudSimilarityOutput(
                similar=True,
                confidence=0.9,
                reasoning="Identical incident fields on the same policy.",
                cited_evidence=[
                    {
                        "from_claim_id": "CLM-PRIOR-1",
                        "field": "incident_date",
                        "value": "2026-09-01",
                    }
                ],
            ),
        )

        state = await fraud_agent(_fraud_state())

        assert messages.schemas == ["FraudSimilarityOutput"]  # exactly one LLM call
        flags = state["fraud"]["flags"]
        assert [f["code"] for f in flags] == [CODE_DUPLICATE_INCIDENT]
        assert flags[0]["evidence"]["duplicate_of_claim_id"] == "CLM-PRIOR-1"
        sim = state["fraud"]["similarity"]
        assert sim["similar"] is True
        assert sim["cited_evidence"][0]["from_claim_id"] == "CLM-PRIOR-1"

    async def test_no_candidates_skips_the_llm(self, patched_mongo, monkeypatch):
        messages = _install_adapter(monkeypatch, FraudSimilarityOutput(similar=False))

        state = await fraud_agent(_fraud_state())

        assert messages.schemas == []  # judgment only runs on candidates
        assert state["fraud"]["similarity"] is None
        assert state["fraud"]["flags"] == []

    async def test_future_date_flags_without_llm_call(self, patched_mongo, monkeypatch):
        messages = _install_adapter(monkeypatch, FraudSimilarityOutput(similar=False))
        state = _fraud_state()
        state["intake"]["normalizedData"]["incidentDate"] = "2999-01-01"

        result = await fraud_agent(state)

        assert messages.schemas == []
        assert [f["code"] for f in result["fraud"]["flags"]] == [CODE_FUTURE_INCIDENT_DATE]

    async def test_llm_failure_degrades_to_deterministic_flags(
        self, patched_mongo, monkeypatch
    ):
        await patched_mongo.claims.insert_one(
            {
                "id": "CLM-PRIOR-2",
                "policy_number": "AUTO-1",
                "incident_date": "2026-09-01",
                "incident_type": "theft",
            }
        )

        class _FailingAdapter:
            def model_for(self, agent):
                return "claude-fake"

            async def complete_structured(self, **kwargs):
                raise agents.LLMError("no api key")

        failing = _FailingAdapter()
        monkeypatch.setattr(agents, "adapter", failing)

        state = await fraud_agent(_fraud_state())

        flags = state["fraud"]["flags"]
        assert [f["code"] for f in flags] == [CODE_DUPLICATE_INCIDENT]
        assert "error" in state["fraud"]["similarity"]

    async def test_self_is_excluded_from_duplicate_check(self, patched_mongo, monkeypatch):
        # The claim's own row (a retry) must not count as a prior incident.
        await patched_mongo.claims.insert_one(
            {
                "id": "CLM-FRAUD-1",
                "policy_number": "AUTO-1",
                "incident_date": "2026-09-01",
                "incident_type": "theft",
                "incident_fingerprint": incident_fingerprint("AUTO-1", "2026-09-01", "theft"),
            }
        )
        messages = _install_adapter(monkeypatch, FraudSimilarityOutput(similar=False))

        state = await fraud_agent(_fraud_state())

        assert messages.schemas == []
        assert state["fraud"]["flags"] == []
