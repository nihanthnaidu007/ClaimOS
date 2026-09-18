"""Reopen flow tests (spec F14, AC-14.1..AC-14.3).

POST /api/claims/{claim_id}/reopen is valid only from a decided state; it
flips the claim to `reopened`, appends an immutable audit entry, and emits a
claim_reopened event the customer portal's timeline renders. Reopen is a
REVIEW state: no claim_run is ever enqueued (no automatic pipeline re-run) —
re-adjudication is the adjuster's explicit override from the workbench, and
that second decision replaces the portal's decision outcome. Runs on
mongomock — no MongoDB, no LLM.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import database
import pytest
import server
from app.status_portal import access_code_hash, public_status_payload
from starlette.testclient import TestClient


def _run(coro):
    return asyncio.run(coro)


REOPEN_REASON = "Customer submitted new repair estimates after the decision."

# The clear, status-naming error every non-decided reopen attempt returns.
_NOT_DECIDED_FRAGMENT = "cannot be reopened"


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        _run(database.claims_col.delete_many({}))
        yield test_client


@pytest.fixture
def adjuster_headers(make_authenticated_user):
    """Factory: register+login an adjuster, return bearer headers."""

    def _make(client):
        headers, _csrf, _email = make_authenticated_user(client, role="adjuster")
        return headers

    return _make


def _login_token(client, role):
    """Register+login a fresh user of `role`, return their access token."""
    from conftest import TEST_INVITE_CODE, TEST_PASSWORD

    email = f"{role}-{uuid4().hex[:8]}@test.example"
    register = client.post(
        "/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "role": role,
              "inviteCode": TEST_INVITE_CODE},
    )
    assert register.status_code == 201
    login = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert login.status_code == 200
    return login.json()["accessToken"]


def _insert_claim(**overrides):
    """Insert a decided-shaped claim; overrides retarget status/trace/override."""
    claim = {
        "id": "CLM-TEST-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "incident_date": "2026-09-01",
        "claimed_amount": 1200.0,
        "status": "auto_approved",
        "risk_score": 0.31,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent_trace": {
            "decision": {"verdict": "approved", "payoutAmount": 1200.0},
        },
        "agent_logs": [],
        **overrides,
    }
    _run(database.claims_col.insert_one(claim.copy()))
    return claim["id"]


def _reopen(client, claim_id, headers, reason=REOPEN_REASON):
    return client.post(
        f"/api/claims/{claim_id}/reopen", json={"reason": reason}, headers=headers
    )


def _claim(claim_id):
    return _run(database.claims_col.find_one({"id": claim_id}, {"_id": 0}))


def _events(claim_id):
    return _run(database.events_col.find({"claim_id": claim_id}, {"_id": 0}).to_list(50))


# ============ AC-14.1: decided succeeds (audit + event), else 409 ============


def test_reopen_from_decided_succeeds_with_audit_and_event(client, adjuster_headers):
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)

    response = _reopen(client, claim_id, headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reopened"
    assert body["claimId"] == claim_id

    entry = body["auditEntry"]
    assert entry["action"] == "reopen"
    assert entry["claim_id"] == claim_id
    assert entry["before"] == {"status": "auto_approved"}
    assert entry["after"] == {"status": "reopened"}
    assert entry["reason"] == REOPEN_REASON
    assert entry["at"] and entry["actor"]

    claim = _claim(claim_id)
    assert claim["status"] == "reopened"
    assert claim["reopen"]["reason"] == REOPEN_REASON
    assert claim["reopen"]["auditId"] == entry["id"]

    # Exactly one audit row and one durable timeline event.
    assert _run(database.audit_log_col.count_documents({})) == 1
    events = _events(claim_id)
    assert any(event["event"] == "claim_reopened" for event in events)
    reopened = next(e for e in events if e["event"] == "claim_reopened")
    assert reopened["data"]["reason"] == REOPEN_REASON
    assert reopened["data"]["actor"]


@pytest.mark.parametrize("status", ["approved", "rejected", "overridden", "settled", "failed"])
def test_reopen_from_every_decided_status_succeeds(client, adjuster_headers, status):
    claim_id = _insert_claim(status=status)
    response = _reopen(client, claim_id, adjuster_headers(client))
    assert response.status_code == 200
    assert response.json()["status"] == "reopened"
    assert _claim(claim_id)["status"] == "reopened"


@pytest.mark.parametrize("status", ["pending", "escalated", "under_review", "reopened"])
def test_reopen_from_undecided_state_conflicts_409(client, adjuster_headers, status):
    """AC-14.1 second branch: every non-decided state 409s with a clear error."""
    claim_id = _insert_claim(status=status)
    headers = adjuster_headers(client)

    response = _reopen(client, claim_id, headers)
    assert response.status_code == 409
    assert _NOT_DECIDED_FRAGMENT in response.json()["detail"]
    assert status in response.json()["detail"]  # the error names the current state

    # No state change anywhere: claim, audit trail, event stream.
    assert _claim(claim_id)["status"] == status
    assert "reopen" not in _claim(claim_id)
    assert _run(database.audit_log_col.count_documents({})) == 0
    assert _run(database.events_col.count_documents({})) == 0


def test_reopen_of_an_already_reopened_claim_conflicts(client, adjuster_headers):
    """Reopen twice: the second attempt 409s — reopened is a review state, not decided."""
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)
    assert _reopen(client, claim_id, headers).status_code == 200
    assert _reopen(client, claim_id, headers).status_code == 409


def test_reopen_without_reason_is_422_with_no_state_change(client, adjuster_headers):
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)
    for payload in ({}, {"reason": ""}, {"reason": "   "}):
        response = client.post(
            f"/api/claims/{claim_id}/reopen", json=payload, headers=headers
        )
        assert response.status_code == 422, payload

    assert _claim(claim_id)["status"] == "auto_approved"
    assert _run(database.audit_log_col.count_documents({})) == 0
    assert _run(database.events_col.count_documents({})) == 0


def test_reopen_unknown_claim_404(client, adjuster_headers):
    response = _reopen(client, "CLM-NOPE-000", adjuster_headers(client))
    assert response.status_code == 404


def test_reopen_rejects_customer_role(client):
    claim_id = _insert_claim(status="auto_approved")
    token = _login_token(client, "customer")
    response = client.post(
        f"/api/claims/{claim_id}/reopen",
        json={"reason": "Customers cannot reopen"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_reopen_never_enqueues_a_pipeline_run(client, adjuster_headers):
    """The no-automatic-re-run invariant: reopen is a review state, not execution."""
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)
    assert _reopen(client, claim_id, headers).status_code == 200

    runs = _run(database.claim_runs_col.find({"claim_id": claim_id}, {"_id": 0}).to_list(10))
    assert runs == []


# ============ AC-14.3: queue appearance + re-decision replacement ============


def test_reopened_claim_appears_in_the_default_queue_scope(client, adjuster_headers):
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)
    assert _reopen(client, claim_id, headers).status_code == 200

    queue = client.get("/api/workbench/queue", headers=headers).json()
    row_ids = [row["id"] for row in queue["rows"]]
    assert claim_id in row_ids
    row = next(row for row in queue["rows"] if row["id"] == claim_id)
    assert row["status"] == "reopened"


def test_reopened_claim_can_be_redecided_by_override(client, adjuster_headers):
    """Override accepts reopened claims; the re-decision lands like any override."""
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)
    assert _reopen(client, claim_id, headers).status_code == 200

    response = client.post(
        f"/api/workbench/claims/{claim_id}/override",
        json={"decision": "rejected", "payoutAmount": 0, "reason": "New facts void coverage."},
        headers=headers,
    )
    assert response.status_code == 200
    claim = _claim(claim_id)
    assert claim["status"] == "overridden"
    assert claim["override"]["decision"] == "rejected"
    assert any(e["event"] == "claim_overridden" for e in _events(claim_id))


def test_second_decision_replaces_the_portal_decision_section(client, adjuster_headers):
    """AC-14.3: after the second decision, the portal outcome is the NEW verdict."""
    claim_id = _insert_claim(status="auto_approved")
    headers = adjuster_headers(client)

    before = public_status_payload(_claim(claim_id), _events(claim_id))
    assert before["decisionOutcome"] == "approved"

    assert _reopen(client, claim_id, headers).status_code == 200
    during = public_status_payload(_claim(claim_id), _events(claim_id))
    assert during["decisionOutcome"] == "approved"  # nothing replaces it until re-decided

    assert client.post(
        f"/api/workbench/claims/{claim_id}/override",
        json={"decision": "rejected", "reason": "New facts void coverage."},
        headers=headers,
    ).status_code == 200

    after = public_status_payload(_claim(claim_id), _events(claim_id))
    assert after["decisionOutcome"] == "rejected"


# ============ AC-14.2: portal projection — chip copy + timeline event ============


def _portal_events_with_reopen():
    return [
        {"seq": 1, "event": "claim_submitted", "data": {}, "created_at": "2026-09-18T10:00:00+00:00"},
        {"seq": 2, "event": "agent_complete", "data": {"agent": "DECISION_AGENT"},
         "created_at": "2026-09-18T10:05:00+00:00"},
        {"seq": 3, "event": "claim_reopened", "data": {"reason": "internal why", "actor": "a@b.c"},
         "created_at": "2026-09-18T11:00:00+00:00"},
    ]


def _reopened_claim(**overrides):
    """Portal-shaped claim dict — projection tests are pure, no DB round-trips."""
    return {
        "id": "CLM-TEST-001",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "status": "reopened",
        "agent_trace": {"decision": {"verdict": "approved"}},
        **overrides,
    }


def test_portal_projection_exposes_reopened_status_and_copy():
    payload = public_status_payload(_reopened_claim(), _portal_events_with_reopen())

    assert payload["status"] == "reopened"
    assert payload["statusLabel"] == "Being reviewed again"
    assert payload["statusNote"] == (
        "Your claim is being reviewed again — we'll keep you updated as it progresses."
    )


def test_portal_projection_gains_the_reopened_timeline_event():
    payload = public_status_payload(_reopened_claim(), _portal_events_with_reopen())

    reopened_slots = [m for m in payload["milestones"] if m["key"] == "reopened"]
    assert len(reopened_slots) == 1
    assert reopened_slots[0]["done"] is True
    assert reopened_slots[0]["at"] == "2026-09-18T11:00:00+00:00"
    assert "under review again" in reopened_slots[0]["label"]


def test_portal_projection_omits_reopened_slot_when_claim_never_reopened():
    submitted_only = [
        {"seq": 1, "event": "claim_submitted", "data": {}, "created_at": "2026-09-18T10:00:00+00:00"},
    ]
    payload = public_status_payload(_reopened_claim(status="auto_approved"), submitted_only)

    assert all(m["key"] != "reopened" for m in payload["milestones"])
    assert payload["statusNote"] is None


def test_portal_projection_stays_deny_by_default_on_reopen():
    """The reopen reason/actor are internal — they never reach the customer payload."""
    claim = _reopened_claim(reopen={
        "actor": "usr_adj123", "actor_email": "adj@claimos.dev", "action": "reopen",
        "reason": "internal reopen why", "at": "2026-09-18T11:00:00+00:00",
        "auditId": "aud_abc123",
    })
    payload = public_status_payload(claim, _portal_events_with_reopen())

    flattened = str(payload)
    assert "internal reopen why" not in flattened
    assert "usr_adj123" not in flattened
    assert "adj@claimos.dev" not in flattened
    assert "aud_abc123" not in flattened


def test_portal_lookup_endpoint_serves_reopened_projection(client, adjuster_headers):
    """End-to-end through the public route: reopen, then the portal shows the copy."""
    access_code = "portal-code-16ch"
    claim_id = _insert_claim(
        # Must match the portal's claim-number pattern (^CLM-YYYYMMDD-n).
        id="CLM-20260918-14",
        status="auto_approved",
        access_code_hash=access_code_hash(access_code),
        holder_name="Sarah Chen",
    )
    headers = adjuster_headers(client)
    assert _reopen(client, claim_id, headers).status_code == 200

    response = client.post(
        "/api/status/lookup",
        json={"claimNumber": claim_id, "accessCode": access_code},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reopened"
    assert payload["statusLabel"] == "Being reviewed again"
    assert payload["statusNote"] is not None
