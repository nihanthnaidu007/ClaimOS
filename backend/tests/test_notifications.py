"""Milestone fan-out and in-app notification center tests.

Covers the spec's notification requirements on mongomock: every pipeline
milestone (submitted, documents_received, decision_ready, payout_recorded)
fans out exactly one idempotent notification, the recipient is resolved from
the claim contact (policy holder fallback), a failing driver never breaks the
event write, and the authenticated list/mark-read endpoints are scoped to the
calling user.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

import server
from app import events as events_store
from app.notifications import fanout
from app.notifications.provider import Notification


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:
        yield test_client


class FakeDriver:
    """Captures sends; optionally raises to model driver outages."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, notification: Notification) -> None:
        if self.fail:
            raise RuntimeError("driver down")
        self.sent.append(notification)


@pytest.fixture
def fake_driver(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(fanout, "get_driver", lambda: driver)
    return driver


def _make_claim(db, claim_id="CLM-20260917-100", contact_email="", policy_number="AUTO-2024-001847"):
    now = datetime.now(timezone.utc).isoformat()
    _run(  # mongomock calls are coroutines
        db.claims.insert_one(
            {
                "id": claim_id,
                "policy_number": policy_number,
                "status": "pending",
                "holder_name": "Sam Riviera",
                "contact_email": contact_email,
                "created_at": now,
            }
        )
    )
    return claim_id


def _make_policy(db, policy_number="AUTO-2024-001847", holder_email="holder@example.com"):
    _run(db.policies.insert_one({"policy_number": policy_number, "holder_email": holder_email}))


def _insert_notification(db, claim_id, recipient, milestone="submitted", read=False):
    doc = {
        "id": f"ntf_{uuid4().hex[:12]}",
        "claim_id": claim_id,
        "recipient_email": recipient,
        "milestone": milestone,
        "title": fanout.MILESTONE_TITLES[milestone],
        "body": f"body-{milestone}",
        "read": read,
        "read_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _run(db.notifications.insert_one(doc))  # mongomock calls are coroutines
    return doc


# ============ Milestone mapping ============


def test_every_customer_milestone_maps_from_pipeline_events():
    assert fanout.milestone_for_event("claim_submitted", {}) == "submitted"
    assert (
        fanout.milestone_for_event("agent_complete", {"agent": "DOCUMENT_AGENT"})
        == "documents_received"
    )
    assert (
        fanout.milestone_for_event("agent_complete", {"agent": "DECISION_AGENT"})
        == "decision_ready"
    )
    assert fanout.milestone_for_event("payout_recorded", {}) == "payout_recorded"


def test_non_milestone_events_map_to_none():
    assert fanout.milestone_for_event("agent_start", {"agent": "POLICY_AGENT"}) is None
    assert fanout.milestone_for_event("agent_complete", {"agent": "INTAKE_AGENT"}) is None
    assert fanout.milestone_for_event("run_checkpoint", {}) is None


# ============ Fan-out behavior ============


def test_record_and_send_persists_once_and_dispatches(patched_mongo, fake_driver):
    db = patched_mongo
    _make_claim(db)

    doc = _run(fanout.record_and_send("CLM-20260917-100", "submitted", "sam@example.com"))
    assert doc is not None
    assert doc["recipient_email"] == "sam@example.com"
    assert len(fake_driver.sent) == 1
    assert fake_driver.sent[0].milestone == "submitted"

    # Replayed milestone (worker retry, event replay) must not double-notify.
    again = _run(fanout.record_and_send("CLM-20260917-100", "submitted", "sam@example.com"))
    assert again is None
    assert len(fake_driver.sent) == 1
    assert _run(db.notifications.count_documents({})) == 1


def test_dispatch_milestone_resolves_claim_contact_first(patched_mongo, fake_driver):
    db = patched_mongo
    _make_policy(db, holder_email="holder@example.com")
    _make_claim(db, contact_email="contact@example.com")

    _run(events_store.emit_event("CLM-20260917-100", {"event": "claim_submitted"}))
    doc = _run(db.notifications.find_one({"claim_id": "CLM-20260917-100"}))
    assert doc["recipient_email"] == "contact@example.com"


def test_dispatch_milestone_falls_back_to_policy_holder(patched_mongo, fake_driver):
    db = patched_mongo
    _make_policy(db, holder_email="holder@example.com")
    _make_claim(db, contact_email="")  # no claim-level contact

    _run(events_store.emit_event("CLM-20260917-100", {"event": "claim_submitted"}))
    doc = _run(db.notifications.find_one({"claim_id": "CLM-20260917-100"}))
    assert doc["recipient_email"] == "holder@example.com"


def test_each_milestone_fires_once_across_the_pipeline(patched_mongo, fake_driver):
    db = patched_mongo
    _make_claim(db, contact_email="sam@example.com")

    stream = [
        {"event": "claim_submitted"},
        {"event": "agent_start", "agent": "DOCUMENT_AGENT"},
        {"event": "agent_complete", "agent": "DOCUMENT_AGENT"},
        {"event": "agent_complete", "agent": "DECISION_AGENT"},
        {"event": "payout_recorded"},
    ]
    for event in stream:
        _run(events_store.emit_event("CLM-20260917-100", event))

    milestones = sorted(
        d["milestone"]
        for d in _run(db.notifications.find({}, {"milestone": 1}).to_list(100))
    )
    assert milestones == ["decision_ready", "documents_received", "payout_recorded", "submitted"]
    # agent_start never notified: exactly four records for five events.
    assert len(fake_driver.sent) == 4


def test_driver_failure_does_not_break_the_event_write(patched_mongo, monkeypatch):
    db = patched_mongo
    _make_claim(db)
    monkeypatch.setattr(fanout, "get_driver", lambda: FakeDriver(fail=True))

    seq = _run(events_store.emit_event("CLM-20260917-100", {"event": "claim_submitted"}))
    assert seq >= 1  # the event landed
    assert _run(events_store.get_claim_events("CLM-20260917-100"))[0]["event"] == "claim_submitted"
    # The notification record still exists for the in-app center.
    assert _run(db.notifications.count_documents({})) == 1


def test_events_for_unknown_claim_notify_nobody(patched_mongo, fake_driver):
    db = patched_mongo
    _run(events_store.emit_event("CLM-GHOST-000", {"event": "claim_submitted"}))
    doc = _run(db.notifications.find_one({"claim_id": "CLM-GHOST-000"}))
    assert doc is not None
    assert doc["recipient_email"] == ""


# ============ Notification center API ============


def test_notification_list_is_scoped_to_the_caller(client, patched_mongo, make_authenticated_user):
    db = patched_mongo
    mine = _insert_notification(db, "CLM-20260917-100", "mine@test.example", read=False)
    mine_read = _insert_notification(
        db, "CLM-20260917-100", "mine@test.example", milestone="decision_ready", read=True
    )
    # Distinct milestone: the (claim_id, milestone) unique index permits one
    # notification per claim milestone, so a same-milestone row would be
    # rejected before the scoping logic is ever exercised.
    _insert_notification(db, "CLM-20260917-100", "other@test.example", milestone="payout_recorded")

    headers, _, email = make_authenticated_user(client, role="customer")
    # The seeded notifications must be addressed to the *caller's* email.
    assert email.endswith("@test.example")
    _run(
        db.notifications.update_many(
            {"recipient_email": "mine@test.example"}, {"$set": {"recipient_email": email}}
        )
    )
    mine["recipient_email"] = email

    response = client.get("/api/notifications", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["unreadCount"] == 1
    # Both of the caller's rows return (newest first); the foreign one does not.
    assert [n["id"] for n in body["notifications"]] == [mine_read["id"], mine["id"]]
    assert body["notifications"][1]["read"] is False
    assert body["notifications"][1]["claimId"] == "CLM-20260917-100"


def test_mark_read_only_touches_own_unread_items(client, patched_mongo, make_authenticated_user):
    db = patched_mongo
    headers, _, email = make_authenticated_user(client, role="customer")
    mine_unread = _insert_notification(db, "CLM-20260917-100", email, milestone="submitted", read=False)
    mine_unread2 = _insert_notification(db, "CLM-20260917-100", email, milestone="payout_recorded", read=False)
    foreign = _insert_notification(db, "CLM-20260917-100", "other@test.example", milestone="decision_ready", read=False)

    response = client.post(
        "/api/notifications/mark-read",
        json={"ids": [mine_unread["id"], foreign["id"]]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"markedRead": 1}

    assert _run(db.notifications.find_one({"id": mine_unread["id"]}))["read"] is True
    assert _run(db.notifications.find_one({"id": foreign["id"]}))["read"] is False

    # Marking the rest clears the unread count.
    response = client.post(
        "/api/notifications/mark-read",
        json={"ids": [mine_unread2["id"]]},
        headers=headers,
    )
    assert response.json() == {"markedRead": 1}
    assert client.get("/api/notifications", headers=headers).json()["unreadCount"] == 0


def test_notification_endpoints_require_auth(client, patched_mongo):
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/mark-read", json={"ids": ["ntf_x"]}).status_code == 401


def test_full_fanout_reaches_the_notification_center(client, patched_mongo, make_authenticated_user, fake_driver):
    """End to end: pipeline event -> fan-out record -> the customer's bell list."""
    db = patched_mongo
    _make_claim(db, contact_email="")
    headers, _, email = make_authenticated_user(client, role="customer")
    _run(
        db.claims.update_one({"id": "CLM-20260917-100"}, {"$set": {"contact_email": email}})
    )

    _run(events_store.emit_event("CLM-20260917-100", {"event": "claim_submitted"}))

    body = client.get("/api/notifications", headers=headers).json()
    assert body["unreadCount"] == 1
    assert body["notifications"][0]["milestone"] == "submitted"
    assert body["notifications"][0]["title"] == "Claim submitted"
    assert len(fake_driver.sent) == 1
