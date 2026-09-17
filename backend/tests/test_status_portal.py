"""Public status-portal tests: the access-code credential model.

Covers the spec's AC-8 security posture end to end on mongomock:
valid lookups return the masked payload only (first name + status), wrong
codes, unknown numbers, and codeless legacy claims all return the identical
generic 404 (nothing is enumerable), and the lookup endpoint rate-limits.

Covers: valid code, invalid code, rate-limited, non-enumerable errors.
"""

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import server
from app.config import settings
from app.status_portal import access_code_hash, generate_access_code, status_not_found

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
