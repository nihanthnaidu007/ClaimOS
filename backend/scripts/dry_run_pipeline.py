#!/usr/bin/env python3
"""Dry-run the whole six-agent pipeline against a mocked Anthropic client.

Verification harness for the prompt-tuning brief: with a mocked client (no API
key) and mongomock (no MongoDB), the pipeline emits every stage output — the
five LLM stages revalidate against typed schemas in app/llm/schemas.py and the
deterministic fraud stage's shape is asserted — and the Decision stage carries
citations[]. Exit code 0 = every output valid; 1 = failure.

Usage: cd backend && python3 scripts/dry_run_pipeline.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on sys.path
# database.py reads these at import time — defaults keep the dry-run offline.
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "claimos_dryrun")

import agents
from agents import PIPELINE_STAGES
from app.llm.adapter import LLMAdapter
from app.llm.schemas import (
    DecisionResult,
    DocumentAnalysis,
    EligibilityResult,
    IntakeResult,
    PolicyVerification,
)

POLICY_NUMBER = "AUTO-2024-001847"

SUBMISSION = {
    "policyNumber": POLICY_NUMBER,
    "holderName": "Sarah Chen",
    "incidentDate": "2026-09-10",
    "incidentType": "Accident",
    "claimedAmount": 4500.0,
    "description": "Rear-end collision on I-35 with a police report filed.",
    "documentText": "Police report #22-1187 filed 2026-09-11; rear bumper damage.",
}

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


class FakeUsage:
    input_tokens = 131
    output_tokens = 289

    def model_dump(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class FakeParsedMessage:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.stop_reason = "end_turn"
        self.usage = FakeUsage()


class PerSchemaMessages:
    """Fake messages API: one canned parsed output per agent schema class."""

    def __init__(self, by_schema):
        self.by_schema = by_schema
        self.parse_calls = []

    async def parse(self, **kwargs):
        schema = kwargs["output_format"]
        self.parse_calls.append(schema.__name__)
        return FakeParsedMessage(self.by_schema[schema])


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


class NoopUsage:
    async def log(self, **kwargs):
        pass


CANNED_OUTPUTS = {
    IntakeResult: IntakeResult(
        valid=True,
        normalizedData={
            "policyNumber": POLICY_NUMBER,
            "incidentDate": "2026-09-10",
            "incidentType": "accident",
            "claimedAmount": 4500.0,
            "description": "Rear-end collision on I-35 with a police report filed.",
        },
        summary="Normalized the submission; no blocking issues.",
    ),
    PolicyVerification: PolicyVerification(
        status="active",
        statusDetail="Policy active on the incident date.",
        coverage="covered",
        coverageDetail="accident is in covered_events.",
        appearsOverLimit=False,
        citedFields=["end_date=2027-01-01", "coverage_limit=50000.0", "deductible=500.0"],
        summary="Active policy covers the incident.",
    ),
    DocumentAnalysis: DocumentAnalysis(
        extracted={
            "datesFound": ["2026-09-10"],
            "locationMentioned": "I-35",
            "partiesInvolved": "Sarah Chen; other driver",
            "damageDescribed": "Rear bumper damage",
            "amountsMentioned": [{"value": 4500.0, "context": "estimated repair cost"}],
        },
        consistency="consistent",
        supportingEvidence=[{"excerpt": "police report #22-1187", "note": "corroborates"}],
        summary="Facts match the submission.",
    ),
    EligibilityResult: EligibilityResult(
        eligible=True,
        recommendation="auto_approve",
        summary="Clean claim within limits.",
    ),
    DecisionResult: DecisionResult(
        verdict="approved",
        payoutAmount=4000.0,
        letterSubject="Your Claim CLM-DRY-1 Has Been Approved",
        letterBody=(
            "Dear Sarah Chen, we are pleased to inform you that your claim "
            "CLM-DRY-1 for the September 10 incident has been approved. The "
            "payout of $4,000.00 (claimed $4,500.00 less your $500.00 "
            "deductible) will be issued within 5-7 business days. "
            "Sincerely, ClaimOS Claims Processing Team"
        ),
        nextSteps=["Payout issued within 5-7 business days"],
        citations=[
            {
                "fact": "Policy active on 2026-09-10 with accident coverage",
                "sourceRef": "policy.citedFields[0]: end_date=2027-01-01",
                "customerFriendlyExplanation": "Your policy was active and covers this incident.",
            }
        ],
        summary="Approved: active coverage, within limits.",
        confidence=0.95,
    ),
}


async def main_async() -> int:
    # mongomock instead of a live MongoDB for the policy/history lookups.
    from mongomock_motor import AsyncMongoMockClient

    mock_db = AsyncMongoMockClient()["claimos_dryrun"]
    agents.policies_col = mock_db.policies
    agents.claims_col = mock_db.claims
    await mock_db.policies.insert_one(dict(POLICY_DOC))

    messages = PerSchemaMessages(CANNED_OUTPUTS)
    agents.adapter = LLMAdapter(client=FakeClient(messages), usage_logger=NoopUsage())

    state = {
        "claimId": "CLM-DRY-1",
        "submittedAt": "dry-run",
        "input": dict(SUBMISSION),
        "intake": {},
        "policy": {},
        "documents": {},
        "eligibility": {},
        "decision": {},
        "agentLogs": [],
    }
    for stage in PIPELINE_STAGES:
        state = await stage["fn"](state)

    # Every stage emitted a plain dict that revalidates against its schema,
    # and the whole state is SSE-serializable.
    json.dumps(state)
    revalidated = {
        "intake": IntakeResult.model_validate(state["intake"]),
        "policy": PolicyVerification.model_validate(
            {k: v for k, v in state["policy"].items()
             if k in PolicyVerification.model_fields}
        ),
        "documents": DocumentAnalysis.model_validate(state["documents"]),
        "eligibility": EligibilityResult.model_validate(
            {k: v for k, v in state["eligibility"].items()
             if k in EligibilityResult.model_fields}
        ),
        "decision": DecisionResult.model_validate(
            {k: v for k, v in state["decision"].items() if k != "emailSent"}
        ),
    }

    assert [name for name in messages.parse_calls] == [
        "IntakeResult",
        "PolicyVerification",
        "DocumentAnalysis",
        "EligibilityResult",
        "DecisionResult",
    ]
    assert revalidated["intake"].valid is True
    assert state["policy"]["found"] is True
    assert state["policy"]["adjustedPayout"] == 4000.0  # computed in code
    assert state["documents"]["consistencyScore"] == 90  # derived in code
    assert state["eligibility"]["riskScore"] == 5  # scored in code
    assert set(state["fraud"]) == {"fingerprint", "flags", "similarity"}
    assert state["fraud"]["similarity"] is None  # no duplicate candidates here
    decision = state["decision"]
    assert decision["verdict"] == "approved"
    assert decision["citations"], "decision output must carry citations[]"
    assert all(
        citation["fact"] and citation["sourceRef"]
        for citation in decision["citations"]
    )

    print(json.dumps({key: state[key] for key in
                      ("intake", "policy", "documents", "fraud", "eligibility", "decision")}, indent=2))
    print(
        "\nOK: pipeline dry-run emitted all six stage outputs; "
        f"decision citations={len(decision['citations'])}; "
        f"LLM calls={len(messages.parse_calls)}"
    )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
