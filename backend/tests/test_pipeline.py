"""Integration tests for the durable pipeline worker (spec AC-3/AC-5/AC-6).

Covers the full run over a mocked adapter, resume-from-checkpoint, both STP
gate branches, Last-Event-ID replay through the HTTP stream, a two-poller
race proving exactly one execution per claim, graceful shutdown requeue, and
the sanitized agent-failure path. Mongo access goes through the shared
mongomock_motor fixture in backend/conftest.py.
"""

import asyncio
import json

import pytest

import agents
import database
from agents import (
    DecisionOutput,
    DocumentOutput,
    EligibilityOutput,
    ExtractedFacts,
    IntakeOutput,
    LLMAdapter,
    NormalizedData,
    PolicyOutput,
)
from app.events import emit_event, get_claim_events
from app.usage import UsageLogger
from pipeline import (
    RUN_AUTO_APPROVED,
    RUN_ESCALATED,
    RUN_FAILED,
    RUN_HALTED,
    RUN_QUEUED,
    PipelineRunner,
    claim_next_run,
    enqueue_claim_run,
)

pytestmark = pytest.mark.asyncio

POLICY_NUMBER = "AUTO-2024-001847"

SUBMISSION = {
    "policyNumber": POLICY_NUMBER,
    "holderName": "Sarah Chen",
    "incidentDate": "2026-09-01",
    "incidentType": "theft",
    "claimedAmount": 1200.0,
    "description": "Parked car broken into; stereo and tools stolen overnight.",
    "contactEmail": "",
    "documentText": "Police report #22-1187 filed 2026-09-02; rear window pried open.",
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

ALL_STAGES = {
    "INTAKE_AGENT",
    "POLICY_AGENT",
    "DOCUMENT_AGENT",
    "ELIGIBILITY_AGENT",
    "DECISION_AGENT",
}


def _intake(valid=True):
    return IntakeOutput(
        valid=valid,
        normalizedData=NormalizedData(
            policyNumber=POLICY_NUMBER,
            incidentDate="2026-09-01",
            incidentType="theft",
            claimedAmount=1200.0,
            description="Parked car broken into; stereo and tools stolen overnight.",
        ),
        reasoning="ok",
    )


def _halted_intake():
    """Model-approved output whose incident date is objectively in the future —
    only the deterministic verdict can (and must) halt the run on it."""
    return IntakeOutput(
        valid=True,
        normalizedData=NormalizedData(
            policyNumber=POLICY_NUMBER,
            incidentDate="2999-01-01",
            incidentType="theft",
            claimedAmount=1200.0,
            description="Parked car broken into; stereo and tools stolen overnight.",
        ),
        reasoning="ok",
    )


CLEAN_OUTPUTS = {
    IntakeOutput: _intake(),
    PolicyOutput: PolicyOutput(
        found=True,
        statusCheck="ACTIVE",
        coverageCheck="COVERED",
        withinLimits=True,
        adjustedPayout=700.0,
        deductibleApplied=500.0,
        reasoning="clean",
    ),
    DocumentOutput: DocumentOutput(
        extracted=ExtractedFacts(),
        consistencyScore=95,
        redFlags=[],
        supportingEvidence=["police report #22-1187"],
        reasoning="consistent",
    ),
    EligibilityOutput: EligibilityOutput(
        eligible=True,
        riskScore=12,
        riskFactors=[],
        fraudIndicators=[],
        recommendation="auto_approve",
        reasoning="low risk",
    ),
    DecisionOutput: DecisionOutput(
        verdict="auto_approve",
        payoutAmount=700.0,
        letterSubject="Your claim decision",
        letterBody="Approved.",
        reasoning="confident",
        confidence=0.93,
    ),
}


class _FakeUsage:
    input_tokens = 11
    output_tokens = 42


class _ParsedMessage:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.stop_reason = "end_turn"
        self.usage = _FakeUsage()


class PerAgentMessages:
    """Fake messages API: one canned parsed output per agent schema class."""

    def __init__(self, by_schema):
        self.by_schema = by_schema
        self.parse_calls = []

    async def parse(self, **kwargs):
        schema = kwargs["output_format"]
        self.parse_calls.append(schema.__name__)
        return _ParsedMessage(self.by_schema[schema])


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def _install_adapter(monkeypatch, by_schema):
    fake = LLMAdapter(
        client=_FakeClient(PerAgentMessages(by_schema)), usage_logger=UsageLogger()
    )
    monkeypatch.setattr(agents, "adapter", fake)
    return fake


async def _seed_policy():
    await database.policies_col.insert_one(dict(POLICY_DOC))


async def test_full_run_checkpoints_usage_and_finalizes(monkeypatch, patched_mongo):
    """A complete run persists stage checkpoints, usage rollup, and STP trace."""
    await _seed_policy()
    _install_adapter(monkeypatch, CLEAN_OUTPUTS)
    await enqueue_claim_run("CLM-FULL-1", dict(SUBMISSION))
    claimed = await claim_next_run("worker-a")
    assert claimed is not None

    status = await PipelineRunner(claimed).run()

    assert status == RUN_AUTO_APPROVED
    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-FULL-1"})
    assert run_doc["status"] == RUN_AUTO_APPROVED
    assert set(run_doc["stages"].keys()) == ALL_STAGES
    assert all(cp["status"] == "done" for cp in run_doc["stages"].values())
    assert run_doc["stp"]["decision"] == "auto_approved"

    claim = await database.claims_col.find_one({"id": "CLM-FULL-1"})
    assert claim is not None
    assert claim["status"] == "auto_approved"
    assert claim["agent_trace"]["decision"]["verdict"] == "auto_approve"
    # Upsert, not insert: exactly one claim row despite checkpoint re-saves.
    assert await database.claims_col.count_documents({"id": "CLM-FULL-1"}) == 1

    # Per-claim usage rollup: five LLM calls at 11 in / 42 out each.
    assert run_doc["usage"]["total_calls"] == 5
    assert run_doc["usage"]["input_tokens"] == 55
    assert run_doc["usage"]["output_tokens"] == 210
    assert claim["usage"]["total_calls"] == 5

    # The durable stream holds every stage plus the gate outcome, ending
    # with the runner's bookkeeping event.
    events = await get_claim_events("CLM-FULL-1")
    names = [e["event"] for e in events]
    assert names.count("agent_start") == 5
    assert names.count("agent_complete") == 5
    assert "stp_finalized" in names
    assert names[-1] == "run_finalized"


async def test_stp_escalates_low_confidence_with_reason(monkeypatch, patched_mongo):
    """A failing gate leg escalates and names the reason."""
    await _seed_policy()
    outputs = dict(CLEAN_OUTPUTS)
    outputs[DecisionOutput] = DecisionOutput(
        verdict="auto_approve",
        payoutAmount=700.0,
        letterSubject="Your claim decision",
        letterBody="Under review.",
        reasoning="not sure",
        confidence=0.40,
    )
    _install_adapter(monkeypatch, outputs)
    await enqueue_claim_run("CLM-ESC-1", dict(SUBMISSION))
    claimed = await claim_next_run("worker-a")

    status = await PipelineRunner(claimed).run()

    assert status == RUN_ESCALATED
    claim = await database.claims_col.find_one({"id": "CLM-ESC-1"})
    assert claim["status"] == "escalated"
    assert "below threshold" in claim["escalation_reason"]
    assert claim["stp"]["decision"] == "escalated"

    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-ESC-1"})
    assert run_doc["status"] == RUN_ESCALATED
    assert run_doc["escalation_reason"] == claim["escalation_reason"]
    names = [e["event"] for e in await get_claim_events("CLM-ESC-1")]
    assert "stp_escalated" in names


async def test_rerun_resumes_from_checkpoints(monkeypatch, patched_mongo):
    """An interrupted run's checkpoints are reused, not re-executed."""
    await _seed_policy()
    adapter = _install_adapter(monkeypatch, CLEAN_OUTPUTS)
    messages = adapter.client.messages
    await enqueue_claim_run("CLM-RES-1", dict(SUBMISSION))

    claimed = await claim_next_run("worker-a")
    runner = PipelineRunner(
        claimed, should_stop=lambda: len(messages.parse_calls) >= 3
    )
    status = await runner.run()

    # Cooperative stop after three stages: requeued, checkpoints persisted.
    assert status == RUN_QUEUED
    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-RES-1"})
    assert run_doc["status"] == RUN_QUEUED
    assert len(run_doc["stages"]) == 3

    # Re-submitting while the run is in flight is idempotent: the same
    # attempt-1 document is handed back, never a duplicate attempt.
    requeued = await enqueue_claim_run("CLM-RES-1", dict(SUBMISSION))
    assert requeued["attempt"] == 1
    resumed = await claim_next_run("worker-b")
    assert resumed["attempt"] == 1
    assert len(resumed["stages"]) == 3

    status2 = await PipelineRunner(resumed).run()

    assert status2 == RUN_AUTO_APPROVED
    # Only ELIGIBILITY and DECISION ran again; nothing was re-executed.
    assert len(messages.parse_calls) == 5
    assert messages.parse_calls[-2:] == ["EligibilityOutput", "DecisionOutput"]

    claim = await database.claims_col.find_one({"id": "CLM-RES-1"})
    assert claim["status"] == "auto_approved"
    # Trace rebuilt from checkpoints: all five stages present.
    assert len(claim["agent_logs"]) == 5


async def test_resubmission_after_terminal_run_seeds_next_attempt(
    monkeypatch, patched_mongo
):
    """A terminal run's checkpoints seed attempt N+1's stage map."""
    outputs = dict(CLEAN_OUTPUTS)
    outputs[IntakeOutput] = _halted_intake()  # halts: terminal fast
    _install_adapter(monkeypatch, outputs)
    await _seed_policy()
    await enqueue_claim_run("CLM-ATT-1", dict(SUBMISSION))
    claimed = await claim_next_run("worker-a")
    assert await PipelineRunner(claimed).run() == RUN_HALTED

    second = await enqueue_claim_run("CLM-ATT-1", dict(SUBMISSION))
    assert second["attempt"] == 2
    # Attempt 2 starts with attempt 1's finished checkpoints attached.
    assert set(second["stages"].keys()) == {"INTAKE_AGENT"}


async def test_invalid_intake_halts_as_pending(monkeypatch, patched_mongo):
    """An objectively invalid intake (future incident date, code-decided) halts
    the run even when the LLM approved it."""
    await _seed_policy()
    outputs = dict(CLEAN_OUTPUTS)
    outputs[IntakeOutput] = _halted_intake()
    _install_adapter(monkeypatch, outputs)
    await enqueue_claim_run("CLM-HALT-1", dict(SUBMISSION))
    claimed = await claim_next_run("worker-a")

    status = await PipelineRunner(claimed).run()

    assert status == RUN_HALTED
    claim = await database.claims_col.find_one({"id": "CLM-HALT-1"})
    assert claim["status"] == "pending"
    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-HALT-1"})
    assert run_doc["status"] == RUN_HALTED
    # Halted before the policy lookup: only intake ran, no STP decision.
    assert set(run_doc["stages"].keys()) == {"INTAKE_AGENT"}
    assert "stp" not in run_doc


async def test_agent_failure_persists_sanitized_failed_claim(monkeypatch, patched_mongo):
    await _seed_policy()
    _install_adapter(monkeypatch, CLEAN_OUTPUTS)
    await enqueue_claim_run("CLM-FAIL-1", dict(SUBMISSION))

    original = agents.PIPELINE_STAGES[2]["fn"]  # DOCUMENT_AGENT

    async def exploding(state):
        raise RuntimeError("secret internals")

    agents.PIPELINE_STAGES[2]["fn"] = exploding
    try:
        claimed = await claim_next_run("worker-a")
        status = await PipelineRunner(claimed).run()
    finally:
        agents.PIPELINE_STAGES[2]["fn"] = original

    assert status == RUN_FAILED
    claim = await database.claims_col.find_one({"id": "CLM-FAIL-1"})
    assert claim["status"] == "failed"
    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-FAIL-1"})
    assert run_doc["status"] == RUN_FAILED
    # Raw exception text never reaches the durable stream.
    serialized = json.dumps(await get_claim_events("CLM-FAIL-1"))
    assert "secret internals" not in serialized
    assert "Internal pipeline error" in serialized


async def test_last_event_id_replay(patched_mongo):
    """The SSE stream replays strictly after Last-Event-ID and ends terminal."""
    for i in range(1, 5):
        await emit_event("CLM-SSE-1", {"event": f"evt_{i}", "n": i})
    await emit_event("CLM-SSE-1", {"event": "run_finalized", "status": "auto_approved"})

    import server as server_mod
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=server_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/events/streams/CLM-SSE-1",
            headers={"Last-Event-ID": "2"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    # Sequences 3..5 replayed; 1 and 2 (already seen) did not.
    assert "id: 3" in body and "id: 5" in body
    assert "id: 1" not in body and "id: 2" not in body
    assert '"event": "run_finalized"' in body
    assert '"event": "evt_1"' not in body
    # The terminal event closes the stream by itself.
    assert "id: 5" == body.strip().splitlines()[-2]


async def test_two_pollers_execute_each_claim_exactly_once(monkeypatch, patched_mongo):
    """Concurrent pollers never double-run a claim; every run finalizes once."""
    await _seed_policy()
    _install_adapter(monkeypatch, CLEAN_OUTPUTS)
    claim_ids = [f"CLM-RACE-{i}" for i in range(3)]
    for cid in claim_ids:
        await enqueue_claim_run(cid, dict(SUBMISSION))

    async def drain(worker_id, executed):
        while True:
            doc = await claim_next_run(worker_id)
            if doc is None:
                return
            executed.append(doc["claim_id"])
            await PipelineRunner(doc).run()

    w1, w2 = [], []
    await asyncio.gather(drain("worker-1", w1), drain("worker-2", w2))

    # Every claim was executed, and no claim was executed twice.
    assert sorted(w1 + w2) == sorted(claim_ids)
    docs = await database.claim_runs_col.find({}).to_list(10)
    assert len(docs) == 3
    assert all(d["status"] == RUN_AUTO_APPROVED for d in docs)
    for cid in claim_ids:
        streams = [e for e in await get_claim_events(cid) if e["event"] == "run_finalized"]
        assert len(streams) == 1


async def test_shutdown_before_first_stage_requeues_intact(patched_mongo):
    """A stop signal before any stage requeues the run without side effects."""
    await _seed_policy()
    await enqueue_claim_run("CLM-STOP-1", dict(SUBMISSION))
    claimed = await claim_next_run("worker-s")
    assert claimed is not None

    runner = PipelineRunner(claimed, should_stop=lambda: True)
    status = await runner.run()

    assert status == RUN_QUEUED
    run_doc = await database.claim_runs_col.find_one({"claim_id": "CLM-STOP-1"})
    assert run_doc["status"] == RUN_QUEUED
    assert run_doc["stages"] == {}
    assert await database.claims_col.count_documents({"id": "CLM-STOP-1"}) == 0


async def test_worker_loop_honors_stop_signal(patched_mongo):
    """run_forever returns promptly when the stop flag is already set."""
    from worker import PipelineWorker

    worker = PipelineWorker(worker_id="w-stop", poll_interval=0.01)
    worker.request_stop()

    await asyncio.wait_for(worker.run_forever(), timeout=2.0)
    # No queued runs were claimed during the aborted loop.
    assert await database.claim_runs_col.count_documents({"status": "running"}) == 0
