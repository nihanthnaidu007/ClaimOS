"""Public status-portal tests: the access-code credential model.

Covers the spec's AC-8 security posture end to end on mongomock:
valid lookups return the masked payload only (first name + status), wrong
codes, unknown numbers, and codeless legacy claims all return the identical
generic 404 (nothing is enumerable), and the lookup endpoint rate-limits.

Also covers the F2 projection (next-step copy + honest ETA): non-empty copy
for every pipeline stage and terminal state, the deny-by-default allowlist,
and the ETA present/omitted branches.

Covers: valid code, invalid code, rate-limited, non-enumerable errors.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import server
from agents import PIPELINE_STAGES
from app.config import settings
from app.status_portal import (
    PORTAL_STAGE_COPY,
    _PORTAL_STAGE_ORDER,
    access_code_hash,
    generate_access_code,
    public_status_payload,
    status_not_found,
)

ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:8001"]


def _run(coro):
    """Sync tests reach the mock collections through asyncio.run — mongomock
    mirrors motor's async API (insert_one/find_one are coroutines)."""
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", list(ALLOWED_ORIGINS))
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        yield test_client


@pytest.fixture
def limiter_on():
    from app.rate_limit import limiter

    limiter.enabled = True
    limiter._storage.reset()  # private attr: the only way to isolate counters between tests
    yield limiter
    limiter.enabled = False
    limiter._storage.reset()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_claim(
    db,
    claim_id="CLM-20260917-042",
    code=None,
    holder_name="Sarah Chen",
    contact_email="sarah.chen@example.com",
    status="pending",
    agent_trace=None,
    store_plaintext=True,
):
    """Insert one claim row exactly as submission creates it, return the code."""
    code = code or generate_access_code()
    now = _now()
    doc = {
        "id": claim_id,
        "policy_number": "AUTO-2024-001847",
        "claim_date": now,
        "incident_date": "2026-09-01",
        "incident_type": "accident",
        "claimed_amount": 1200.0,
        "status": status,
        "risk_score": 0,
        "decision_reason": "",
        "agent_trace": agent_trace or {},
        "agent_logs": [],
        "holder_name": holder_name,
        "contact_email": contact_email,
        "is_historical": False,
        "created_at": now,
    }
    if store_plaintext:
        doc["access_code"] = code
        doc["access_code_hash"] = access_code_hash(code)
    else:
        # Legacy rows predate the portal: hashable credential never existed.
        doc["access_code_hash"] = access_code_hash(generate_access_code())
    _run(db.claims.insert_one(doc))  # mongomock calls are coroutines
    return code


def _seed_events(db, claim_id, events):
    """Insert pipeline events in order, mirroring app/events.emit_event docs."""
    for seq, (event, data) in enumerate(events, start=1):
        _run(  # mongomock calls are coroutines
            db.events.insert_one(
                {
                    "claim_id": claim_id,
                    "seq": seq,
                    "event": event,
                    "data": data or {},
                    "created_at": _now(),
                }
            )
        )


def _lookup(client, claim_number, code):
    return client.post(
        "/api/status/lookup",
        json={"claimNumber": claim_number, "accessCode": code},
    )


# ============ Valid access ============


