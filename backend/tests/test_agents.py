"""Agent rewiring tests — agents emit Pydantic-validated dicts, mocked client, no key."""

import asyncio
import json
import sys

import pytest

import agents
from agents import DecisionOutput, IntakeOutput, decision_agent, intake_agent


class FakeUsage:
    input_tokens = 11
    output_tokens = 42

    def model_dump(self):
        return {"input_tokens": 11, "output_tokens": 42}


class FakeParsedMessage:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class ScriptedMessages:
    """Fake messages API returning one canned parsed output."""

    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.parse_calls = []

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return FakeParsedMessage(self.parsed_output, self.stop_reason)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


class RecordingUsage:
    """No-op usage sink that records calls — avoids any Mongo access in tests."""

    def __init__(self):
        self.records = []

    async def log(self, **kwargs):
        self.records.append(kwargs)


INTAKE_MODEL = IntakeOutput(
    valid=True,
    normalizedData={
        "policyNumber": "AUTO-2024-001847",
        "incidentDate": "2026-09-10",
        "incidentType": "accident",
        "claimedAmount": 4500.0,
        "description": "Rear-end collision on I-35 with a police report filed.",
    },
    missingFields=[],
    validationNotes="All fields present and valid.",
    reasoning="Step 1-3 checked, normalized, VALID.",
)

DECISION_MODEL = DecisionOutput(
    verdict="approved",
    payoutAmount=4500.0,
    letterSubject="Your Claim CLM-DRY-1 Has Been Approved",
    letterBody="Dear Sarah Chen, we are pleased to approve your claim.",
    nextSteps=["Payout within 5-7 business days"],
    reasoning="Low risk, within policy limits.",
)


def test_agents_module_imports_without_emergent():
    # agents.py is imported at module scope; the point is that the
    # emergentintegrations package was never pulled into sys.modules.
    assert "emergentintegrations" not in sys.modules


def test_intake_agent_emits_validated_dict(monkeypatch):
    usage = RecordingUsage()
    fake_client = FakeClient(ScriptedMessages(INTAKE_MODEL))
    monkeypatch.setattr(agents, "adapter", agents.LLMAdapter(client=fake_client, usage_logger=usage))

    state = {"claimId": "CLM-DRY-0", "input": {"policyNumber": "AUTO-2024-001847"}}
    result = asyncio.run(intake_agent(state))

    assert result["intake"]["valid"] is True
    assert result["intake"]["normalizedData"]["incidentDate"] == "2026-09-10"
    # SSE payloads embed agent output as JSON — it must stay plain dicts.
    json.dumps(result)


def test_decision_agent_emits_validated_output_with_email_false(monkeypatch):
    usage = RecordingUsage()
    fake_client = FakeClient(ScriptedMessages(DECISION_MODEL))
    fake_adapter = agents.LLMAdapter(client=fake_client, usage_logger=usage)
    monkeypatch.setattr(agents, "adapter", fake_adapter)

    state = {
        "claimId": "CLM-DRY-1",
        "input": {"policyNumber": "AUTO-2024-001847"},
        "intake": INTAKE_MODEL.model_dump(),
        "policy": {
            "found": True,
            "statusCheck": "ACTIVE",
            "adjustedPayout": 4500.0,
            "deductibleApplied": 500.0,
            "policyData": {
                "policy_number": "AUTO-2024-001847",
                "holder_name": "Sarah Chen",
                "status": "active",
                "coverage_limit": 50000.0,
                "deductible": 500.0,
            },
        },
        "documents": {
            "consistencyScore": 92,
            "redFlags": [],
        },
        "eligibility": {
            "eligible": True,
            "riskScore": 8,
            "recommendation": "auto_approve",
        },
        "decision": {},
        "agentLogs": [],
    }

    result = asyncio.run(decision_agent(state))

    decision = result["decision"]
    assert decision["verdict"] == "approved"
    assert decision["payoutAmount"] == 4500.0
    assert decision["emailSent"] is False  # mock email, unchanged contract
    assert decision["letterSubject"].startswith("Your Claim CLM-DRY-1")
    # Round-trip: the emitted dict revalidates against the output model.
    assert DecisionOutput.model_validate(decision).verdict == "approved"
    json.dumps(result)  # SSE-serializable


