"""Per-agent prompt-tuning tests — every agent against a mocked client.

Coverage required by the prompt-tuning brief, per agent: a valid schema
output round-trips through the agent unchanged; the refusal path retries
once then fails closed (Decision falls back to a template letter); and the
Decision output carries citations[]. Also covers fail-closed missing-input
errors, per-agent generation params, the cached prompt preamble, tool-call
trace fidelity, and document-text normalization.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import agents
from agents import (
    AGENT_PARAMS,
    MissingStageInputError,
    _normalize_document_text,
    decision_agent,
    document_agent,
    eligibility_agent,
    intake_agent,
    policy_agent,
    tool_claim_history,
)
from app.llm.adapter import LLMRefusal
from app.llm.schemas import (
    DecisionResult,
    DocumentAnalysis,
    EligibilityResult,
    IntakeResult,
    NormalizedClaim,
    PolicyVerification,
)

POLICY_NUMBER = "AUTO-2024-001847"

POLICY_DOC = {
    "policy_number": POLICY_NUMBER,
    "holder_name": "Sarah Chen",
    "status": "active",
    "policy_type": "auto",
    "start_date": "2024-01-01",
    "end_date": "2027-01-01",
    "coverage_limit": 50000.0,
    "deductible": 500.0,
    "covered_events": ["accident", "theft", "weather_damage", "vandalism"],
}


# ---- Mocked client plumbing -------------------------------------------------

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
    """Fake messages API: one canned parsed output, or refusals forever."""

    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.parse_calls = []

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return FakeParsedMessage(self.parsed_output, self.stop_reason)


class QueueMessages:
    """Fake messages API handing out canned outputs in order (retry tests).

    Items are SimpleNamespace-like or plain dicts (dicts are convenient for
    refusal stubs with no parsed payload).
    """

    def __init__(self, outputs):
        self.outputs = [
            SimpleNamespace(**item) if isinstance(item, dict) else item
            for item in outputs
        ]
        self.parse_calls = []

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        item = self.outputs.pop(0)
        return FakeParsedMessage(item.parsed_output, item.stop_reason)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


class RecordingUsage:
    def __init__(self):
        self.records = []

    async def log(self, **kwargs):
        self.records.append(kwargs)


def _wire(monkeypatch, messages, usage=None):
    fake = agents.LLMAdapter(
        client=FakeClient(messages), usage_logger=usage or RecordingUsage()
    )
    monkeypatch.setattr(agents, "adapter", fake)
    return fake


# ---- Typed fixtures (what the model is scripted to return) -------------------

def _intake_model(**overrides):
    base = dict(
        valid=True,
        normalizedData=NormalizedClaim(
            policyNumber=POLICY_NUMBER,
            incidentDate="2026-09-10",
            incidentType="accident",
            claimedAmount=4500.0,
            description="Rear-end collision on I-35 with a police report filed.",
        ),
        missingFields=[],
        flags=[],
        summary="Normalized and validated; no flags.",
    )
    base.update(overrides)
    return IntakeResult(**base)


def _policy_model(**overrides):
    base = dict(
        status="active",
        statusDetail="Policy active on the incident date (bounds inclusive).",
        coverage="covered",
        coverageDetail="accident is in covered_events.",
        appearsOverLimit=False,
        claimFrequencyFlag=False,
        priorClaims12mo=0,
        citedFields=[
            "end_date=2027-01-01",
            "start_date=2024-01-01",
            "covered_events=[accident, theft, weather_damage, vandalism]",
            "coverage_limit=50000.0",
            "deductible=500.0",
        ],
        summary="Active policy covers the incident; amounts within limits.",
    )
    base.update(overrides)
    return PolicyVerification(**base)


def _document_model(**overrides):
    base = dict(
        extracted={
            "datesFound": ["2026-09-10"],
            "locationMentioned": "I-35",
            "partiesInvolved": "Sarah Chen",
            "damageDescribed": "rear bumper damage",
            "amountsMentioned": [{"value": 4500.0, "context": "estimated repair cost"}],
        },
        consistency="consistent",
        redFlags=[],
        supportingEvidence=[{"excerpt": "police report #22-1187", "note": "corroborates date"}],
        summary="Document facts match the submission.",
    )
    base.update(overrides)
    return DocumentAnalysis(**base)


def _eligibility_model(**overrides):
    base = dict(
        eligible=True,
        riskFactors=[],
        fraudIndicators=[],
        recommendation="auto_approve",
        inputGaps=[],
        summary="Clean claim: active policy, covered incident, within limits.",
    )
    base.update(overrides)
    return EligibilityResult(**base)


def _decision_model(**overrides):
    base = dict(
        verdict="approved",
        payoutAmount=4000.0,
        letterSubject="Your Claim CLM-T-1 Has Been Approved",
        letterBody=(
            "Dear Sarah Chen,\n\nWe are pleased to approve your claim for the accident "
            "reported on 2026-09-10. The payout of $4,000.00 will be issued within 5-7 "
            "business days.\n\nSincerely,\nClaimOS Claims Processing Team"
        ),
        nextSteps=["No action needed; payout arrives within 5-7 business days."],
        citations=[
            {
                "fact": "Policy active on 2026-09-10 with accident coverage",
                "sourceRef": "policy.citedFields[0]: end_date=2027-01-01",
                "customerFriendlyExplanation": "Your policy was active and covers this incident.",
            }
        ],
        summary="Approved: active coverage, within limits.",
        confidence=0.95,
    )
    base.update(overrides)
    return DecisionResult(**base)


# Clean upstream state for the downstream agents.
def _clean_state(**stage_outputs):
    state = {
        "claimId": "CLM-T-1",
        "input": {
            "policyNumber": POLICY_NUMBER,
            "incidentDate": "2026-09-10",
            "incidentType": "Accident",
            "claimedAmount": 4500.0,
            "description": "Rear-end collision on I-35 with a police report filed.",
            "documentText": "Police report #22-1187 filed 2026-09-11.",
        },
        "intake": {},
        "policy": {},
        "documents": {},
        "eligibility": {},
        "decision": {},
        "agentLogs": [
            {"agent": "POLICY_AGENT", "status": "running", "toolsCalled": []}
        ],
    }
    state.update(stage_outputs)
    return state


# ---- Cross-cutting contract tests -------------------------------------------


def test_system_prompt_is_cached_preamble_plus_agent_block():
    system = agents._cached_system(agents.INTAKE_PROMPT)
    assert isinstance(system, list) and len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == agents.PROMPT_PREAMBLE
    assert system[1]["text"] == agents.INTAKE_PROMPT
    # Per-claim values must never ride in the (cacheable) system prompt.
    assert POLICY_NUMBER not in system[0]["text"]
    assert POLICY_NUMBER not in agents.DECISION_PROMPT


def test_per_agent_params_cover_all_six_agents():
    assert set(AGENT_PARAMS) == {
        "intake", "policy", "document", "fraud", "eligibility", "decision"
    }
    assert AGENT_PARAMS["intake"]["temperature"] == 0.0
    assert AGENT_PARAMS["policy"]["temperature"] == 0.0
    assert AGENT_PARAMS["decision"]["temperature"] > AGENT_PARAMS["eligibility"]["temperature"]
    assert AGENT_PARAMS["document"]["max_tokens"] > AGENT_PARAMS["intake"]["max_tokens"]


# ---- Intake agent ------------------------------------------------------------


def test_intake_valid_output_round_trips(monkeypatch):
    messages = ScriptedMessages(_intake_model())
    _wire(monkeypatch, messages)

    state = _clean_state()
    result = asyncio.run(intake_agent(state))

    assert result["intake"]["valid"] is True
    assert result["intake"]["normalizedData"]["incidentType"] == "accident"
    call = messages.parse_calls[0]
    assert call["extra_body"]["temperature"] == 0.0
    assert call["max_tokens"] == AGENT_PARAMS["intake"]["max_tokens"]
    assert call["output_format"] is IntakeResult
    json.dumps(result)  # SSE payloads embed agent output as JSON


def test_intake_future_date_flags_without_invalidating(monkeypatch):
    """Review semantics: a future date is a flag for downstream judgment,
    not a validity failure — validity is for absent/unparseable fields."""
    model = _intake_model()
    model.normalizedData.incidentDate = "2999-01-01"
    _wire(monkeypatch, ScriptedMessages(model))

    result = asyncio.run(intake_agent(_clean_state()))

    assert result["intake"]["valid"] is True
    codes = [flag["code"] for flag in result["intake"]["flags"]]
    assert "future_date" in codes


def test_intake_high_amount_and_thin_description_flags(monkeypatch):
    model = _intake_model()
    model.normalizedData.claimedAmount = 600000.0
    model.normalizedData.description = "Crashed."
    _wire(monkeypatch, ScriptedMessages(model))

    result = asyncio.run(intake_agent(_clean_state()))

    codes = [flag["code"] for flag in result["intake"]["flags"]]
    assert "high_amount" in codes
    assert "thin_description" in codes
    assert result["intake"]["valid"] is True


def test_intake_missing_required_field_fails_closed(monkeypatch):
    """The model approved, but a required normalized field is empty —
    only the deterministic verdict can fail the run on it."""
    model = _intake_model()
    model.normalizedData.description = ""
    _wire(monkeypatch, ScriptedMessages(model))

    result = asyncio.run(intake_agent(_clean_state()))

    assert result["intake"]["valid"] is False
    assert "description" in result["intake"]["missingFields"]


def test_intake_refusal_retries_once_then_raises(monkeypatch):
    """One identical retry on refusal; the second refusal fails the stage."""
    messages = QueueMessages(
        [
            {"parsed_output": None, "stop_reason": "refusal"},
            {"parsed_output": None, "stop_reason": "refusal"},
        ]
    )
    _wire(monkeypatch, messages)

    state = _clean_state()
    with pytest.raises(LLMRefusal):
        asyncio.run(intake_agent(state))
    assert len(messages.parse_calls) == 2  # original attempt + one retry


def test_intake_requires_raw_submission():
    with pytest.raises(MissingStageInputError):
        asyncio.run(intake_agent({"claimId": "CLM-T-2", "input": {}, "agentLogs": []}))


# ---- Policy agent ------------------------------------------------------------


def test_policy_tool_io_recorded_verbatim(monkeypatch, patched_mongo):
    asyncio.run(patched_mongo.policies.insert_one(dict(POLICY_DOC)))
    messages = ScriptedMessages(_policy_model())
    _wire(monkeypatch, messages)

    state = _clean_state()
    result = asyncio.run(
        policy_agent({**state, "intake": _intake_model().model_dump()})
    )

    tools = result["agentLogs"][0]["toolsCalled"]
    assert [tool["tool"] for tool in tools] == ["policyLookup", "claimHistory"]
    lookup_output = tools[0]["output"]
    assert lookup_output["found"] is True
    assert lookup_output["data"]["holder_name"] == "Sarah Chen"  # full record kept
    assert result["policy"]["found"] is True
    # Code-owned arithmetic landed on the stored output.
    assert result["policy"]["withinLimits"] is True
    assert result["policy"]["adjustedPayout"] == 4000.0
    assert result["policy"]["deductibleApplied"] == 500.0


def test_policy_history_window_filter_is_in_the_query(monkeypatch, patched_mongo):
    """Old claims are excluded by Mongo, not by post-filtering (review §7 item 11)."""
    asyncio.run(patched_mongo.claims.insert_one(
        {"policy_number": POLICY_NUMBER, "claim_date": "2020-01-01"}
    ))
    asyncio.run(patched_mongo.claims.insert_one(
        {"policy_number": POLICY_NUMBER, "claim_date": "2026-09-01"}
    ))
    result = asyncio.run(tool_claim_history(POLICY_NUMBER))
    assert result["count"] == 1
    assert result["claims"][0]["claim_date"] == "2026-09-01"


def test_policy_not_found_is_deterministic(monkeypatch, patched_mongo):
    """No LLM call for a code-decidable case; the run can halt on it."""
    messages = ScriptedMessages(_policy_model())
    _wire(monkeypatch, messages)

    state = _clean_state()
    result = asyncio.run(
        policy_agent({**state, "intake": _intake_model().model_dump()})
    )

    assert result["policy"]["found"] is False
    assert result["policy"]["status"] == "unknown"
    assert len(messages.parse_calls) == 0


def test_policy_requires_intake_state():
    with pytest.raises(MissingStageInputError):
        asyncio.run(policy_agent(_clean_state()))


# ---- Document agent ----------------------------------------------------------


def test_document_valid_output_round_trips(monkeypatch):
    messages = ScriptedMessages(_document_model())
    _wire(monkeypatch, messages)

    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    result = asyncio.run(document_agent(state))

    assert result["documents"]["consistency"] == "consistent"
    # Numeric score derived in code from the category.
    assert result["documents"]["consistencyScore"] == 90
    call = messages.parse_calls[0]
    assert call["output_format"] is DocumentAnalysis
    assert call["extra_body"]["temperature"] == AGENT_PARAMS["document"]["temperature"]


def test_document_no_documents_gets_no_score(monkeypatch):
    messages = ScriptedMessages(_document_model(consistency="no_documents"))
    _wire(monkeypatch, messages)

    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    state["input"]["documentText"] = ""
    result = asyncio.run(document_agent(state))

    assert result["documents"]["consistency"] == "no_documents"
    assert "consistencyScore" not in result["documents"]


def test_normalize_document_text_multimake_and_ocr():
    raw = (
        "Page one\fPage two with a com-\nputer claim."
        "\ufb01re  \ufb02ood   text   with\xa0nbsp\n\n\n\ntrailing pages"
    )
    cleaned = _normalize_document_text(raw)
    assert "--- PAGE 1 ---" in cleaned and "--- PAGE 2 ---" in cleaned
    assert "computer claim" in cleaned  # de-hyphenated line break
    assert "\ufb01" not in cleaned and "\ufb02" not in cleaned
    assert "\xa0" not in cleaned
    assert "\n\n\n" not in cleaned


def test_normalize_document_text_truncates():
    cleaned = _normalize_document_text("x" * 25_000)
    assert len(cleaned) < 25_000
    assert "truncated" in cleaned


# ---- Eligibility agent -------------------------------------------------------


def test_eligibility_valid_output_with_code_owned_score(monkeypatch):
    messages = ScriptedMessages(_eligibility_model())
    _wire(monkeypatch, messages)

    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    state["policy"] = {
        "found": True,
        "status": "active",
        "withinLimits": True,
        "adjustedPayout": 4000.0,
        "claimFrequencyFlag": False,
        "priorClaims12mo": 0,
        "policyData": dict(POLICY_DOC),
    }
    state["documents"] = _document_model().model_dump()
    result = asyncio.run(eligibility_agent(state))

    eligibility = result["eligibility"]
    assert eligibility["eligible"] is True
    assert eligibility["recommendation"] == "auto_approve"
    assert eligibility["riskScore"] == 5  # clean-claim base, nothing applied
    assert "riskFactorDetails" in eligibility  # model's typed factors preserved
    call = messages.parse_calls[0]
    assert call["output_format"] is EligibilityResult


def test_eligibility_model_disagreement_is_overridden_by_code(monkeypatch):
    """The model's score/routing is advisory: code owns the numbers."""
    hallucinated = _eligibility_model(eligible=True, recommendation="auto_approve")
    _wire(monkeypatch, ScriptedMessages(hallucinated))

    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    state["policy"] = {
        "found": True,
        "status": "expired",
        "withinLimits": False,
        "adjustedPayout": 0.0,
        "claimFrequencyFlag": False,
        "priorClaims12mo": 0,
        "policyData": {**POLICY_DOC, "status": "expired"},
    }
    state["documents"] = _document_model().model_dump()
    result = asyncio.run(eligibility_agent(state))

    eligibility = result["eligibility"]
    assert eligibility["eligible"] is False  # not active
    assert eligibility["recommendation"] == "auto_reject"  # expired forces reject
    assert eligibility["riskScore"] >= 40