def test_valid_code_returns_masked_status(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(db)
    _seed_events(
        db,
        "CLM-20260917-042",
        [
            ("claim_submitted", {"claim_id": "CLM-20260917-042"}),
            ("agent_start", {"agent": "INTAKE_AGENT"}),
            ("agent_complete", {"agent": "INTAKE_AGENT"}),
            ("agent_start", {"agent": "POLICY_AGENT"}),
            ("agent_complete", {"agent": "DOCUMENT_AGENT"}),
        ],
    )

    response = _lookup(client, "CLM-20260917-042", code)
    assert response.status_code == 200, response.text
    body = response.json()
    raw = str(body)

    assert body["claimNumber"] == "CLM-20260917-042"
    assert body["firstName"] == "Sarah"
    assert body["status"] == "pending"
    assert body["statusLabel"] == "In progress"
    assert body["currentStage"] == "Policy Verification"
    assert body["decisionReady"] is False

    milestones = {m["key"]: m for m in body["milestones"]}
    assert {m["key"] for m in body["milestones"]} == {
        "submitted",
        "documents_received",
        "decision_ready",
        "payout_recorded",
    }
    assert milestones["submitted"]["done"] is True
    assert milestones["documents_received"]["done"] is True
    assert milestones["decision_ready"]["done"] is False
    assert milestones["payout_recorded"]["done"] is False

    # PII budget: nothing beyond the first name and claim status may leak.
    assert "Chen" not in raw
    assert "sarah.chen@example.com" not in raw
    assert "AUTO-2024-001847" not in raw
    assert "1200" not in raw
    assert "access_code" not in raw
    assert code not in raw


def test_valid_lookup_shows_decision_when_ready(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(
        db,
        status="auto_approved",
        agent_trace={
            "decision": {"verdict": "approved", "payoutAmount": 500.0},
        },
    )
    _seed_events(
        db,
        "CLM-20260917-042",
        [
            ("claim_submitted", {"claim_id": "CLM-20260917-042"}),
            ("agent_complete", {"agent": "DECISION_AGENT"}),
        ],
    )

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert body["decisionReady"] is True
    assert body["decisionOutcome"] == "approved"
    assert body["statusLabel"] == "Approved"
    assert body["currentStage"] is None  # nothing in flight


# ============ Non-enumerable failures ============


def test_wrong_code_is_generic_404(client, patched_mongo):
    db = patched_mongo
    _make_claim(db, code=generate_access_code())

    response = _lookup(client, "CLM-20260917-042", "definitely-not-the-code-123456")
    assert response.status_code == 404
    assert response.json() == status_not_found()


def test_unknown_claim_number_returns_identical_404(client, patched_mongo):
    db = patched_mongo
    _make_claim(db)  # one claim exists; a different number must be indistinguishable

    wrong_number = _lookup(client, "CLM-20260917-999", "some-code-abc-0123456789")
    assert wrong_number.status_code == 404
    # The two failure modes return byte-identical bodies: a tester cannot
    # discover which claim numbers exist.
    assert wrong_number.json() == status_not_found()


def test_legacy_claim_without_code_stays_hidden(client, patched_mongo):
    db = patched_mongo
    _make_claim(db, store_plaintext=False)

    response = _lookup(client, "CLM-20260917-042", "legacy-guess-code-0123456789")
    assert response.status_code == 404
    assert response.json() == status_not_found()


def test_wrong_code_does_not_leak_lookup_timing_via_validation(client, patched_mongo):
    """Malformed claim numbers fail schema validation (422) before touching the
    store — 404 is reserved for well-formed but unmatched pairs."""
    db = patched_mongo
    _make_claim(db)
    response = _lookup(client, "../etc-passwd", "some-code-abc-0123456789")
    assert response.status_code == 422


# ============ Rate limiting ============


def test_lookup_rate_limited_per_ip(client, patched_mongo, limiter_on):
    db = patched_mongo
    code = _make_claim(db)

    # The configured cap is 60/minute: 60 valid lookups pass, the 61st 429s.
    statuses = [
        _lookup(client, "CLM-20260917-042", code).status_code for _ in range(61)
    ]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
    # The limit holds for further attempts too.
    assert _lookup(client, "CLM-20260917-042", code).status_code == 429


# ============ Decision letter ============


def test_decision_letter_downloads_for_ready_claim(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(
        db,
        agent_trace={"decision": {"verdict": "approved", "payoutAmount": 500.0}},
    )

    response = client.post(
        "/api/status/decision-letter",
        json={"claimNumber": "CLM-20260917-042", "accessCode": code},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["claimId"] == "CLM-20260917-042"
    assert body["pdf"].startswith("JV")  # '%PDF' base64-encoded


def test_decision_letter_hidden_until_decision_exists(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(db)  # pending, no decision trace

    response = client.post(
        "/api/status/decision-letter",
        json={"claimNumber": "CLM-20260917-042", "accessCode": code},
    )
    assert response.status_code == 404
    assert response.json() == status_not_found()


def test_decision_letter_rejects_wrong_code(client, patched_mongo):
    db = patched_mongo
    _make_claim(db, agent_trace={"decision": {"verdict": "approved", "payoutAmount": 1}})

    response = client.post(
        "/api/status/decision-letter",
        json={"claimNumber": "CLM-20260917-042", "accessCode": "wrong-code-0123456789"},
    )
    assert response.status_code == 404
    assert response.json() == status_not_found()


# ============ F2: next-step guidance + honest ETA ============


# The projection's wire allowlist: exactly the fields a customer may see.
# Anything added to the payload without being added here fails the test.
PORTAL_PAYLOAD_ALLOWLIST = frozenset({
    "claimNumber", "firstName", "status", "statusLabel", "currentStage",
    "incidentType", "decisionOutcome", "decisionReady", "pdfAvailable",
    "milestones", "nextSteps", "expectedResolution",
    # F6 decision transparency — the deny-by-default projection's output.
    "stageSummaries", "decision",
    # F5 messaging — lets the portal mount the customer thread surface.
    "messagesEnabled",
    # F7 settlement card — record fields only, and only once recorded.
    "settlement",
})

STAGE_NAMES = tuple(stage["name"] for stage in PIPELINE_STAGES)


def _projection_claim(**overrides):
    """A claim dict shaped like the stored document, tuned per test."""
    claim = {
        "id": "CLM-20260917-042",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "claimed_amount": 800.0,  # theft under the low-severity amount -> low -> 72h SLA
        "status": "pending",
        "created_at": _now(),
        "agent_trace": {},
    }
    claim.update(overrides)
    return claim


def _events_through(stage_index):
    """Event dicts completing every stage before stage_index, then starting it."""
    events = [{"event": "claim_submitted", "data": {}, "seq": 0, "created_at": _now()}]
    seq = 1
    for i in range(stage_index):
        events.append({"event": "agent_start", "data": {"agent": STAGE_NAMES[i]},
                       "seq": seq, "created_at": _now()})
        seq += 1
        events.append({"event": "agent_complete", "data": {"agent": STAGE_NAMES[i]},
                       "seq": seq, "created_at": _now()})
        seq += 1
    if stage_index < len(STAGE_NAMES):
        events.append({"event": "agent_start", "data": {"agent": STAGE_NAMES[stage_index]},
                       "seq": seq, "created_at": _now()})
    return events


def test_portal_stage_copy_tracks_pipeline_stages():
    """PORTAL_STAGE_COPY stays keyed to the real pipeline: the projection's
    ordered stage tuple must equal agents.PIPELINE_STAGES, and every key the
    customer can hit (all six stages + decided/reopened/failed) must carry
    non-empty copy."""
    assert _PORTAL_STAGE_ORDER == STAGE_NAMES
    assert set(PORTAL_STAGE_COPY) == set(STAGE_NAMES) | {"decided", "reopened", "failed"}
    for entry in PORTAL_STAGE_COPY.values():
        assert isinstance(entry["nextStep"], str) and entry["nextStep"].strip()


@pytest.mark.parametrize("stage_index", range(len(STAGE_NAMES)))
def test_next_steps_nonempty_for_every_pipeline_stage(stage_index):
    """AC-2.1: a claim at any stage gets non-empty next-step copy, starting at
    the stage that is running now."""
    claim = _projection_claim()
    payload = public_status_payload(claim, _events_through(stage_index))

    steps = payload["nextSteps"]
    assert steps and all(step.strip() for step in steps)
    assert steps[0] == PORTAL_STAGE_COPY[STAGE_NAMES[stage_index]]["nextStep"]


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"agent_trace": {"decision": {"verdict": "approved"}}}, "decided"),
        ({"status": "auto_approved"}, "decided"),
        ({"status": "reopened"}, "reopened"),
        ({"status": "failed"}, "failed"),
    ],
)
def test_next_steps_nonempty_for_terminal_states(overrides, key):
    """AC-2.1: terminal states never render an empty card."""
    payload = public_status_payload(_projection_claim(**overrides), [])
    assert payload["nextSteps"] == [PORTAL_STAGE_COPY[key]["nextStep"]]


def test_next_steps_show_full_run_before_events_start():
    """A queued claim (no events yet) previews the whole pipeline."""
    payload = public_status_payload(_projection_claim(), [])
    assert payload["nextSteps"] == [PORTAL_STAGE_COPY[stage]["nextStep"] for stage in STAGE_NAMES]


def test_projection_allowlist_blocks_internal_fields():
    """AC-2.1 deny-by-default: fields an internal trace gains tomorrow have no
    path into the customer payload, and no PII/credential ever appears."""
    claim = _projection_claim(
        contact_email="sarah.chen@example.com",
        policy_number="AUTO-2024-001847",
        access_code="plaintext-code",
        risk_score=91,
        fraud_score=0.91,
        escalation_reason="internal",
        failure_reason="internal",
        agent_trace={
            "decision": {"verdict": "approved"},
            "fraud": {"score": 0.91, "similar_incidents": [{"citedClaimId": "CLM-20260908-008"}]},
            "eligibility": {"confidence": 0.42},
            "internal_notes": "adjuster only",
        },
    )

    payload = public_status_payload(claim, _events_through(1))

    assert set(payload) <= PORTAL_PAYLOAD_ALLOWLIST
    raw = str(payload)
    for secret in (
        "Chen",
        "sarah.chen@example.com",
        "AUTO-2024-001847",
        "plaintext-code",
        "0.91",
        "adjuster only",
        "CLM-20260908-008",
    ):
        assert secret not in raw


def test_eta_present_when_sla_state_exists():
    """AC-2.2 present branch: a fresh low-severity claim has a 72h target, and
    the phrase renders it as business days without promising a date."""
    payload = public_status_payload(_projection_claim(), _events_through(0))

    eta = payload["expectedResolution"]
    assert eta and "within about 3 business days" in eta
    # Never a fabricated date or timestamp in the ETA.
    assert "/" not in eta and "-" not in eta


def test_eta_breached_phrase_names_the_delay_without_a_date():
    """An aged claim past its SLA target says so honestly instead of promising."""
    created = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
    payload = public_status_payload(_projection_claim(created_at=created), [])

    eta = payload["expectedResolution"]
    assert eta and "taking longer than we usually aim for" in eta
    assert "business day" not in eta


def test_eta_absent_omits_field_when_sla_state_is_absent():
    """AC-2.2 absent branch: no created_at -> no SLA clock -> the key is
    omitted entirely, never an empty string."""
    payload = public_status_payload(_projection_claim(created_at=None), [])
    assert "expectedResolution" not in payload

    payload = public_status_payload(_projection_claim(created_at="not-a-date"), [])
    assert "expectedResolution" not in payload


def test_eta_absent_when_nothing_is_pending():
    """Decided, failed, and reopened claims have no honest ETA."""
    for overrides in (
        {"agent_trace": {"decision": {"verdict": "approved"}}},
        {"status": "failed"},
        {"status": "reopened"},
    ):
        payload = public_status_payload(_projection_claim(**overrides), [])
        assert "expectedResolution" not in payload


def test_lookup_returns_next_steps_and_eta(client, patched_mongo):
    """API round-trip: the projection fields ride the masked response."""
    db = patched_mongo
    code = _make_claim(db)

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert body["nextSteps"] and all(isinstance(step, str) for step in body["nextSteps"])
    assert re.search(r"within about \d+ business days?", body["expectedResolution"])


def test_lookup_omits_eta_field_without_sla_state(client, patched_mongo):
    """API round-trip absent branch: the response body drops the key entirely
    (exclude_unset), so an older cached client cannot mistake null for a value."""
    db = patched_mongo
    code = generate_access_code()
    _run(
        db.claims.insert_one({
            "id": "CLM-20260917-090",
            "policy_number": "AUTO-2024-001847",
            "claim_date": _now(),
            "incident_type": "accident",
            "claimed_amount": 1200.0,
            "status": "pending",
            "risk_score": 0,
            "decision_reason": "",
            "agent_trace": {},
            "agent_logs": [],
            "holder_name": "Sarah Chen",
            "contact_email": "sarah.chen@example.com",
            "is_historical": False,
            # No created_at: legacy rows predate the SLA clock.
            "access_code_hash": access_code_hash(code),
        })
    )

    body = _lookup(client, "CLM-20260917-090", code).json()
    assert "expectedResolution" not in body
    assert body["nextSteps"]  # the card copy does not depend on the ETA

# ============ F6 decision transparency (deny-by-default projection) ============


def _decided_trace() -> dict:
    """A realistic decided trace carrying every internal field the pipeline
    stores — none of which may reach a customer endpoint."""
    return {
        "intake": {
            "valid": True,
            "normalizedData": {"policyNumber": "AUTO-2024-001847", "claimedAmount": 1200.0},
            "summary": "Normalized the submission.",
        },
        "policy": {
            "found": True,
            "status": "active",
            "citedFields": ["end_date=2027-01-01"],
            "adjustedPayout": 380.0,
            "summary": "Policy active.",
        },
        "documents": {
            "consistency": "consistent",
            "consistencyScore": 1.0,
            "redFlags": [],
            "summary": "Documents consistent.",
        },
        "fraud": {
            "fingerprint": "fp-internal",
            "flags": [{"code": "high_amount"}],
            "similarity": {"verdict": "coincidence"},
        },
        "eligibility": {
            "riskScore": 12,
            "riskFactors": ["clean"],
            "recommendation": "auto_approve",
            "summary": "Low risk.",
        },
        "decision": {
            "verdict": "approved",
            "payoutAmount": 380.0,
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
        },
    }


def test_lookup_decided_claim_returns_transparency_projection(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(db, status="auto_approved", agent_trace=_decided_trace())
    _seed_events(
        db,
        "CLM-20260917-042",
        [("claim_submitted", {}), ("agent_complete", {"agent": "DECISION_AGENT"})],
    )

    response = _lookup(client, "CLM-20260917-042", code)
    assert response.status_code == 200, response.text
    body = response.json()
    raw = str(body)

    assert body["decisionReady"] is True
    assert body["decision"]["summary"] == "Approved: covered incident within limits."
    assert body["decision"]["citations"][0]["customerFriendlyExplanation"] == (
        "Your policy was active when the incident happened."
    )
    assert [s["stage"] for s in body["stageSummaries"]] == [
        "intake",
        "policy",
        "documents",
        "fraud",
        "eligibility",
        "decision",
    ]
    # Stage copy is pre-written (PORTAL_STAGE_COPY), not agent text.
    assert body["stageSummaries"][1]["title"] == "Verifying your coverage"

    # Deny-by-default at the endpoint: internal trace data has no path out.
    for marker in (
        "riskScore",
        "adjustedPayout",
        "payoutAmount",
        "letterBody",
        "confidence",
        "fingerprint",
        "similarity",
        "normalizedData",
        "citedFields",
        "consistencyScore",
        "redFlags",
        "recommendation",
        "AUTO-2024-001847",
        "Sarah Chen",
    ):
        assert marker not in raw, f"internal marker leaked to endpoint: {marker}"


def test_lookup_inflight_claim_has_no_decision_block(client, patched_mongo):
    db = patched_mongo
    code = _make_claim(
        db,
        status="pending",
        agent_trace={"intake": {"valid": True}, "policy": {"found": True}},
    )
    _seed_events(
        db,
        "CLM-20260917-042",
        [
            ("claim_submitted", {}),
            ("agent_complete", {"agent": "INTAKE_AGENT"}),
            ("agent_start", {"agent": "POLICY_AGENT"}),
        ],
    )

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert body["decision"] is None
    assert body["decisionReady"] is False
    assert [s["stage"] for s in body["stageSummaries"]] == ["intake", "policy"]


def test_lookup_projection_ignores_new_internal_trace_field(client, patched_mongo):
    """The deny-by-default regression at the endpoint boundary: a field added
    to a stored trace tomorrow must not appear in the customer response."""
    db = patched_mongo
    trace = _decided_trace()
    trace["policy"]["segment_risk_band"] = "ENDPOINT-LEAK-42"
    code = _make_claim(db, status="auto_approved", agent_trace=trace)
    _seed_events(
        db,
        "CLM-20260917-042",
        [("agent_complete", {"agent": "DECISION_AGENT"})],
    )

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert "ENDPOINT-LEAK-42" not in str(body)


# ============ F7: settlement card (record fields only, once recorded) ============


def _settlement_record(**overrides):
    """The settlement record exactly as POST /claims/{id}/settlement stores it."""
    record = {
        "amount": 1150.0,
        "method": "bank_transfer",
        "reference": "BNK-2026-000123",
        "settled_at": _now(),
        "recorded_by": "adjuster@claimos.dev",
    }
    record.update(overrides)
    return record


def _decided_claim(**overrides):
    """An auto-approved claim dict, tuned per test."""
    return _projection_claim(
        status="auto_approved",
        agent_trace={"decision": {"verdict": "approved"}},
        **overrides,
    )


def test_settlement_card_absent_before_settlement():
    """AC-7.1 absence branch: before settlement the payload carries no card
    key at all, and milestone 4 stays pending with no timestamp."""
    payload = public_status_payload(_decided_claim(), _events_through(len(STAGE_NAMES)))

    assert "settlement" not in payload
    milestones = {m["key"]: m for m in payload["milestones"]}
    assert milestones["payout_recorded"]["done"] is False
    assert milestones["payout_recorded"]["at"] is None


def test_settlement_card_and_final_milestone_appear_after_settlement():
    """AC-7.1 present branch: settlement recorded -> the payload carries the
    card and milestone 4 completes from the payout_recorded event."""
    settled_at = _now()
    claim = _decided_claim(settlement=_settlement_record(settled_at=settled_at))
    events = _events_through(len(STAGE_NAMES)) + [
        {"event": "payout_recorded", "data": {"amount": 1150.0}, "seq": 99, "created_at": settled_at}
    ]

    payload = public_status_payload(claim, events)

    assert payload["settlement"] == {"amount": 1150.0, "settledAt": settled_at}
    milestones = {m["key"]: m for m in payload["milestones"]}
    assert milestones["payout_recorded"]["done"] is True
    assert milestones["payout_recorded"]["at"] == settled_at


def test_settlement_card_drops_internal_and_future_record_fields():
    """AC-7.2 deny-by-default: method/reference/recorded_by are record-only
    bookkeeping, and any field the record gains tomorrow (a fee, a payment
    ETA) stays invisible — the card is built field-by-field, never copied."""
    claim = _decided_claim(
        settlement=_settlement_record(fee=12.5, payment_eta="2026-09-25")
    )

    payload = public_status_payload(claim, [])

    assert payload["settlement"] == {
        "amount": 1150.0,
        "settledAt": claim["settlement"]["settled_at"],
    }
    raw = str(payload)
    for marker in (
        "bank_transfer",
        "BNK-2026-000123",
        "adjuster@claimos.dev",
        "12.5",
        "2026-09-25",
        "payment_eta",
        "fee",
    ):
        assert marker not in raw, f"settlement bookkeeping leaked to the portal: {marker}"


def test_settlement_card_omitted_when_record_has_no_visible_fields():
    """A record carrying only bookkeeping (or malformed/empty fields) projects
    to nothing — the card never renders an empty shell or placeholders."""
    claim = _decided_claim(
        settlement=_settlement_record(amount=None, settled_at="")
    )

    payload = public_status_payload(claim, [])

    assert "settlement" not in payload


def test_lookup_returns_settlement_card_after_recording(client, patched_mongo, make_authenticated_user):
    """API round-trip through the real chain: the settlement endpoint stores
    the record and emits payout_recorded (Wave 0 wiring), the next lookup
    carries the card and the completed final milestone — and none of the
    record bookkeeping."""
    db = patched_mongo
    code = _make_claim(db, status="auto_approved", agent_trace={"decision": {"verdict": "approved"}})
    headers, _, _ = make_authenticated_user(client, role="adjuster")

    response = client.post(
        "/api/claims/CLM-20260917-042/settlement",
        json={"amount": 1150.0, "method": "bank_transfer", "reference": "BNK-1"},
        headers=headers,
    )
    assert response.status_code == 201, response.text

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert body["settlement"]["amount"] == 1150.0
    assert body["settlement"]["settledAt"]
    raw = str(body)
    assert "bank_transfer" not in raw
    assert "BNK-1" not in raw
    milestones = {m["key"]: m for m in body["milestones"]}
    assert milestones["payout_recorded"]["done"] is True


def test_lookup_omits_settlement_key_before_recording(client, patched_mongo):
    """API round-trip absence branch: the response body drops the key entirely
    (response_model_exclude_unset), so the card cannot render from a null."""
    db = patched_mongo
    code = _make_claim(db, status="auto_approved", agent_trace={"decision": {"verdict": "approved"}})

    body = _lookup(client, "CLM-20260917-042", code).json()
    assert "settlement" not in body
