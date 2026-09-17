"""FNOL wizard support APIs: drafts, trace, claim documents, evidence pack.

Drafts are authenticated key/value persistence for the wizard; trace and
documents assemble the claim-detail surfaces from durable state; the
evidence pack renders the full adjudication record as a PDF. All run on
mongomock — no live MongoDB, no LLM.
"""

import asyncio
import base64
import hashlib

import pytest
from starlette.testclient import TestClient

import server
from app.events import emit_event

DRAFT_ID = "draft-abc123xyz"


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        yield test_client


@pytest.fixture
def auth_headers(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    return headers


def seed_claim(patched_mongo, claim_id="CLM-000042", **overrides):
    claim = {
        "id": claim_id,
        "status": "completed",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "accident",
        "claimed_amount": 1200.0,
        "agent_trace": {
            "confidence": 0.87,
            "decision": {"verdict": "approved", "payout": 1200.0},
        },
        "agent_logs": [
            {"agent": "intake", "status": "done", "confidence": 0.95, "findings": []},
            {"agent": "policy", "status": "done", "confidence": 0.9, "findings": []},
        ],
    }
    claim.update(overrides)
    # mongomock_motor methods are coroutines — writes must actually run.
    asyncio.run(patched_mongo.claims.insert_one(claim.copy()))
    return claim


# ---- FNOL drafts ----


def test_draft_roundtrip(client, auth_headers):
    payload = {"data": {"policyNumber": "AUTO-2024-001847", "incidentType": "accident"}}
    put = client.put(f"/api/fnol/drafts/{DRAFT_ID}", json=payload, headers=auth_headers)
    assert put.status_code == 200
    assert put.json()["draftId"] == DRAFT_ID
    assert put.json()["updatedAt"]

    got = client.get(f"/api/fnol/drafts/{DRAFT_ID}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["data"] == payload["data"]


def test_draft_upsert_overwrites(client, patched_mongo, auth_headers):
    url = f"/api/fnol/drafts/{DRAFT_ID}"
    client.put(url, json={"data": {"step": 1}}, headers=auth_headers)
    client.put(url, json={"data": {"step": 2}}, headers=auth_headers)

    stored = asyncio.run(patched_mongo.fnol_drafts.find_one({"draft_id": DRAFT_ID}))
    assert stored["data"] == {"step": 2}
    assert asyncio.run(patched_mongo.fnol_drafts.count_documents({})) == 1


def test_draft_id_is_validated(client, auth_headers):
    # Regex-rejecting ids must fail closed, before any collection key is built.
    response = client.put("/api/fnol/drafts/bad id!", json={"data": {}}, headers=auth_headers)
    assert response.status_code == 400


def test_draft_missing_404(client, auth_headers):
    assert client.get(f"/api/fnol/drafts/{DRAFT_ID}", headers=auth_headers).status_code == 404


def test_draft_requires_auth(client):
    assert client.get(f"/api/fnol/drafts/{DRAFT_ID}").status_code == 401


def test_draft_delete(client, auth_headers):
    url = f"/api/fnol/drafts/{DRAFT_ID}"
    client.put(url, json={"data": {}}, headers=auth_headers)
    assert client.delete(url, headers=auth_headers).status_code == 200
    assert client.get(url, headers=auth_headers).status_code == 404


# ---- Claim trace ----


def test_trace_assembles_durable_state(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    asyncio.run(emit_event("CLM-000042", {"event": "run_started"}))
    asyncio.run(emit_event("CLM-000042", {"event": "agent_complete", "agent": "intake"}))
    asyncio.run(
        patched_mongo.claim_runs.insert_one(
            {"claim_id": "CLM-000042", "attempt": 1, "status": "completed", "created_at": "t1"}
        )
    )

    response = client.get("/api/claims/CLM-000042/trace", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["claimId"] == "CLM-000042"
    assert body["claimStatus"] == "completed"
    assert [e["event"] for e in body["events"]] == ["run_started", "agent_complete"]
    assert body["runs"][0]["attempt"] == 1
    assert body["agentLogs"][0]["agent"] == "intake"
    assert body["decision"]["verdict"] == "approved"


def test_trace_orders_runs_by_attempt(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    for attempt, status in ((2, "completed"), (1, "failed")):
        asyncio.run(
            patched_mongo.claim_runs.insert_one(
                {"claim_id": "CLM-000042", "attempt": attempt, "status": status, "created_at": f"t{attempt}"}
            )
        )

    runs = client.get("/api/claims/CLM-000042/trace", headers=auth_headers).json()["runs"]
    assert [r["attempt"] for r in runs] == [1, 2]


def test_trace_missing_claim_404(client, auth_headers):
    response = client.get("/api/claims/CLM-999999/trace", headers=auth_headers)
    assert response.status_code == 404


def test_trace_requires_auth(client):
    assert client.get("/api/claims/CLM-000042/trace").status_code == 401


# ---- Claim documents ----


def test_document_upload_roundtrip(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    payload = b"%PDF-1.4 minimal claim report"

    response = client.post(
        "/api/claims/CLM-000042/documents",
        files={"file": ("crash-report.pdf", payload, "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    meta = response.json()
    assert meta["filename"] == "crash-report.pdf"
    assert meta["sizeBytes"] == len(payload)
    assert meta["sha256"] == hashlib.sha256(payload).hexdigest()

    # Durable audit trail: exactly one document_uploaded event.
    events = asyncio.run(
        patched_mongo.events.count_documents(
            {"claim_id": "CLM-000042", "event": "document_uploaded"}
        )
    )
    assert events == 1


def test_document_reupload_is_idempotent(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    payload = b"%PDF-1.4 same bytes"
    files = {"file": ("crash-report.pdf", payload, "application/pdf")}

    first = client.post("/api/claims/CLM-000042/documents", files=files, headers=auth_headers)
    second = client.post("/api/claims/CLM-000042/documents", files=files, headers=auth_headers)
    assert first.json()["id"] == second.json()["id"]
    assert asyncio.run(patched_mongo.claim_documents.count_documents({})) == 1


def test_document_rejects_disallowed_type(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    response = client.post(
        "/api/claims/CLM-000042/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_document_rejects_oversize(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    response = client.post(
        "/api/claims/CLM-000042/documents",
        files={"file": ("big.pdf", b"x" * (10 * 1024 * 1024 + 1), "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_document_requires_existing_claim(client, auth_headers):
    response = client.post(
        "/api/claims/CLM-000042/documents",
        files={"file": ("report.pdf", b"%PDF", "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---- Evidence pack ----


def test_evidence_pack_returns_decodable_pdf(client, patched_mongo, auth_headers):
    seed_claim(patched_mongo)
    asyncio.run(emit_event("CLM-000042", {"event": "run_started"}))

    response = client.get("/api/claims/CLM-000042/evidence-pack", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "evidence-pack-CLM-000042.pdf"
    pdf = base64.b64decode(body["pdf"])
    assert pdf.startswith(b"%PDF-")


def test_evidence_pack_requires_auth(client):
    assert client.get("/api/claims/CLM-000042/evidence-pack").status_code == 401