def test_eligibility_requires_policy_data(monkeypatch):
    _wire(monkeypatch, ScriptedMessages(_eligibility_model()))
    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    state["documents"] = _document_model().model_dump()
    # policy stage present but policyData empty (e.g. legacy checkpoint)
    with pytest.raises(MissingStageInputError):
        asyncio.run(eligibility_agent(state))


# ---- Decision agent ----------------------------------------------------------


def _decision_stage_state(recommendation="auto_approve"):
    state = _clean_state()
    state["intake"] = _intake_model().model_dump()
    state["policy"] = {
        "found": True,
        "status": "active",
        "withinLimits": True,
        "adjustedPayout": 4000.0,
        "deductibleApplied": 500.0,
        "policyData": dict(POLICY_DOC),
    }
    state["documents"] = _document_model().model_dump()
    state["eligibility"] = {
        "eligible": True,
        "riskScore": 5,
        "riskFactors": [],
        "recommendation": recommendation,
    }
    return state


def test_decision_output_carries_citations(monkeypatch):
    """Brief requirement: Decision output contains citations[]."""
    messages = ScriptedMessages(_decision_model())
    _wire(monkeypatch, messages)

    result = asyncio.run(decision_agent(_decision_stage_state()))
    decision = result["decision"]

    assert decision["verdict"] == "approved"
    assert decision["payoutAmount"] == 4000.0
    assert len(decision["citations"]) == 1
    citation = decision["citations"][0]
    assert citation["fact"]
    assert citation["sourceRef"]
    assert citation["customerFriendlyExplanation"]
    assert decision["emailSent"] is False
    # Round-trip: the stored dict revalidates against the typed schema.
    assert DecisionResult.model_validate(decision).verdict == "approved"
    json.dumps(result)  # SSE-serializable