def test_decision_agent_uses_configured_model_and_claim_id(monkeypatch):
    usage = RecordingUsage()
    fake_client = FakeClient(ScriptedMessages(DECISION_MODEL))
    monkeypatch.setattr(
        agents, "adapter", agents.LLMAdapter(client=fake_client, usage_logger=usage)
    )

    state = {"claimId": "CLM-DRY-3", "input": {}, "intake": {}, "policy": {},
             "documents": {}, "eligibility": {}, "decision": {}, "agentLogs": []}
    asyncio.run(decision_agent(state))

    call = fake_client.messages.parse_calls[0]
    assert call["model"] == "claude-sonnet-5"  # Decision tier default
    assert call["output_format"] is DecisionOutput
    assert call["system"].startswith("You are the Decision")
    # claim_id rides the call for per-claim usage attribution.
    assert usage.records[0]["claim_id"] == "CLM-DRY-3"
    assert usage.records[0]["agent"] == "decision"


def test_refusal_surfaces_typed_error_through_agent(monkeypatch):
    from app.llm.adapter import LLMRefusal

    fake_client = FakeClient(ScriptedMessages(None, stop_reason="refusal"))
    monkeypatch.setattr(
        agents, "adapter", agents.LLMAdapter(client=fake_client, usage_logger=RecordingUsage())
    )

    state = {"claimId": "CLM-DRY-2", "input": {"policyNumber": "X"}}
    with pytest.raises(LLMRefusal):
        asyncio.run(intake_agent(state))
    # Refusals are deterministic: exactly one attempt.
    assert len(fake_client.messages.parse_calls) == 1


def _intake_model(**overrides):
    base = INTAKE_MODEL.model_dump()
    base.update(overrides)
    return IntakeOutput(**base)


def _wired_intake(monkeypatch, model):
    usage = RecordingUsage()
    monkeypatch.setattr(
        agents,
        "adapter",
        agents.LLMAdapter(client=FakeClient(ScriptedMessages(model)), usage_logger=usage),
    )


def test_intake_validity_is_decided_in_code_not_by_the_model(monkeypatch):
    """Live-run regression: the intake model called 2026-09-01 'in the future'
    relative to 2026-09-17 even when told today's date, halting a valid claim.
    Validity is enforced by _deterministic_intake_verdict, not the model."""
    hallucinated = _intake_model(
        valid=False,
        validationNotes="Incident date 2026-09-01 is in the future relative to today.",
    )
    _wired_intake(monkeypatch, hallucinated)

    state = {"claimId": "CLM-DRY-3", "input": {"policyNumber": "AUTO-2024-001847"}}
    result = asyncio.run(intake_agent(state))

    assert result["intake"]["valid"] is True
    assert result["intake"]["validationNotes"].startswith("All required fields present")


def test_intake_future_date_is_rejected_in_code_even_if_model_approves(monkeypatch):
    future_model = _intake_model(
        valid=True,
        normalizedData={
            **INTAKE_MODEL.normalizedData.model_dump(),
            "incidentDate": "2999-01-01",
        },
    )
    _wired_intake(monkeypatch, future_model)

    state = {"claimId": "CLM-DRY-4", "input": {"policyNumber": "AUTO-2024-001847"}}
    result = asyncio.run(intake_agent(state))

    assert result["intake"]["valid"] is False
    assert "future" in result["intake"]["validationNotes"]


def test_intake_missing_fields_fail_deterministically(monkeypatch):
    sparse = _intake_model(
        valid=True,
        normalizedData={
            "policyNumber": "",
            "incidentDate": "",
            "incidentType": "",
            "claimedAmount": 0.0,
            "description": "",
        },
    )
    _wired_intake(monkeypatch, sparse)

    state = {"claimId": "CLM-DRY-5", "input": {"policyNumber": ""}}
    result = asyncio.run(intake_agent(state))

    assert result["intake"]["valid"] is False
    assert "policyNumber is missing" in result["intake"]["validationNotes"]
    assert "claimedAmount must be positive" in result["intake"]["validationNotes"]
