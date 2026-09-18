"""F10 - claim assignment: round-robin rotation, config toggle, audited
manual reassignment, queue Mine/Unassigned/All filters, and customer-portal
neutrality. The rotation and workload math are pure functions; the endpoint
tests go through the FastAPI app with mongomock collections.
"""

import pytest
from fastapi.testclient import TestClient

import database
import server
from app.assignment import (
    AUTO_ASSIGNER,
    assignment_fields,
    choose_auto_assignee,
    pick_next_assignee_id,
)
from app.config import settings
from app.schemas import UserRecord
from app.status_portal import access_code_hash
from app.workbench_routes import QueueParams, _apply_row_filters


def adjuster(uid, email, active=True, role="adjuster"):
    return UserRecord(
        id=uid, email=email, password_hash="hashed", role=role, active=active
    )


def claim(cid, status, assignee_id=None, assigned_at=None):
    return {
        "id": cid,
        "status": status,
        "assignee_id": assignee_id,
        "assigned_at": assigned_at,
    }


SUBMISSION = {
    "policyNumber": "POL-1001",
    "holderName": "Test Holder",
    "contactEmail": "holder@example.com",
    "incidentType": "theft",
    "incidentDate": "2026-09-01",
    "claimedAmount": 1200,
    "description": "Storm damaged the garage roof overnight",
}


# ---- Pure rotation (spec F10.1: stable least-recently-assigned order) ----


def test_pick_next_assignee_rotation_round_robin():
    users = [adjuster("u1", "a@x.com"), adjuster("u2", "b@x.com")]
    # u1 assigned more recently than u2 -> u2 is next.
    claims = [claim("C1", "pending", "u1", "2026-09-18T10:00:00+00:00")]
    assert pick_next_assignee_id(users, claims) == "u2"
    # After u2 is assigned most recently, u1 (older stamp) is next.
    claims.append(claim("C2", "pending", "u2", "2026-09-18T11:00:00+00:00"))
    assert pick_next_assignee_id(users, claims) == "u1"


def test_pick_next_assignee_never_assigned_first():
    users = [adjuster("u1", "a@x.com"), adjuster("u2", "b@x.com")]
    # u2 has an old assignment, u1 none -> never-assigned u1 goes first.
    claims = [claim("C1", "pending", "u2", "2020-01-01T00:00:00+00:00")]
    assert pick_next_assignee_id(users, claims) == "u1"


def test_pick_next_assignee_ties_break_on_user_id():
    users = [adjuster("u2", "b@x.com"), adjuster("u1", "a@x.com")]
    # Never-assigned u1 sorts before stamped u2; among equal keys the id
    # tiebreak decides.
    claims = [claim("C1", "pending", "u2", "2026-09-18T10:00:00+00:00")]
    assert pick_next_assignee_id(users, claims) == "u1"


def test_pick_next_assignee_skips_inactive_and_customers():
    users = [
        adjuster("u1", "a@x.com", active=False),
        adjuster("u3", "c@x.com", role="customer"),
        adjuster("u2", "b@x.com"),
    ]
    assert pick_next_assignee_id(users, []) == "u2"


def test_pick_next_assignee_empty_roster_returns_none():
    assert pick_next_assignee_id([], []) is None
    assert pick_next_assignee_id([adjuster("u1", "a@x.com", active=False)], []) is None


# ---- Config toggle (AUTO_ASSIGN=round_robin|none) ----


async def test_choose_auto_assignee_none_leaves_claim_unassigned(patched_mongo, monkeypatch):
    monkeypatch.setattr(settings, "auto_assign", "none")
    assert await choose_auto_assignee() is None


async def test_choose_auto_assignee_round_robin_picks_adjuster(patched_mongo, monkeypatch):
    monkeypatch.setattr(settings, "auto_assign", "round_robin")
    await database.users_col.insert_one(adjuster("u1", "a@x.com").model_dump())
    assert await choose_auto_assignee() == "u1"


def test_assignment_fields_stamp_and_unassigned_shape():
    fields = assignment_fields("u1")
    assert fields["assignee_id"] == "u1"
    assert fields["assigned_by"] == AUTO_ASSIGNER
    assert fields["assigned_at"]
    none_fields = assignment_fields(None)
    assert none_fields == {"assignee_id": None, "assigned_at": None, "assigned_by": None}


