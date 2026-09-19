"""F6 decision-transparency projection tests: the deny-by-default keystone.

The regression in this file (new internal trace field never reaches the
customer projection) is the spec's security keystone — it also guards F11
notes and any future internal field. It must never be weakened to make a
feature pass.

Covers: allowlist fidelity, stage copy source, citation flow, in-flight
shape, and drift between PORTAL_STAGE_COPY and the pipeline stages.
"""

from app.portal_projection import (
    CUSTOMER_VISIBLE_TRACE_FIELDS,
    PORTAL_STAGE_COPY,
    CustomerClaimProjection,
    build_customer_projection,
)

# Internal markers the projection must never contain, in any feature state.
_INTERNAL_MARKERS = [
    "AUTO-2024-001847",  # policy number — masked since the portal shipped
    "normalizedData",
    "adjustedPayout",
    "deductibleApplied",
    "withinLimits",
    "citedFields",
    "consistencyScore",
    "redFlags",
    "supportingEvidence",
    "riskScore",
    "riskFactors",
    "riskFactorDetails",
    "recommendation",
    "inputGaps",
    "fingerprint",
    "similarity",
    "letterBody",
    "letterSubject",
    "payoutAmount",
    "confidence",
    "emailSent",
    "nextSteps",
    "model_metadata",
    "segment_risk_band",
    "INTERNAL-BAND-77",
    "adjuster_notes",
    "internal_notes",
]


def _decided_claim() -> tuple[dict, dict]:
    """A claim row + trace shaped exactly like the pipeline saves them —
    including every internal field the customer must never see."""
    claim = {
        "id": "CLM-20260918-001",
        "status": "auto_approved",
        "holder_name": "Sarah Chen",
        "agent_logs": [
            {"agent": "INTAKE_AGENT", "status": "done", "startTime": "t0", "endTime": "t1"},
            {"agent": "POLICY_AGENT", "status": "done", "startTime": "t1", "endTime": "t2"},
            {"agent": "DOCUMENT_AGENT", "status": "done", "startTime": "t2", "endTime": "t3"},
            {"agent": "FRAUD_AGENT", "status": "done", "startTime": "t3", "endTime": "t4"},
            {"agent": "ELIGIBILITY_AGENT", "status": "done", "startTime": "t4", "endTime": "t5"},
            {"agent": "DECISION_AGENT", "status": "done", "startTime": "t5", "endTime": "t6"},
        ],
    }
    trace = {
        "intake": {
            "valid": True,
            "normalizedData": {
                "policyNumber": "AUTO-2024-001847",
                "claimedAmount": 1200.0,
                "incidentDate": "2026-09-01",
                "incidentType": "theft",
            },
            "flags": [{"code": "high_amount", "detail": "internal detail"}],
            "summary": "Normalized the submission.",
        },
        "policy": {
            "found": True,
            "status": "active",
            "citedFields": ["end_date=2027-01-01", "coverage_limit=50000"],
            "withinLimits": True,
            "adjustedPayout": 380.0,
            "deductibleApplied": 400.0,
            "claimFrequencyFlag": False,
            "priorClaims12mo": 1,
            "summary": "Policy active and incident covered.",
        },
        "documents": {
            "consistency": "consistent",
            "consistencyScore": 1.0,
            "redFlags": [{"code": "amount_mismatch", "excerpt": "internal excerpt"}],
            "supportingEvidence": [{"excerpt": "internal excerpt"}],
            "summary": "Documents consistent.",
        },
        "fraud": {
            "fingerprint": "fp-internal-abc",
            "flags": [{"code": "high_amount", "severity": "low"}],
            "similarity": {"verdict": "coincidence", "confidence": 0.9},
        },
        "eligibility": {
            "riskScore": 12,
            "riskFactors": ["clean_claim_base"],
            "riskFactorDetails": [{"code": "base", "points": 5}],
            "recommendation": "auto_approve",
            "eligible": True,
            "inputGaps": [],
            "summary": "Low risk assessment.",
        },
        "decision": {
            "verdict": "approved",
            "payoutAmount": 380.0,
            "letterSubject": "Your claim has been approved",
            "letterBody": "Dear Sarah, we are pleased to inform you...",
            "nextSteps": ["Nothing further is needed from you."],
            "citations": [
                {
                    "fact": "Policy active on the incident date",
                    "sourceRef": "policy.status=active",
                    "customerFriendlyExplanation": (
                        "Your policy was active when the incident happened."
                    ),
                }
            ],
            "summary": "Approved: covered incident within limits.",
            "confidence": 0.93,
            "emailSent": False,
        },
    }
    claim["agent_trace"] = trace
    return claim, trace


