"""Spec F12: saved workbench views (owner-private) + audited bulk actions.

Covers the acceptance criteria end to end:
- AC-12.1 saved views: create/list/apply/delete, private to their owner —
  another adjuster cannot see, apply, or delete someone else's view.
- AC-12.2 bulk success + partial failure: one audited single action per
  touched claim, per-claim outcomes reported, duplicates never double-apply.
- AC-12.3 per-claim audits: bulk reassign/flag each append their own
  audit_log entry; a failing claim reports a failure result, never silence.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import database
import server


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        # Per-test isolation for the collections F12 touches. Startup seeds
        # demo claims; row-level assertions want an empty slate.
        _run(database.claims_col.delete_many({}))
        _run(database.audit_log_col.delete_many({}))
        _run(database.workbench_views_col.delete_many({}))
        yield test_client


@pytest.fixture
def adjuster_headers(make_authenticated_user):
    """Factory: register+login an adjuster, return bearer headers."""

    def _make(client):
        headers, _csrf, _email = make_authenticated_user(client, role="adjuster")
        return headers

    return _make


@pytest.fixture
def adjuster_identity(make_authenticated_user):
    """Factory: bearer headers PLUS the adjuster's email (reassign target)."""

    def _make(client):
        headers, _csrf, email = make_authenticated_user(client, role="adjuster")
        return headers, email

    return _make


def _insert_claim(**overrides):
    """Insert a workbench-shaped claim; returns its id."""
    claim = {
        "id": "CLM-TEST-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "incident_date": "2026-09-01",
        "claimed_amount": 1200.0,
        "description": "Rear-end collision.",
        "status": "escalated",
        "risk_score": 0.42,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent_trace": {},
        "agent_logs": [],
        **overrides,
    }
    _run(database.claims_col.insert_one(claim.copy()))
    return claim["id"]


# ============ AC-12.1: saved views, owner-private ============


def test_views_require_authentication(client):
    assert client.post("/api/workbench/views", json={"name": "x"}).status_code == 401
    assert client.get("/api/workbench/views").status_code == 401


def test_save_list_and_apply_view_round_trip(client, adjuster_headers):
    headers = adjuster_headers(client)
    response = client.post(
        "/api/workbench/views",
        headers=headers,
        json={"name": "Elevated theft", "filters": {"severity": "elevated", "sort": "age"}},
    )
    assert response.status_code == 201
    view = response.json()
    assert view["name"] == "Elevated theft"
    assert view["filters"] == {"severity": "elevated", "sort": "age"}

    listed = client.get("/api/workbench/views", headers=headers).json()
    assert [item["id"] for item in listed] == [view["id"]]

    apply_response = client.get(f"/api/workbench/views/{view['id']}/apply", headers=headers)
    assert apply_response.status_code == 200
    body = apply_response.json()
    assert body["view"]["id"] == view["id"]
    assert "rows" in body and "generatedAt" in body

    deleted = client.delete(f"/api/workbench/views/{view['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/workbench/views", headers=headers).json() == []


def test_views_reject_unknown_filters_and_blank_names(client, adjuster_headers):
    headers = adjuster_headers(client)
    unknown = client.post(
        "/api/workbench/views", headers=headers, json={"name": "Bad", "filters": {"claimant": "x"}}
    )
    assert unknown.status_code == 422

    blank = client.post("/api/workbench/views", headers=headers, json={"name": "   ", "filters": {}})
    assert blank.status_code == 422


def test_view_apply_404s_for_other_owners_and_missing(client, adjuster_headers, adjuster_identity):
    owner_headers = adjuster_headers(client)
    created = client.post(
        "/api/workbench/views",
        headers=owner_headers,
        json={"name": "Mine", "filters": {"severity": "elevated"}},
    ).json()

    missing = client.get(
        "/api/workbench/views/vw_does_not_exist/apply", headers=owner_headers
    )
    assert missing.status_code == 404

    # A different adjuster can neither apply nor delete someone else's view,
    # and their own list stays empty — views never leak across owners.
    other_headers, _other_email = adjuster_identity(client)
    stolen = client.get(f"/api/workbench/views/{created['id']}/apply", headers=other_headers)
    assert stolen.status_code == 404
    assert client.delete(f"/api/workbench/views/{created['id']}", headers=other_headers).status_code == 404
    assert client.get("/api/workbench/views", headers=other_headers).json() == []