# ---- Claim creation assigns (AC-10.1) ----


async def test_submit_claim_auto_assigns_round_robin(
    patched_mongo, monkeypatch, make_authenticated_user
):
    monkeypatch.setattr(settings, "auto_assign", "round_robin")
    for uid, email in (("u1", "a@x.com"), ("u2", "b@x.com")):
        await database.users_col.insert_one(adjuster(uid, email).model_dump())

    ids = []
    with TestClient(server.app) as client:
        headers, _, auth_email = make_authenticated_user(client)
        for _ in range(4):
            response = client.post("/api/claims", json=SUBMISSION, headers=headers)
            assert response.status_code == 200, response.text
            doc = await database.claims_col.find_one(
                {"id": response.json()["claimId"]}, {"_id": 0}
            )
            ids.append(doc["assignee_id"])

    # Strict rotation over the whole active roster (u1, u2, and the registered
    # caller): never-assigned adjusters go first in id order, then the
    # least-recently-assigned one — u1 again on the fourth claim.
    auth_user = await database.users_col.find_one({"email": auth_email}, {"_id": 0, "id": 1})
    assert ids == ["u1", "u2", auth_user["id"], "u1"]
    doc = await database.claims_col.find_one({"id": response.json()["claimId"]}, {"_id": 0})
    assert doc["assigned_by"] == AUTO_ASSIGNER
    assert doc["assigned_at"]


async def test_submit_claim_unassigned_when_auto_assign_none(
    patched_mongo, monkeypatch, make_authenticated_user
):
    monkeypatch.setattr(settings, "auto_assign", "none")
    await database.users_col.insert_one(adjuster("u1", "a@x.com").model_dump())
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        response = client.post("/api/claims", json=SUBMISSION, headers=headers)
        assert response.status_code == 200
    doc = await database.claims_col.find_one({"id": response.json()["claimId"]}, {"_id": 0})
    assert doc["assignee_id"] is None
    assert doc["assigned_at"] is None
    assert doc["assigned_by"] is None


# ---- Manual reassignment endpoint (spec F10.2 / AC-10.2) ----


async def _seed_claim(cid="CLM-20260918-00001", assignee_id=None):
    await database.claims_col.insert_one(
        {
            **claim(
                cid,
                "pending",
                assignee_id,
                "2026-09-18T09:00:00+00:00" if assignee_id else None,
            ),
            "policy_number": "POL-1001",
        }
    )


async def _seed_adjusters():
    for uid, email in (("u1", "a@x.com"), ("u2", "b@x.com")):
        await database.users_col.insert_one(adjuster(uid, email).model_dump())


async def test_reassign_requires_adjuster_auth(patched_mongo):
    await _seed_claim()
    response = TestClient(server.app).post(
        "/api/workbench/claims/CLM-20260918-00001/assignee",
        json={"assigneeId": "u2", "reason": "handoff"},
    )
    assert response.status_code in (401, 403)


