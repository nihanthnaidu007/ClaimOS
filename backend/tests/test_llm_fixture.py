"""Fixture LLM adapter — deterministic outputs and fault injection.

These tests pin the fixture provider's contract: valid structured outputs for
every agent prompt shape, fault semantics that mirror the live failure classes
(schema = transient-with-cap, refusal = one agent-level retry, timeout =
terminal), and scoping to the named agent only.
"""

import json

import pytest
from pydantic import ValidationError

from app.config import settings
from app.llm import adapter as adapter_module
from app.llm.adapter import (
    LLMError,
    LLMRefusal,
    LLMSchemaValidationError,
    LLMTimeout,
)
from app.llm.fixture import FixtureLLMAdapter, parse_fixture_faults
from app.usage import UsageLogger
from app.llm.schemas import (
    DecisionResult,
    DocumentAnalysis,
    EligibilityResult,
    FraudSimilarityOutput,
    IntakeResult,
    PolicyVerification,
)

INTAKE_USER = (
    "Normalize this claim submission.\n"
    "Raw claim submission data: "
    + json.dumps(
        {
            "policyNumber": "AUTO-2024-001847",
            "incidentDate": "2026-09-17",
            "incidentType": "Theft",
            "claimedAmount": 2000,
            "description": "Bicycle stolen from apartment bike room; police report filed.",
        }
    )
)

POLICY_USER = (
    "Today's date is 2026-09-18; the 12-month claim window covers 2025-09-18 "
    "through today.\n"
    "Policy lookup result: "
    + json.dumps(
        {
            "policy_number": "AUTO-2024-001847",
            "status": "active",
            "policy_type": "auto",
            "coverage_limit": 50000,
            "deductible": 500,
            "covered_events": ["theft", "accident", "fire"],
            "start_date": "2024-01-15",
            "end_date": "2027-01-15",
        }
    )
    + "\n"
    "Claim history result (last 12 months): []\n"
    "Submitted claim data — incident date: 2026-09-17, incident type: theft, "
    "claimed amount: 2000"
)

DOCUMENT_USER_WITH_DOCS = (
    "Claim description: Bicycle stolen from the apartment bike room overnight.\n"
    "Supporting document text: POLICE REPORT case 26-44112. Owner: Dana Whitfield. "
    "Property: Trek mountain bike, value approximately $2,000. Stolen 2026-09-17.\n"
    "Submitted claim data: incident date=2026-09-17, incident type=theft, "
    "claimed amount=$2000.0, policy type=auto"
)

DOCUMENT_USER_NO_DOCS = (
    "Claim description: Bicycle stolen from the apartment bike room overnight.\n"
    "Supporting document text: No additional documents submitted.\n"
    "Submitted claim data: incident date=2026-09-17, incident type=theft, "
    "claimed amount=$2000.0, policy type=auto"
)

DECISION_USER = (
    "All agent outputs (structured):\n"
    "INTAKE: valid=True, incident date=2026-09-17, incident type=theft\n"
    "POLICY: found=True, status=active, adjustedPayout=1500.0, deductibleApplied=500\n"
    "DOCUMENTS: consistency=consistent, redFlags=[]\n"
    "ELIGIBILITY: eligible=True, riskScore=0, recommendation=auto_approve, riskFactors=[]\n"
    "Claim ID: CLM-20260918-001\n"
    "Policy Number: AUTO-2024-001847\n"
    "Holder Name: Dana Whitfield\n"
    "Computed payout amount: $1,500.00 (echo this — never recalculate)"
)


async def call(adapter, agent, user_text, schema, claim_id=None):
    return await adapter.complete_structured(
        model="fixture-model",
        system="fixture system",
        messages=[{"role": "user", "content": user_text}],
        output_schema=schema,
        agent=agent,
        claim_id=claim_id,
    )


class _NoopUsage(UsageLogger):
    """Unit-test stand-in: no Mongo writes, so nothing outlives the loop."""

    def __init__(self):
        self.records = []

    async def log(self, **record):
        self.records.append(record)


def make_adapter(faults="", max_retries=2):
    adapter = FixtureLLMAdapter(usage_logger=_NoopUsage())
    adapter._max_retries = max_retries
    if faults:
        adapter._faults = parse_fixture_faults(faults)
    return adapter


