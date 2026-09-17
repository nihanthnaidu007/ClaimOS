"""API contract tests: validation 422s, probes, and dashboard golden values.

Route validation is the subject here; the pipeline is stubbed out so no LLM
is ever contacted.
"""

import pytest
from starlette.testclient import TestClient

import server

VALID_CLAIM = {
    "policyNumber": "AUTO-2024-001847",
    "holderName": "Sarah Chen",
    "incidentDate": "2026-09-01",
    "incidentType": "accident",
    "claimedAmount": 1200.0,
    "description": "Rear-end collision in a parking lot with a police report filed.",
    "contactEmail": "",
    "documentText": "",
}


class _StubOrchestrator:
    """Stands in for the LLM pipeline; route behavior is what's under test."""

    def __init__(self, claim_id, queue):
        self.claim_id = claim_id
        self.queue = queue

    async def run(self, form_data):
        return None


@pytest.fixture
def client(patched_mongo, monkeypatch):
    monkeypatch.setattr(server, "ClaimOrchestrator", _StubOrchestrator)
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        yield test_client


@pytest.fixture
def adjuster_headers(client, make_authenticated_user):
    """Bearer headers for an adjuster — every non-public route needs them now."""
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    return headers


def test_health_liveness(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_ready_reports_connected(client):
    assert client.get("/api/ready").json() == {"status": "ready", "database": "connected"}


def test_root_status(client):
    assert client.get("/api/").json() == {"status": "ok", "service": "ClaimOS API", "agents": 5}


def test_valid_submission_gets_claim_id(client, adjuster_headers):
    response = client.post("/api/claims", json=VALID_CLAIM, headers=adjuster_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["claimId"].startswith("CLM-")
    assert body["message"]


def test_missing_required_field_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "policyNumber": ""}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_negative_amount_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "claimedAmount": -5}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_zero_amount_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "claimedAmount": 0}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_amount_over_cap_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "claimedAmount": 5_000_001}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_bad_date_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "incidentDate": "not-a-date"}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_short_description_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "description": "too short"}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_bad_email_is_422(client, adjuster_headers):
    bad = {**VALID_CLAIM, "contactEmail": "not-an-email"}
    assert client.post("/api/claims", json=bad, headers=adjuster_headers).status_code == 422


def test_policies_seeded_and_listed(client, adjuster_headers):
    policies = client.get("/api/policies", headers=adjuster_headers).json()
    assert len(policies) == 10
    assert "AUTO-2024-001847" in {p["policy_number"] for p in policies}


def test_policy_lookup_by_number(client, adjuster_headers):
    response = client.get(
        "/api/policies/lookup",
        params={"policy_number": "AUTO-2024-001847"},
        headers=adjuster_headers,
    )
    assert response.status_code == 200
    assert response.json()["holder_name"] == "Sarah Chen"


def test_policy_lookup_404(client, adjuster_headers):
    response = client.get(
        "/api/policies/lookup",
        params={"policy_number": "MISSING-1"},
        headers=adjuster_headers,
    )
    assert response.status_code == 404


def test_dashboard_stats_golden_values(client, adjuster_headers):
    """Seeded ground truth: 15 claims, 13 approved, $121,150 total payout."""
    body = client.get("/api/dashboard/stats", headers=adjuster_headers).json()
    assert body["totalClaims"] == 15
    assert body["approved"] == 13
    assert body["rejected"] == 1
    assert body["underReview"] == 1
    assert body["pending"] == 0
    assert body["totalPayout"] == 121150.0
    assert body["activePolicies"] == 8
    assert len(body["recentClaims"]) == 5


def test_claims_list_returns_seed_history(client, adjuster_headers):
    claims = client.get("/api/claims", headers=adjuster_headers).json()
    assert isinstance(claims, list)
    assert len(claims) == 15
    assert all(c["is_historical"] for c in claims)