async def test_reassign_updates_claim_and_audits(
    patched_mongo, make_authenticated_user
):
    await _seed_claim()
    await _seed_adjusters()
    with TestClient(server.app) as client:
        headers, _, actor_email = make_authenticated_user(client)
        response = client.post(
            "/api/workbench/claims/CLM-20260918-00001/assignee",
            json={"assigneeId": "u2", "reason": "specialist on water damage"},
            headers=headers,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assigneeId"] == "u2"
    assert body["auditEntry"]["action"] == "reassign"

    doc = await database.claims_col.find_one({"id": "CLM-20260918-00001"}, {"_id": 0})
    assert doc["assignee_id"] == "u2"
    assert doc["assigned_by"] == actor_email  # human actor, not the picker
    audit = await database.audit_log_col.find_one(
        {"claim_id": "CLM-20260918-00001", "action": "reassign"}, {"_id": 0}
    )
    assert audit["after"]["assigneeId"] == "u2"
    assert audit["reason"] == "specialist on water damage"


async def test_reassign_unknown_user_404(patched_mongo, make_authenticated_user):
    await _seed_claim()
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        response = client.post(
            "/api/workbench/claims/CLM-20260918-00001/assignee",
            json={"assigneeId": "ghost", "reason": "rebalance"},
            headers=headers,
        )
    assert response.status_code == 404


async def test_reassign_unknown_claim_404(patched_mongo, make_authenticated_user):
    await _seed_adjusters()
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        response = client.post(
            "/api/workbench/claims/CLM-DOES-NOT-EXIST/assignee",
            json={"assigneeId": "u2", "reason": "rebalance"},
            headers=headers,
        )
    assert response.status_code == 404


async def test_reassign_inactive_or_customer_target_409(
    patched_mongo, make_authenticated_user
):
    await _seed_claim()
    await database.users_col.insert_one(adjuster("u9", "x@x.com", active=False).model_dump())
    await database.users_col.insert_one(
        {"id": "c1", "email": "cust@x.com", "role": "customer", "active": True,
         "password_hash": "hashed"}
    )
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        for target in ("u9", "c1"):
            response = client.post(
                "/api/workbench/claims/CLM-20260918-00001/assignee",
                json={"assigneeId": target, "reason": "rebalance"},
                headers=headers,
            )
            assert response.status_code == 409


async def test_reassign_requires_reason(patched_mongo, make_authenticated_user):
    await _seed_claim()
    await _seed_adjusters()
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        response = client.post(
            "/api/workbench/claims/CLM-20260918-00001/assignee",
            json={"assigneeId": "u2", "reason": "   "},
            headers=headers,
        )
    assert response.status_code == 422


async def test_reassign_emits_event(patched_mongo, make_authenticated_user):
    await _seed_claim()
    await _seed_adjusters()
    with TestClient(server.app) as client:
        headers, _, _ = make_authenticated_user(client)
        response = client.post(
            "/api/workbench/claims/CLM-20260918-00001/assignee",
            json={"assigneeId": "u2", "reason": "handoff"},
            headers=headers,
        )
    assert response.status_code == 200, response.text
    event = await database.events_col.find_one(
        {"claim_id": "CLM-20260918-00001", "event": "claim_reassigned"}, {"_id": 0}
    )
    assert event and event["data"]["assigneeId"] == "u2"


# ---- Queue Mine / Unassigned / All filters (spec F10.3) ----


def _params(assignee, current_user=None):
    params = QueueParams(assignee=assignee)
    params.current_user = current_user
    return params


def test_queue_filter_mine_matches_only_own_rows():
    rows = [
        {"assignee_id": "u1"},
        {"assignee_id": "u2"},
        {"assignee_id": None},
    ]
    filtered = _apply_row_filters(rows, _params("mine", adjuster("u1", "a@x.com")))
    assert [row["assignee_id"] for row in filtered] == ["u1"]


def test_queue_filter_unassigned_includes_legacy_rows():
    rows = [{"assignee_id": "u1"}, {"assignee_id": None}, {}]
    filtered = _apply_row_filters(rows, _params("unassigned", adjuster("u1", "a@x.com")))
    assert len(filtered) == 2


def test_queue_filter_all_omits_nothing():
    rows = [{"assignee_id": "u1"}, {"assignee_id": None}]
    assert len(_apply_row_filters(rows, _params("", adjuster("u1", "a@x.com")))) == 2


def test_queue_filter_rejects_unknown_value():
    with pytest.raises(Exception):
        QueueParams(assignee="someones")


def test_queue_filter_mine_needs_authentication_context():
    rows = [{"assignee_id": "u1"}]
    assert _apply_row_filters(rows, _params("mine")) == []


# ---- Customer portal path unchanged (AC-10.3) ----


async def test_status_portal_lookup_hides_assignment_fields(patched_mongo):
    """The public lookup returns the same deny-by-default projection whether
    or not the claim carries assignment fields (AC-10.3)."""
    await database.claims_col.insert_one(
        {
            **claim("CLM-20260918-00002", "pending", "u1", "2026-09-18T09:00:00+00:00"),
            "policy_number": "POL-1001",
            "access_code": "1234-5678-9012-3456",
            "access_code_hash": access_code_hash("1234-5678-9012-3456"),
        }
    )
    response = TestClient(server.app).post(
        "/api/status/lookup",
        json={"claimNumber": "CLM-20260918-00002", "accessCode": "1234-5678-9012-3456"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    flat = str(body)
    for forbidden in ("assignee_id", "assigneeId", "assigned_at", "assignedBy", "audit"):
        assert forbidden not in flat