# ============ AC-6.1 — projection built only from the allowlist ============


def test_allowlist_matches_the_locked_spec():
    """The keystone's allowlist is exactly the spec's six fields. Adding a
    field must consciously update this test — that friction is the point."""
    assert CUSTOMER_VISIBLE_TRACE_FIELDS == frozenset(
        {"stage", "title", "summary", "status", "citations", "decided_at"}
    )


def test_decided_claim_projection_exposes_stage_summaries_and_decision():
    claim, trace = _decided_claim()
    projection = build_customer_projection(claim, trace)

    assert projection.claim_number == "CLM-20260918-001"
    assert projection.status == "auto_approved"
    assert projection.current_stage is None  # decided: nothing in flight
    # All six stages, in pipeline order, all completed.
    assert [s.stage for s in projection.stage_summaries] == [
        "intake",
        "policy",
        "documents",
        "fraud",
        "eligibility",
        "decision",
    ]
    assert all(s.status == "completed" for s in projection.stage_summaries)
    assert projection.decision is not None
    assert projection.decision.summary == "Approved: covered incident within limits."
    assert projection.decision.decidedAt == "t6"
    assert projection.decision.citations[0].customerFriendlyExplanation == (
        "Your policy was active when the incident happened."
    )


def test_deny_by_default_new_internal_trace_field_is_never_projected():
    """THE deny-by-default regression (spec keystone; also guards F11 notes).

    Tomorrow's feature adds a new internal field to an internal trace — it
    must not appear in the customer projection. If a feature fails because
    of this test, the feature is leaking; fix the feature, never this test.
    """
    claim, trace = _decided_claim()
    trace["policy"]["segment_risk_band"] = "INTERNAL-BAND-77"
    trace["decision"]["model_metadata"] = {"model": "internal-model-x", "cost_usd": 0.42}
    trace["fraud"]["threshold_config"] = "SECRET-THRESHOLDS"
    claim["agent_logs"][0]["escalation_hint"] = "INTERNAL-HINT-9"

    projection = build_customer_projection(claim, trace)
    raw = projection.model_dump_json()
    for marker in (
        "INTERNAL-BAND-77",
        "internal-model-x",
        "0.42",
        "SECRET-THRESHOLDS",
        "INTERNAL-HINT-9",
        "segment_risk_band",
        "model_metadata",
        "threshold_config",
        "escalation_hint",
    ):
        assert marker not in raw, f"internal marker leaked: {marker}"


def test_known_internal_trace_fields_never_reach_the_projection():
    claim, trace = _decided_claim()
    projection = build_customer_projection(claim, trace)
    raw = projection.model_dump_json()
    for marker in _INTERNAL_MARKERS:
        assert marker not in raw, f"internal marker leaked: {marker}"


def test_f11_notes_in_trace_stay_invisible():
    """Cross-feature guard (AC-11.2): note-shaped fields placed in the trace
    payload have no path into the customer projection."""
    claim, trace = _decided_claim()
    trace["decision"]["adjuster_notes"] = [{"body": "internal note body"}]
    trace["eligibility"]["internal_notes"] = "customer seems evasive"
    trace["intake"]["notes"] = ["note-a"]

    projection = build_customer_projection(claim, trace)
    raw = projection.model_dump_json()
    assert "internal note body" not in raw
    assert "customer seems evasive" not in raw
    assert "note-a" not in raw
    assert "notes" not in raw  # the key itself never appears


def test_stage_summaries_use_portal_stage_copy_not_agent_text():
    claim, trace = _decided_claim()  # every stage carries an agent summary
    projection = build_customer_projection(claim, trace)

    by_stage = {s.stage: s for s in projection.stage_summaries}
    assert by_stage["policy"].title == PORTAL_STAGE_COPY["POLICY_AGENT"]["title"]
    assert by_stage["policy"].summary == PORTAL_STAGE_COPY["POLICY_AGENT"]["summary"]
    # The agent's own summary text never reaches the customer.
    assert "Policy active and incident covered." not in projection.model_dump_json()


