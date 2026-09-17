"""Ops end-to-end test: a duplicate claim runs the full pipeline and lands
fraud flags, the incident fingerprint, and the risk bump on the saved claim.

Route-level pieces are covered in test_fraud.py (agent in isolation) and
test_settlement.py (endpoint); this ties the fraud stage into the durable
pipeline the way ops will actually see it.
"""

import pytest

import agents
import database
from app.fraud import CODE_DUPLICATE_INCIDENT
from app.usage import UsageLogger
from pipeline import PipelineRunner, enqueue_claim_run, claim_next_run
# backend/tests has no __init__.py: pytest imports these files as top-level
# modules with backend/tests on sys.path, so the sibling fixture module is
# importable directly — importing it as tests.test_pipeline would resolve to
# the root tests/ package and break the combined "pytest tests/ backend/tests/" run.
from test_pipeline import (
    CLEAN_OUTPUTS,
    PerAgentMessages,
    SUBMISSION,
    _FakeClient,
    _ParsedMessage,
    _seed_policy,
)

pytestmark = pytest.mark.asyncio


class _RecordingMessages(PerAgentMessages):
    """PerAgentMessages plus a schema-call log for exact LLM-call assertions."""

    def __init__(self, by_schema):
        super().__init__(by_schema)
        self.schemas = []

    async def parse(self, **kwargs):
        self.schemas.append(kwargs["output_format"].__name__)
        return _ParsedMessage(self.by_schema[kwargs["output_format"]])


def _install_recording_adapter(monkeypatch, by_schema):
    messages = _RecordingMessages(by_schema)
    fake = agents.LLMAdapter(client=_FakeClient(messages), usage_logger=UsageLogger())
    monkeypatch.setattr(agents, "adapter", fake)
    return messages


async def test_duplicate_claim_persists_fraud_outputs_and_risk_bump(
    monkeypatch, patched_mongo
):
    await _seed_policy()
    # A prior incident with the same fingerprint fields as the new submission.
    await patched_mongo.claims.insert_one(
        {
            "id": "CLM-PRIOR-1",
            "policy_number": SUBMISSION["policyNumber"],
            "incident_date": SUBMISSION["incidentDate"],
            "incident_type": SUBMISSION["incidentType"],
            "claimed_amount": 1100.0,
            "status": "approved",
        }
    )

    by_schema = dict(CLEAN_OUTPUTS)
    # The seeded duplicate makes the fraud agent the sixth LLM caller.
    by_schema[agents.FraudSimilarityOutput] = agents.FraudSimilarityOutput(
        similar=True,
        confidence=0.9,
        reasoning="Same policy, date, and incident type as CLM-PRIOR-1.",
        cited_evidence=[
            {
                "from_claim_id": "CLM-PRIOR-1",
                "field": "incident_date",
                "value": SUBMISSION["incidentDate"],
            }
        ],
    )
    messages = _install_recording_adapter(monkeypatch, by_schema)

    await enqueue_claim_run("CLM-FRAUD-E2E", dict(SUBMISSION))
    claimed = await claim_next_run("worker-a")
    assert claimed is not None
    await PipelineRunner(claimed).run()

    claim = await database.claims_col.find_one({"id": "CLM-FRAUD-E2E"})
    assert claim is not None

    # Fraud stage ran and cited evidence came back from the record fields.
    assert [s for s in messages.schemas if s == "FraudSimilarityOutput"] == [
        "FraudSimilarityOutput"
    ]

    # Flags, fingerprint, and the severity-tagged flag shape persisted.
    assert claim["incident_fingerprint"], "fingerprint must be stored for future matches"
    assert [f["code"] for f in claim["fraud_flags"]] == [CODE_DUPLICATE_INCIDENT]
    assert claim["fraud_flags"][0]["severity"] == "high"
    assert claim["fraud_flags"][0]["evidence"]["duplicate_of_claim_id"] == "CLM-PRIOR-1"

    # The fraud trace is part of the glass-box record.
    assert claim["agent_trace"]["fraud"]["fingerprint"] == claim["incident_fingerprint"]

    # Risk score includes the +20 high-severity bump over the clean baseline.
    assert claim["risk_score"] >= 20

    # The stage log carries the new agent entry.
    assert any(log["agent"] == "FRAUD_AGENT" for log in claim["agent_logs"])
