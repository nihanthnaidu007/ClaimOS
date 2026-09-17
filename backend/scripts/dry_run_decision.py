#!/usr/bin/env python3
"""Dry-run the Decision agent against a mocked Anthropic client.

Proves the rewired Decision agent emits a valid Pydantic output — no live API
key, no MongoDB. Exit code 0 = valid output; 1 = failure.

Usage: cd backend && python3 scripts/dry_run_decision.py
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
from agents import DecisionOutput, decision_agent
from app.llm.adapter import LLMAdapter


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


class ScriptedMessages:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.parse_calls = []

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return FakeParsedMessage(self.parsed_output)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


class NoopUsage:
    async def log(self, **kwargs):
        pass


CANNED_DECISION = DecisionOutput(
    verdict="approved",
    payoutAmount=4000.0,
    letterSubject="Your Claim CLM-DRY-1 Has Been Approved",
    letterBody=(
        "Dear Sarah Chen, we are pleased to inform you that your claim "
        "CLM-DRY-1 for the September 10 incident has been approved. The "
        "adjusted payout of $4,000.00 (claimed $4,500.00 less your $500.00 "
        "deductible) will be issued within 5-7 business days. Thank you for "
        "your prompt documentation, including the police report. "
        "Sincerely, ClaimOS Claims Processing Team"
    ),
    nextSteps=[
        "Payout issued within 5-7 business days",
        "Reply in writing within 30 days to appeal",
    ],
    reasoning="Policy active, incident covered, risk score 8 — auto_approve.",
)

STATE = {
    "claimId": "CLM-DRY-1",
    "input": {"policyNumber": "AUTO-2024-001847"},
    "intake": {
        "valid": True,
        "normalizedData": {
            "policyNumber": "AUTO-2024-001847",
            "incidentDate": "2026-09-10",
            "incidentType": "accident",
            "claimedAmount": 4500.0,
            "description": "Rear-end collision on I-35 with a police report filed.",
        },
        "missingFields": [],
        "validationNotes": "All fields present and valid.",
        "reasoning": "Required fields present; date normalized; amount valid.",
    },
    "policy": {
        "found": True,
        "statusCheck": "ACTIVE — policy covers incident date",
        "coverageCheck": "COVERED — accident in covered_events",
        "withinLimits": True,
        "adjustedPayout": 4000.0,
        "deductibleApplied": 500.0,
        "claimFrequencyFlag": False,
        "reasoning": "Verification PASSED.",
        "policyData": {
            "policy_number": "AUTO-2024-001847",
            "holder_name": "Sarah Chen",
            "policy_type": "auto",
            "status": "active",
            "coverage_limit": 50000.0,
            "deductible": 500.0,
        },
    },
    "documents": {
        "extracted": {
            "datesFound": ["2026-09-10"],
            "locationMentioned": "I-35",
            "partiesInvolved": "Sarah Chen; other driver",
            "damageDescribed": "Rear bumper damage",
            "amountsMentioned": [4500.0],
        },
        "consistencyScore": 92,
        "redFlags": [],
        "supportingEvidence": ["Police report filed on scene"],
        "reasoning": "Facts match submission; consistencyScore 92/100.",
    },
    "eligibility": {
        "eligible": True,
        "riskScore": 8,
        "riskFactors": [],
        "fraudIndicators": [],
        "recommendation": "auto_approve",
        "reasoning": "TOTAL RISK SCORE: 8/100. RECOMMENDATION: auto_approve.",
    },
    "decision": {},
    "agentLogs": [],
}


def main() -> int:
    scripted = ScriptedMessages(CANNED_DECISION)
    agents.adapter = LLMAdapter(client=FakeClient(scripted), usage_logger=NoopUsage())

    state = asyncio.run(decision_agent(STATE))
    emitted = state["decision"]

    # The SSE layer json.dumps agent outputs — they must be plain dicts.
    json.dumps(emitted)
    # Round-trip: the emitted dict revalidates against the Pydantic model.
    revalidated = DecisionOutput.model_validate({
        k: v for k, v in emitted.items() if k != "emailSent"
    })

    assert emitted["verdict"] == "approved"
    assert emitted["payoutAmount"] == 4000.0
    assert emitted["emailSent"] is False
    assert scripted.parse_calls[0]["model"] == "claude-sonnet-5"
    assert scripted.parse_calls[0]["output_format"] is DecisionOutput

    print(json.dumps(emitted, indent=2))
    print(
        "\nOK: Decision agent emitted a valid Pydantic output "
        f"({revalidated.__class__.__name__}, verdict={revalidated.verdict})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
