"""Access-code recovery (F1, AC-1.3): claim number + email exact match.

The recovery endpoint must be non-enumerable: a matching pair rotates the
code and emails the new one; every mismatch — unknown number, unknown email,
claim filed without an email — answers with the same fixed neutral response
and sends nothing. Response bytes for match and mismatch are asserted equal,
and the rotation is verified end to end (old code dies, new code works).
"""

import asyncio
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import server
from app.notifications import emails
from app.status_portal import access_code_hash, generate_access_code


def _run(coro):
    return asyncio.run(coro)


class FakeDriver:
    """Records send() calls; optionally fails to exercise degradation."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, notification):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(notification)


@pytest.fixture
def email_driver(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(emails, "get_driver", lambda: driver)
    return driver


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:
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
    claim_id="CLM-20260918-101",
    contact_email="sarah.chen@example.com",
    holder_name="Sarah Chen",
):
    """Insert one claim row as submission creates it; returns the code."""
    code = generate_access_code()
    now = _now()
    doc = {
        "id": claim_id,
        "policy_number": "AUTO-2024-001847",
        "claim_date": now,
        "incident_date": "2026-09-01",
        "incident_type": "accident",
        "claimed_amount": 1200.0,
        "status": "pending",
        "risk_score": 0,
        "decision_reason": "",
        "agent_trace": {},
        "agent_logs": [],
        "holder_name": holder_name,
        "contact_email": contact_email,
        "access_code": code,
        "access_code_hash": access_code_hash(code),
        "is_historical": False,
        "created_at": now,
    }
    _run(db.claims.insert_one(doc))
    return code


def _recover(client, claim_number, email):
    return client.post(
        "/api/status/recover-access-code",
        json={"claimNumber": claim_number, "contactEmail": email},
    )


NEUTRAL = {
    "status": "ok",
    "message": (
        "If this claim number and email match a claim on file, a new access code "
        "has been sent to that address."
    ),
}


# ============ Match: rotate + deliver ============


def test_match_rotates_the_code_and_emails_it(client, patched_mongo, email_driver):
    db = patched_mongo
    old_code = _make_claim(db)

    response = _recover(client, "CLM-20260918-101", "Sarah.Chen@Example.com")

    assert response.status_code == 200, response.text
    assert response.json() == NEUTRAL
    assert len(email_driver.sent) == 1
    sent = email_driver.sent[0]
    # Case-insensitive address match; delivery goes to the stored address.
    assert sent.recipient_email == "sarah.chen@example.com"
    assert sent.milestone == "access_code"
    new_code = sent.template_vars["access_code"]
    assert new_code != old_code

    # End to end: the new code opens the portal, the old code is dead.
    lookup_payload = {"claimNumber": "CLM-20260918-101", "accessCode": new_code}
    assert client.post("/api/status/lookup", json=lookup_payload).status_code == 200
    old_payload = {"claimNumber": "CLM-20260918-101", "accessCode": old_code}
    assert client.post("/api/status/lookup", json=old_payload).status_code == 404


def test_match_survives_a_failing_email_driver(client, patched_mongo, monkeypatch):
    # A driver outage degrades delivery, not the response: the customer must
    # not learn whether delivery succeeded, so the neutral answer stands.
    monkeypatch.setattr(emails, "get_driver", lambda: FakeDriver(fail=True))
    _make_claim(patched_mongo)

    response = _recover(client, "CLM-20260918-101", "sarah.chen@example.com")

    assert response.status_code == 200
    assert response.json() == NEUTRAL


# ============ Mismatch: identical neutral response, no email ============


def test_unknown_claim_number_is_neutral(client, patched_mongo, email_driver):
    response = _recover(client, "CLM-20260918-999", "sarah.chen@example.com")

    assert response.status_code == 200
    assert response.json() == NEUTRAL
    assert email_driver.sent == []


def test_wrong_email_is_neutral(client, patched_mongo, email_driver):
    _make_claim(patched_mongo, contact_email="sarah.chen@example.com")

    response = _recover(client, "CLM-20260918-101", "someone.else@example.com")

    assert response.status_code == 200
    assert response.json() == NEUTRAL
    assert email_driver.sent == []


def test_claim_without_email_is_neutral(client, patched_mongo, email_driver):
    _make_claim(patched_mongo, contact_email="")

    response = _recover(client, "CLM-20260918-101", "sarah.chen@example.com")

    assert response.status_code == 200
    assert response.json() == NEUTRAL
    assert email_driver.sent == []


def test_mismatch_response_is_byte_identical_to_match_response(client, patched_mongo, email_driver):
    """Non-enumerability: mismatch responses must not differ in any byte."""
    _make_claim(patched_mongo)
    match = _recover(client, "CLM-20260918-101", "sarah.chen@example.com")
    miss_number = _recover(client, "CLM-20260918-999", "sarah.chen@example.com")
    miss_email = _recover(client, "CLM-20260918-101", "nope@example.com")

    assert miss_number.content == match.content
    assert miss_email.content == match.content


def test_whitespace_is_stripped_before_matching(client, patched_mongo, email_driver):
    _make_claim(patched_mongo)

    response = _recover(client, "  CLM-20260918-101  ", "  sarah.chen@example.com ")

    assert response.status_code == 200
    assert len(email_driver.sent) == 1


# ============ Rate limit ============


def test_recovery_is_rate_limited(client, patched_mongo, email_driver, limiter_on):
    for _ in range(3):
        response = _recover(client, "CLM-20260918-999", "sarah.chen@example.com")
        assert response.status_code == 200

    fourth = _recover(client, "CLM-20260918-999", "sarah.chen@example.com")
    assert fourth.status_code == 429
    assert email_driver.sent == []
