"""SLA escalation tests (spec F9, AC-9.1..AC-9.3).

Covers the three gates the spec calls out: escalated_at is set exactly once
across repeated evaluations and never cleared (AC-9.1), the bell notifies only
the assigned adjuster while unassigned escalations surface solely in the ops
analytics count plus the queue badge/filter (AC-9.2), and the threshold is the
SLA_ESCALATION_FACTOR multiplier on the severity's SLA window with 1.0 meaning
at-breach (AC-9.3). Runs on mongomock — no MongoDB, no LLM.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

import database
import server
from app.analytics import build_ops_analytics
from app.config import Settings, settings
from app.escalation import (
    ESCALATION_MILESTONE,
    escalation_due,
    sweep_escalations,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:
        # Row-level assertions are deterministic: start from no claims, even
        # though startup seeding creates demo ones.
        _run(database.claims_col.delete_many({}))
        yield test_client


class FakeDriver:
    """Captures sends; never raises (delivery assertions inspect the record)."""

    def __init__(self):
        self.sent = []

    async def send(self, notification):
        self.sent.append(notification)


@pytest.fixture
def fake_driver(monkeypatch):
    driver = FakeDriver()
    # escalation.py imports get_driver into its own namespace — patch it there.
    monkeypatch.setattr("app.escalation.get_driver", lambda: driver)
    return driver


def _claim_now():
    return datetime.now(timezone.utc)


def _make_claim(
    claim_id="CLM-ESCALATION-001",
    *,
    age_hours=80.0,
    status="pending",
    assignee_id=None,
    claimed_amount=1200.0,
    incident_type="theft",
    created_at=None,
    **overrides,
):
    """Insert a reviewable LOW-severity claim (theft ≤ $10k) aged past its 72h
    window by default — the exact shape the queue renders."""
    created = created_at or (_claim_now() - timedelta(hours=age_hours))
    claim = {
        "id": claim_id,
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": incident_type,
        "claimed_amount": claimed_amount,
        "status": status,
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
        "assignee_id": assignee_id,
        "agent_trace": {},
        "agent_logs": [],
        **overrides,
    }
    _run(database.claims_col.insert_one(claim.copy()))
    return claim


def _make_adjuster(user_id="usr_assignee1", email="assignee@test.example"):
    _run(
        database.users_col.insert_one(
            {
                "id": user_id,
                "email": email,
                "role": "adjuster",
                "active": True,
                "password_hash": "x",
                "created_at": _claim_now().isoformat(),
            }
        )
    )
    return {"id": user_id, "email": email}


# ============ AC-9.1: set exactly once, never cleared ============


def test_breach_sets_escalated_at_exactly_once_across_repeated_evaluations(client, fake_driver):
    _make_claim(age_hours=80.0)  # low severity: 72h window, factor 1.0 -> breached
    now = _claim_now()

    first = _run(sweep_escalations(now=now))
    second = _run(sweep_escalations(now=now))
    third = _run(sweep_escalations(now=now))

    assert first == ["CLM-ESCALATION-001"]
    assert second == []  # repeated evaluations are no-ops
    assert third == []
    stored = _run(database.claims_col.find_one({"id": "CLM-ESCALATION-001"}, {"_id": 0}))
    assert stored["escalated_at"]
    first_seen = stored["escalated_at"]
    # ...and a later sweep (real clock) cannot re-stamp or clear it.
    _run(sweep_escalations())
    stored_again = _run(database.claims_col.find_one({"id": "CLM-ESCALATION-001"}, {"_id": 0}))
    assert stored_again["escalated_at"] == first_seen


def test_escalation_record_survives_deciding_the_claim(client, fake_driver):
    _make_claim(age_hours=80.0)
    _run(sweep_escalations())
    before = _run(database.claims_col.find_one({"id": "CLM-ESCALATION-001"}, {"_id": 0}))["escalated_at"]

    # The claim leaves the reviewable set (a decision lands); the record stays.
    _run(
        database.claims_col.update_one(
            {"id": "CLM-ESCALATION-001"}, {"$set": {"status": "auto_approved"}}
        )
    )
    swept = _run(sweep_escalations())

    assert swept == []
    stored = _run(database.claims_col.find_one({"id": "CLM-ESCALATION-001"}, {"_id": 0}))
    assert stored["escalated_at"] == before  # clears never


def test_escalation_due_is_pure_and_never_proposes_a_second_stamp():
    now = _claim_now()
    claim = {
        "id": "CLM-PURE",
        "status": "pending",
        "created_at": (now - timedelta(hours=80)).isoformat(),
        "escalated_at": now.isoformat(),
        "claimed_amount": 1200.0,
        "incident_type": "theft",
        "agent_trace": {},
    }
    # An existing record is final: no evaluation proposes setting it again...
    assert escalation_due(claim, now=now) is False
    # ...while the record-less twin of the same claim is due.
    assert escalation_due({**claim, "escalated_at": None}, now=now) is True


# ============ AC-9.3: SLA_ESCALATION_FACTOR threshold ============


def test_default_factor_escalates_at_breach(client, fake_driver):
    # Low severity: 72h window. Just inside -> no escalation; at-breach -> yes.
    _make_claim("CLM-INSIDE", age_hours=71.0)
    _make_claim("CLM-AT-BREACH", age_hours=72.0)

    swept = _run(sweep_escalations())

    assert swept == ["CLM-AT-BREACH"]


def test_factor_multiplier_delays_escalation_past_the_window(client, fake_driver, monkeypatch):
    monkeypatch.setattr(settings, "sla_escalation_factor", 1.5)
    # 80h old: breached (72h) but below the 1.5x threshold (108h) — not escalated.
    _make_claim("CLM-BREACHED-ONLY", age_hours=80.0)
    # 110h old: past the 1.5x threshold — escalated.
    _make_claim("CLM-PAST-1.5X", age_hours=110.0)

    swept = _run(sweep_escalations())

    assert swept == ["CLM-PAST-1.5X"]
    stored = _run(database.claims_col.find_one({"id": "CLM-BREACHED-ONLY"}, {"_id": 0}))
    assert not stored.get("escalated_at")


def test_zero_or_negative_factor_fails_configuration():
    with pytest.raises(ValidationError):
        Settings(SLA_ESCALATION_FACTOR=0)
    with pytest.raises(ValidationError):
        Settings(SLA_ESCALATION_FACTOR=-0.5)


# ============ AC-9.2: routing — bell only for the assignee ============


def test_assigned_escalation_bell_notifies_only_the_assignee(client, fake_driver):
    assignee = _make_adjuster()
    other = _make_adjuster("usr_other", "other@test.example")
    _make_claim(age_hours=80.0, assignee_id=assignee["id"])

    swept = _run(sweep_escalations())

    assert swept == ["CLM-ESCALATION-001"]
    notifications = _run(
        database.notifications_col.find({}, {"_id": 0}).to_list(None)
    )
    # Exactly one notification, addressed to the assignee — never queue-wide.
    assert len(notifications) == 1
    assert notifications[0]["recipient_email"] == assignee["email"]
    assert notifications[0]["milestone"] == ESCALATION_MILESTONE
    assert "CLM-ESCALATION-001" in notifications[0]["body"]
    assert len(fake_driver.sent) == 1
    assert other["email"] not in [n["recipient_email"] for n in notifications]


def test_unassigned_escalation_never_notifies(client, fake_driver):
    _make_claim(age_hours=80.0, assignee_id=None)
    _make_adjuster()  # an adjuster exists, but nothing is assigned to them

    swept = _run(sweep_escalations())

    assert swept == ["CLM-ESCALATION-001"]
    notifications = _run(database.notifications_col.find({}, {"_id": 0}).to_list(None))
    assert notifications == []  # no bell for unassigned claims
    assert fake_driver.sent == []


def test_unknown_assignee_is_logged_not_raised(client, fake_driver):
    _make_claim(age_hours=80.0, assignee_id="usr_ghost")

    swept = _run(sweep_escalations())  # must not raise

    assert swept == ["CLM-ESCALATION-001"]
    notifications = _run(database.notifications_col.find({}, {"_id": 0}).to_list(None))
    assert notifications == []


# ============ AC-9.2: unassigned escalations surface in analytics ============


def test_analytics_escalation_count_routes_unassigned_claims():
    claims = [
        {"id": "A", "status": "pending", "escalated_at": "2026-09-19T00:00:00+00:00",
         "assignee_id": "usr_1", "incident_type": "theft", "claimed_amount": 100.0},
        {"id": "B", "status": "pending", "escalated_at": "2026-09-19T00:00:00+00:00",
         "assignee_id": None, "incident_type": "theft", "claimed_amount": 100.0},
        {"id": "C", "status": "pending", "incident_type": "theft",
         "claimed_amount": 100.0},  # never escalated
    ]

    result = build_ops_analytics(
        claims,
        [],
        low_types={"theft"},
        low_amount_threshold=10_000.0,
        sla_hours_by_severity={"low": 72.0, "elevated": 24.0},
    )

    assert result["escalations"] == {"total": 2, "unassigned": 1}


# ============ AC-9.2: queue badge data + Escalated filter ============


@pytest.fixture
def adjuster_headers(make_authenticated_user):
    def _make(client):
        headers, _csrf, _email = make_authenticated_user(client, role="adjuster")
        return headers

    return _make


def test_queue_escalated_filter_returns_only_escalated_rows(client, adjuster_headers):
    headers = adjuster_headers(client)
    _make_claim("CLM-ESCALATED", age_hours=80.0)
    _make_claim("CLM-FRESH", age_hours=1.0)
    _run(sweep_escalations())

    all_rows = client.get("/api/workbench/queue", headers=headers).json()["rows"]
    escalated_only = client.get(
        "/api/workbench/queue", params={"escalated": "yes"}, headers=headers
    ).json()["rows"]

    assert {row["id"] for row in all_rows} == {"CLM-ESCALATED", "CLM-FRESH"}
    assert [row["id"] for row in escalated_only] == ["CLM-ESCALATED"]
    assert escalated_only[0]["escalated_at"]
    fresh = next(row for row in all_rows if row["id"] == "CLM-FRESH")
    assert not fresh["escalated_at"]


def test_queue_escalated_filter_rejects_unknown_values(client, adjuster_headers):
    response = client.get(
        "/api/workbench/queue", params={"escalated": "bogus"}, headers=adjuster_headers(client)
    )
    assert response.status_code == 400


def test_case_summary_carries_escalated_at(client, adjuster_headers):
    headers = adjuster_headers(client)
    _make_claim(age_hours=80.0)
    _run(sweep_escalations())

    summary = client.get(
        "/api/workbench/claims/CLM-ESCALATION-001/summary", headers=headers
    ).json()

    assert summary["escalatedAt"]


def test_sweep_ignores_decided_and_fresh_claims(client, fake_driver):
    _make_claim("CLM-DECIDED", age_hours=200.0, status="auto_approved")
    _make_claim("CLM-FRESH", age_hours=1.0)

    swept = _run(sweep_escalations())

    assert swept == []  # only reviewable claims past the threshold escalate