def test_decision_verdict_and_payout_are_enforced_in_code(monkeypatch):
    """A model that mismaps the verdict or recomputes the payout is overridden."""
    divergent = _decision_model(verdict="rejected", payoutAmount=9999.0)
    _wire(monkeypatch, ScriptedMessages(divergent))

    result = asyncio.run(decision_agent(_decision_stage_state()))
    decision = result["decision"]
    assert decision["verdict"] == "approved"  # mapped from auto_approve
    assert decision["payoutAmount"] == 4000.0  # echoed from policy stage


def test_decision_escalation_maps_to_under_review(monkeypatch):
    _wire(monkeypatch, ScriptedMessages(_decision_model()))
    state = _decision_stage_state(recommendation="escalate")

    result = asyncio.run(decision_agent(state))
    assert result["decision"]["verdict"] == "under_review"


def test_decision_refusal_escalates_to_template_letter(monkeypatch):
    """Refusal retry exhausted -> deterministic letter, never a crash; the
    fallback cannot auto-finalize (confidence 0.0) and cites risk factors."""
    messages = QueueMessages(
        [
            {"parsed_output": None, "stop_reason": "refusal"},
            {"parsed_output": None, "stop_reason": "refusal"},
        ]
    )
    _wire(monkeypatch, messages)

    state = _decision_stage_state(recommendation="auto_reject")
    state["eligibility"]["riskFactors"] = ["policy expired on the incident date"]
    state["policy"]["adjustedPayout"] = 0.0

    result = asyncio.run(decision_agent(state))
    decision = result["decision"]

    assert decision["verdict"] == "rejected"
    assert decision["payoutAmount"] == 0.0
    assert "unable to approve" in decision["letterBody"]
    assert "appeal within 30 days" in decision["letterBody"]
    assert decision["confidence"] == 0.0
    assert decision["citations"][0]["sourceRef"].startswith("eligibility.riskFactors[")
    assert len(messages.parse_calls) == 2  # refusal retry happened first


def test_decision_jargon_letter_is_regenerated_then_replaced(monkeypatch):
    """Internal machinery must not leak into the customer letter."""
    leaking = _decision_model(
        letterBody="Your risk score was low and no fraud was detected in the claim assessment.",
    )
    messages = QueueMessages(
        [
            {"parsed_output": leaking, "stop_reason": "end_turn"},
            {"parsed_output": leaking, "stop_reason": "end_turn"},
        ]
    )
    _wire(monkeypatch, messages)

    result = asyncio.run(decision_agent(_decision_stage_state()))
    decision = result["decision"]

    assert "risk" not in decision["letterBody"].lower()
    assert "fraud" not in decision["letterBody"].lower()
    assert decision["confidence"] == 0.0  # template letter took over
    assert len(messages.parse_calls) == 2


def test_decision_requires_all_upstream_stages(monkeypatch):
    _wire(monkeypatch, ScriptedMessages(_decision_model()))
    state = _decision_stage_state()
    state["eligibility"] = {}  # upstream stage missing entirely
    with pytest.raises(MissingStageInputError):
        asyncio.run(decision_agent(state))