def test_saving_the_same_name_replaces_the_preset(client, adjuster_headers):
    headers = adjuster_headers(client)
    first = client.post(
        "/api/workbench/views",
        headers=headers,
        json={"name": "Daily", "filters": {"severity": "elevated"}},
    ).json()
    second = client.post(
        "/api/workbench/views",
        headers=headers,
        json={"name": "Daily", "filters": {"status": "escalated"}},
    ).json()

    assert first["id"] == second["id"]
    listed = client.get("/api/workbench/views", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["filters"] == {"status": "escalated"}


# ============ AC-12.2/12.3: bulk actions, audited per claim ============


def test_bulk_reassign_is_a_loop_of_per_claim_audits(client, adjuster_identity):
    target_headers, target_email = adjuster_identity(client)  # the reassign target
    _insert_claim(id="CLM-TEST-001")
    _insert_claim(id="CLM-TEST-002")

    headers, _email = adjuster_identity(client)
    response = client.post(
        "/api/workbench/claims/bulk",
        headers=headers,
        json={
            "action": "reassign",
            "claimIds": ["CLM-TEST-001", "CLM-TEST-002"],
            "target": target_email,
            "reason": "Vacation hand-off",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 2 and body["failed"] == 0

    # AC-12.3: one audit entry per touched claim, not one opaque write.
    audit_001 = _run(database.audit_log_col.find({"claim_id": "CLM-TEST-001"}).to_list(200))
    audit_002 = _run(database.audit_log_col.find({"claim_id": "CLM-TEST-002"}).to_list(200))
    assert len(audit_001) == 1 and len(audit_002) == 1
    assert audit_001[0]["action"] == "reassign"

    target = _run(database.users_col.find_one({"email": target_email}))
    assert target is not None
    assert audit_001[0]["after"]["assignee_id"] == target["id"]

    doc = _run(database.claims_col.find_one({"id": "CLM-TEST-001"}))
    assert doc["assignee_id"] == target["id"]

    for item in body["results"]:
        assert item["status"] == "updated"
        assert item["auditId"]


def test_bulk_flag_appends_flags_with_per_claim_audits(client, adjuster_identity):
    actor_headers, actor_email = adjuster_identity(client)
    _insert_claim(id="CLM-TEST-001")

    response = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "flag", "claimIds": ["CLM-TEST-001"], "reason": "Suspicious invoice"},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    doc = _run(database.claims_col.find_one({"id": "CLM-TEST-001"}))
    assert len(doc["flags"]) == 1
    assert doc["flags"][0]["reason"] == "Suspicious invoice"
    assert doc["flags"][0]["flagged_by"] == actor_email

    audit = _run(database.audit_log_col.find({"claim_id": "CLM-TEST-001"}).to_list(200))
    assert len(audit) == 1
    assert audit[0]["action"] == "flag_for_review"


def test_bulk_partial_failure_reports_per_claim_and_keeps_auditing_rest(client, adjuster_identity):
    actor_headers, _email = adjuster_identity(client)
    _insert_claim(id="CLM-TEST-001")

    response = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "flag", "claimIds": ["CLM-TEST-001", "CLM-9999"], "reason": "Mixed batch"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1 and body["failed"] == 1

    by_id = {item["claimId"]: item for item in body["results"]}
    assert by_id["CLM-TEST-001"]["status"] == "updated"
    # AC-12.2: the failure is reported with a reason, never silently dropped.
    assert by_id["CLM-9999"]["status"] == "failed"
    assert by_id["CLM-9999"]["detail"] == "Claim not found"

    # The good claim was still audited; the bad one left no audit trace.
    assert _run(database.audit_log_col.count_documents({"claim_id": "CLM-TEST-001"})) == 1
    assert _run(database.audit_log_col.count_documents({"claim_id": "CLM-9999"})) == 0


def test_bulk_duplicate_claim_ids_apply_once(client, adjuster_identity):
    actor_headers, _email = adjuster_identity(client)
    _insert_claim(id="CLM-TEST-001")

    body = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "flag", "claimIds": ["CLM-TEST-001", "CLM-TEST-001"], "reason": "Once"},
    ).json()
    assert body["updated"] == 1
    doc = _run(database.claims_col.find_one({"id": "CLM-TEST-001"}))
    assert len(doc["flags"]) == 1


def test_bulk_reassign_requires_existing_adjuster_target(client, make_authenticated_user, adjuster_identity):
    actor_headers, _email = adjuster_identity(client)
    _insert_claim(id="CLM-TEST-001")

    missing_target = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "reassign", "claimIds": ["CLM-TEST-001"], "target": "", "reason": "x"},
    )
    assert missing_target.status_code == 422

    unknown_target = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={
            "action": "reassign",
            "claimIds": ["CLM-TEST-001"],
            "target": "nobody@test.example",
            "reason": "x",
        },
    )
    assert unknown_target.status_code == 404

    customer_headers, _customer_csrf, customer_email = make_authenticated_user(client, role="customer")
    customer_target = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={
            "action": "reassign",
            "claimIds": ["CLM-TEST-001"],
            "target": customer_email,
            "reason": "x",
        },
    )
    assert customer_target.status_code == 404


def test_bulk_rejects_empty_ids_and_missing_reason(client, adjuster_identity):
    actor_headers, _email = adjuster_identity(client)
    empty_ids = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "flag", "claimIds": [], "reason": "x"},
    )
    assert empty_ids.status_code == 422

    blank_reason = client.post(
        "/api/workbench/claims/bulk",
        headers=actor_headers,
        json={"action": "flag", "claimIds": ["CLM-TEST-001"], "reason": "   "},
    )
    assert blank_reason.status_code == 422


def test_bulk_endpoint_is_adjuster_gated(client, make_authenticated_user):
    customer_headers, _customer_csrf, _email = make_authenticated_user(client, role="customer")
    response = client.post(
        "/api/workbench/claims/bulk",
        headers=customer_headers,
        json={"action": "flag", "claimIds": ["CLM-TEST-001"], "reason": "Nope"},
    )
    assert response.status_code == 403


def test_queue_rows_expose_flags_and_assignee():
    from app.workbench import queue_row

    claim = {
        "id": "CLM-TEST-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "claimed_amount": 1200.0,
        "status": "escalated",
        "risk_score": 0.42,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "flags": [{"reason": "r", "flagged_by": "ada@test.example", "flagged_at": "t"}],
        "assignee_id": "usr_adj_9",
    }
    row = queue_row(claim, now=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    assert len(row["flags"]) == 1
    assert row["assignee_id"] == "usr_adj_9"