class TestFixtureOutputs:
    async def test_intake_normalizes_submission(self):
        result = await call(make_adapter(), "intake", INTAKE_USER, IntakeResult)
        assert result.valid is True
        assert result.normalizedData.incidentType == "theft"
        assert result.normalizedData.claimedAmount == 2000
        assert result.normalizedData.policyNumber == "AUTO-2024-001847"

    async def test_policy_reads_status_and_coverage(self):
        result = await call(make_adapter(), "policy", POLICY_USER, PolicyVerification)
        assert result.status == "active"
        assert result.coverage == "covered"
        assert result.appearsOverLimit is False
        assert any("status=active" in cited for cited in result.citedFields)

    async def test_policy_flags_over_limit(self):
        over = POLICY_USER.replace("claimed amount: 2000", "claimed amount: 60000")
        result = await call(make_adapter(), "policy", over, PolicyVerification)
        assert result.appearsOverLimit is True

    async def test_document_with_supporting_text_is_consistent(self):
        result = await call(
            make_adapter(), "document", DOCUMENT_USER_WITH_DOCS, DocumentAnalysis
        )
        assert result.consistency == "consistent"
        assert result.supportingEvidence, "verbatim evidence expected"

    async def test_document_without_supporting_text_flags_no_documents(self):
        result = await call(
            make_adapter(), "document", DOCUMENT_USER_NO_DOCS, DocumentAnalysis
        )
        assert result.consistency == "no_documents"
        assert result.supportingEvidence == []

    async def test_fraud_judges_not_similar(self):
        result = await call(
            make_adapter(), "fraud", "candidates: []", FraudSimilarityOutput
        )
        assert result.similar is False
        assert result.confidence == 0.5

    async def test_eligibility_passes_clean(self):
        result = await call(
            make_adapter(), "eligibility", "structured summary", EligibilityResult
        )
        assert result.eligible is True
        assert result.recommendation == "auto_approve"

    async def test_decision_letter_is_customer_safe(self):
        result = await call(
            make_adapter(), "decision", DECISION_USER, DecisionResult,
            claim_id="CLM-20260918-001",
        )
        assert result.verdict == "approved"
        assert result.payoutAmount == 1500.0
        assert result.confidence > 0.85
        blob = " ".join([result.letterSubject, result.letterBody, *result.nextSteps])
        lowered = blob.lower()
        for jargon in ("risk", "fraud", "score"):
            assert jargon not in lowered, f"jargon {jargon!r} leaked into the letter"
        assert "Dana Whitfield" in result.letterBody
        assert "$1,500.00" in result.letterBody

    async def test_decision_reject_letter_for_rejected_recommendation(self):
        rejected = DECISION_USER.replace(
            "recommendation=auto_approve", "recommendation=auto_reject"
        )
        result = await call(
            make_adapter(), "decision", rejected, DecisionResult,
            claim_id="CLM-20260918-001",
        )
        assert result.verdict == "rejected"
        assert "appeal" in result.letterBody.lower()

    async def test_decision_under_review_letter_for_escalate(self):
        escalated = DECISION_USER.replace(
            "recommendation=auto_approve", "recommendation=escalate"
        )
        result = await call(
            make_adapter(), "decision", escalated, DecisionResult,
            claim_id="CLM-20260918-001",
        )
        assert result.verdict == "under_review"
        assert result.payoutAmount == 1500.0

    async def test_confidence_env_override_feeds_stp_gate(self, monkeypatch):
        monkeypatch.setenv("FIXTURE_CONFIDENCE", "0.5")
        result = await call(make_adapter(), "decision", DECISION_USER, DecisionResult)
        assert result.confidence == 0.5


class TestFaultInjection:
    async def test_timeout_fault_is_terminal(self):
        adapter = make_adapter()
        adapter._faults = parse_fixture_faults("timeout:eligibility")
        with pytest.raises(LLMTimeout):
            await call(adapter, "eligibility", "x", EligibilityResult)

    async def test_persistent_schema_fault_exhausts_cap(self):
        adapter = make_adapter("schema:policy")
        with pytest.raises(LLMSchemaValidationError):
            await call(adapter, "policy", POLICY_USER, PolicyVerification)

    async def test_schema_once_recovers_on_retry(self):
        adapter = make_adapter("schema_once:policy")
        result = await call(adapter, "policy", POLICY_USER, PolicyVerification)
        assert result.status == "active"

    async def test_refusal_propagates_immediately(self):
        adapter = make_adapter()
        adapter._faults = parse_fixture_faults("refusal:decision")
        with pytest.raises(LLMRefusal):
            await call(adapter, "decision", DECISION_USER, DecisionResult)

    async def test_refusal_once_recovers_on_second_call(self):
        # The adapter propagates refusals; the agent layer owns the one
        # identical retry. Attempt 0 refuses, the retry (attempt 1) succeeds.
        adapter = make_adapter("refusal_once:decision")
        with pytest.raises(LLMRefusal):
            await call(adapter, "decision", DECISION_USER, DecisionResult)
        second = await call(adapter, "decision", DECISION_USER, DecisionResult)
        assert second.verdict == "approved"

    async def test_fault_is_scoped_to_the_named_agent(self):
        adapter = make_adapter()
        adapter._faults = parse_fixture_faults("timeout:eligibility")
        result = await call(adapter, "intake", INTAKE_USER, IntakeResult)
        assert result.valid is True
        with pytest.raises(LLMTimeout):
            await call(adapter, "eligibility", "x", EligibilityResult)


class TestFixtureParsing:
    async def test_unparseable_prompt_fails_loudly(self):
        adapter = make_adapter()
        with pytest.raises(LLMError):
            await call(adapter, "intake", "no markers here", IntakeResult)

    async def test_unknown_agent_fails_loudly(self):
        adapter = make_adapter()
        with pytest.raises(LLMError):
            await call(adapter, "nonsense", "x", IntakeResult)

    def test_fault_parser_ignores_unknown_entries(self):
        faults = parse_fixture_faults(
            "timeout:eligibility, garbage, schema:nope, refusal:decision"
        )
        assert ("timeout", "eligibility") in faults
        assert ("refusal", "decision") in faults
        assert len(faults) == 2

    def test_schema_type_mismatch_is_an_internal_error(self):
        # A wiring bug (builder output not matching the requested schema) must
        # surface as a loud internal error, not silent junk downstream.
        adapter = make_adapter()
        with pytest.raises((LLMError, ValidationError)):
            adapter._fixture_output(
                "intake", [{"role": "user", "content": INTAKE_USER}], DecisionResult, None
            )


class TestGetAdapterWiring:
    def test_fixture_provider_selected_from_settings(self, monkeypatch):
        monkeypatch.setattr(adapter_module, "_adapter", None)
        monkeypatch.setattr(settings, "llm_provider", "fixture")
        try:
            resolved = adapter_module.get_adapter()
        finally:
            adapter_module._adapter = None
        assert isinstance(resolved, FixtureLLMAdapter)