def test_citations_flow_only_from_the_allowlisted_field():
    claim, trace = _decided_claim()
    projection = build_customer_projection(claim, trace)

    by_stage = {s.stage: s for s in projection.stage_summaries}
    # The policy agent stores clause refs under `citedFields` — not the
    # allowlisted key — so none of them reach the customer projection.
    assert by_stage["policy"].citations == []
    # The decision agent stores `citations` (allowlisted) — those flow.
    assert by_stage["decision"].citations
    assert projection.decision.citations == by_stage["decision"].citations


def test_malformed_citations_are_sanitized_not_stringified():
    claim, trace = _decided_claim()
    trace["decision"]["citations"] = [
        {"fact": 42, "sourceRef": None, "customerFriendlyExplanation": {"nested": "x"}},
        "garbage",
        7,
    ]

    projection = build_customer_projection(claim, trace)
    citations = projection.decision.citations
    assert len(citations) == 1  # non-dict entries dropped
    assert citations[0].fact == ""  # non-strings refused, never coerced
    assert citations[0].sourceRef == ""  # non-strings coerce to empty
    assert citations[0].customerFriendlyExplanation == ""  # dicts are not stringified


# ============ In-flight and failure states ============


def test_inflight_claim_has_no_decision_block():
    claim, _ = _decided_claim()
    claim["status"] = "pending"
    claim["agent_logs"] = [
        {"agent": "INTAKE_AGENT", "status": "done", "startTime": "t0", "endTime": "t1"},
        {"agent": "POLICY_AGENT", "status": "done", "startTime": "t1", "endTime": "t2"},
        {"agent": "DOCUMENT_AGENT", "status": "running", "startTime": "t2"},
    ]
    trace = {"intake": {"valid": True}, "policy": {"found": True}}

    projection = build_customer_projection(claim, trace)
    assert projection.decision is None
    # Only stages that produced something appear — nothing invented.
    assert [s.stage for s in projection.stage_summaries] == ["intake", "policy"]
    assert projection.current_stage == PORTAL_STAGE_COPY["DOCUMENT_AGENT"]["title"]


def test_failed_stage_reports_failed_status():
    claim, _ = _decided_claim()
    claim["status"] = "failed"
    claim["agent_logs"] = [
        {"agent": "INTAKE_AGENT", "status": "done", "startTime": "t0", "endTime": "t1"},
        {"agent": "DOCUMENT_AGENT", "status": "error", "startTime": "t2", "endTime": "t3"},
    ]
    trace = {"intake": {"valid": True}, "documents": {}}

    projection = build_customer_projection(claim, trace)
    by_stage = {s.stage: s for s in projection.stage_summaries}
    assert by_stage["documents"].status == "failed"
    assert projection.decision is None


def test_empty_trace_projects_bare_claim():
    """Seeded/legacy rows store agent_trace: {} — the projection stays bare."""
    claim = {"id": "CLM-20260918-002", "status": "pending", "agent_trace": {}}
    projection = build_customer_projection(claim, claim["agent_trace"])
    assert projection == CustomerClaimProjection(
        claim_number="CLM-20260918-002",
        status="pending",
        current_stage=None,
        decision=None,
        stage_summaries=[],
    )


# ============ Copy-constant drift ============


def test_portal_stage_copy_tracks_the_pipeline():
    """PORTAL_STAGE_COPY covers exactly the six PIPELINE_STAGES plus the
    decided/reopened portal states — a new pipeline stage must consciously
    decide whether (and how) it is explained to customers."""
    from agents import PIPELINE_STAGES

    from app.portal_projection import _STAGE_AGENT_BY_KEY

    stage_names = [stage["name"] for stage in PIPELINE_STAGES]
    assert list(_STAGE_AGENT_BY_KEY.values()) == stage_names
    for name in stage_names:
        copy = PORTAL_STAGE_COPY[name]
        assert copy["title"], f"{name} has no customer title"
        assert copy["summary"], f"{name} has no customer summary"
    # Portal states served alongside the pipeline stages — "failed" is F2's
    # next-steps copy for runs that errored out.
    assert "decided" in PORTAL_STAGE_COPY
    assert "reopened" in PORTAL_STAGE_COPY
    assert "failed" in PORTAL_STAGE_COPY
