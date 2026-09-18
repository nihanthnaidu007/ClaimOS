"""Portal document upload tests (spec F4, AC-4.1..AC-4.3).

Round-trips the customer surface on the same mock Mongo the adjuster tests
use: upload → stored document → request received → audit + durable event →
adjuster bell, plus every rejection path (oversized, disallowed type, empty,
wrong code, cross-claim request id, closed request). The cross-claim case is
the security core: a valid code for claim B must not even reveal that claim
A's request id exists — same generic 404 as a wrong code.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import server
from app.config import settings
from app.storage import LocalFsProvider
from app.status_portal import access_code_hash, generate_access_code

CLAIM_ID = "CLM-20260918-010"
OTHER_CLAIM_ID = "CLM-20260918-011"
REQUEST_ID = "dreq_portal01"
OTHER_REQUEST_ID = "dreq_other999"
ADJUSTER_EMAIL = "adjuster-on-file@test.example"
ADJUSTER_USER_ID = "usr_adj_1"
PDF = b"%PDF-1.4 fake customer estimate"
GENERIC_404 = "No claim found for that claim number and access code"


def _run(coro):
    """Sync tests reach the mock collections through asyncio.run — mongomock
    mirrors motor's async API (insert_one/find_one are coroutines)."""
    return asyncio.run(coro)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_doc(claim_id=CLAIM_ID, contact_email="sarah.chen@example.com"):
    code = generate_access_code()
    now = _now()
    doc = {
        "id": claim_id,
        "policy_number": "AUTO-2024-001847",
        "claim_date": now,
        "incident_date": "2026-09-01",
        "incident_type": "accident",
        "claimed_amount": 4200.0,
        "status": "escalated",
        "risk_score": 12,
        "decision_reason": "",
        "agent_trace": {},
        "agent_logs": [],
        "holder_name": "Sarah Chen",
        "contact_email": contact_email,
        "is_historical": False,
        "created_at": now,
        "access_code": code,
        "access_code_hash": access_code_hash(code),
    }
    return doc, code


def _request_doc(**overrides):
    doc = {
        "id": REQUEST_ID,
        "claim_id": CLAIM_ID,
        "title": "Repair estimate",
        "description": "A signed estimate from the shop.",
        "status": "requested",
        "requested_by": ADJUSTER_USER_ID,
        "document_id": None,
        "created_at": "2026-09-18T10:00:00+00:00",
        "updated_at": "2026-09-18T10:00:00+00:00",
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Point the process-wide provider at a temp root (same as test_document_upload)."""
    import app.storage.provider as provider_module

    root = tmp_path / "uploads"
    monkeypatch.setattr(provider_module, "_provider", LocalFsProvider(root=root))
    return root


@pytest.fixture
def client(patched_mongo, local_storage):
    with TestClient(server.app) as test_client:
        yield test_client


def _seed(db, claim_id=CLAIM_ID, **request_overrides):
    """Insert the claim under test, its document request, and the requester."""
    doc, code = _claim_doc(claim_id=claim_id)
    _run(db.claims.insert_one(doc))
    _run(db.document_requests.insert_one(_request_doc(**request_overrides)))
    _run(
        db.users.insert_one(
            {"id": ADJUSTER_USER_ID, "email": ADJUSTER_EMAIL, "role": "adjuster"}
        )
    )
    return code


def _code_of(db, claim_id=CLAIM_ID) -> str:
    row = _run(db.claims.find_one({"id": claim_id}, {"access_code": 1}))
    return row["access_code"]


def _upload(client, code, claim_number=CLAIM_ID, request_id=REQUEST_ID):
    return client.post(
        "/api/status/upload-document",
        data={
            "claimNumber": claim_number,
            "accessCode": code,
            "requestId": request_id,
        },
        files={"file": ("estimate.pdf", PDF, "application/pdf")},
    )


class TestPortalUploadRoundTrip:
    def test_upload_links_document_and_receives_request(self, client, patched_mongo):
        code = _seed(patched_mongo)

        response = _upload(client, code)

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["requestId"] == REQUEST_ID
        assert data["status"] == "received"
        assert data["filename"] == "estimate.pdf"
        assert data["sizeBytes"] == len(PDF)

        stored = _run(
            patched_mongo.claim_documents.find_one(
                {"id": data["documentId"]}, {"_id": 0}
            )
        )
        assert stored["claim_id"] == CLAIM_ID
        assert stored["document_request_id"] == REQUEST_ID
        assert stored["document_type"] == "customer_upload"
        assert stored["size_bytes"] == len(PDF)
        assert stored["sha256"]
        assert stored["uploaded_by"] == "sarah.chen@example.com"

        request_row = _run(
            patched_mongo.document_requests.find_one({"id": REQUEST_ID}, {"_id": 0})
        )
        assert request_row["status"] == "received"
        assert request_row["document_id"] == data["documentId"]

    def test_upload_appends_audit_row_and_event(self, client, patched_mongo):
        code = _seed(patched_mongo)

        response = _upload(client, code)
        data = response.json()

        audits = _run(
            patched_mongo.audit_log.find(
                {"claim_id": CLAIM_ID, "action": "document_request_received"}
            ).to_list(10)
        )
        assert len(audits) == 1
        assert audits[0]["actor"] == "customer"
        assert audits[0]["before"] == {"status": "requested", "document_id": None}
        assert audits[0]["after"]["document_id"] == data["documentId"]

        events = _run(
            patched_mongo.events.find({"claim_id": CLAIM_ID}).to_list(10)
        )
        received = [e for e in events if e["event"] == "document_request_received"]
        assert len(received) == 1
        assert received[0]["data"]["requestId"] == REQUEST_ID
        assert received[0]["data"]["documentId"] == data["documentId"]

    def test_upload_notifies_requesting_adjuster(self, client, patched_mongo):
        code = _seed(patched_mongo)

        _upload(client, code)

        notifications = _run(
            patched_mongo.notifications.find(
                {"claim_id": CLAIM_ID, "milestone": "document_request_received"}
            ).to_list(10)
        )
        assert len(notifications) == 1
        assert notifications[0]["recipient_email"] == ADJUSTER_EMAIL
        assert not notifications[0]["read"]
        assert "Repair estimate" in notifications[0]["body"]

    def test_upload_without_requester_skips_notification(
        self, client, patched_mongo
    ):
        _seed(patched_mongo, requested_by=None)

        response = _upload(client, _code_of(patched_mongo))

        assert response.status_code == 201, response.text
        assert (
            _run(
                patched_mongo.notifications.count_documents(
                    {"claim_id": CLAIM_ID}
                )
            )
            == 0
        )


class TestPortalUploadRejections:
    def test_oversized_file_is_413_and_request_stays_requested(
        self, client, patched_mongo
    ):
        code = _seed(patched_mongo)
        oversized = b"x" * (settings.upload_max_bytes + 1)

        response = client.post(
            "/api/status/upload-document",
            data={
                "claimNumber": CLAIM_ID,
                "accessCode": code,
                "requestId": REQUEST_ID,
            },
            files={"file": ("big.pdf", oversized, "application/pdf")},
        )

        assert response.status_code == 413
        row = _run(patched_mongo.document_requests.find_one({"id": REQUEST_ID}))
        assert row["status"] == "requested"
        assert (
            _run(patched_mongo.claim_documents.count_documents({"claim_id": CLAIM_ID}))
            == 0
        )

    def test_disallowed_content_type_is_415(self, client, patched_mongo):
        code = _seed(patched_mongo)

        response = client.post(
            "/api/status/upload-document",
            data={
                "claimNumber": CLAIM_ID,
                "accessCode": code,
                "requestId": REQUEST_ID,
            },
            files={"file": ("notes.txt", b"plain text", "text/plain")},
        )

        assert response.status_code == 415
        row = _run(patched_mongo.document_requests.find_one({"id": REQUEST_ID}))
        assert row["status"] == "requested"

    def test_empty_file_is_422(self, client, patched_mongo):
        code = _seed(patched_mongo)

        response = client.post(
            "/api/status/upload-document",
            data={
                "claimNumber": CLAIM_ID,
                "accessCode": code,
                "requestId": REQUEST_ID,
            },
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )

        assert response.status_code == 422

    def test_already_received_request_is_409(self, client, patched_mongo):
        code = _seed(patched_mongo, status="received", document_id="doc_existing01")

        response = _upload(client, code)

        assert response.status_code == 409
        row = _run(patched_mongo.document_requests.find_one({"id": REQUEST_ID}))
        assert row["document_id"] == "doc_existing01"  # untouched

    def test_waived_request_is_409(self, client, patched_mongo):
        code = _seed(patched_mongo, status="waived")

        response = _upload(client, code)

        assert response.status_code == 409


class TestAccessCodeScoping:
    def test_wrong_access_code_is_generic_404(self, client, patched_mongo):
        _seed(patched_mongo)

        response = _upload(client, "WRONG-CODE-99")

        assert response.status_code == 404
        assert response.json()["detail"] == GENERIC_404

    def test_request_from_another_claim_is_indistinguishable_404(
        self, client, patched_mongo
    ):
        """The cross-claim core (AC-4.3): claim B's code + claim A's request id.

        Must 404 exactly like a wrong code — no leak that the request id
        exists — and claim A's request must stay untouched.
        """
        code_a = _seed(patched_mongo)
        other_doc, code_b = _claim_doc(claim_id=OTHER_CLAIM_ID)
        _run(patched_mongo.claims.insert_one(other_doc))
        _run(
            patched_mongo.document_requests.insert_one(
                _request_doc(
                    id=OTHER_REQUEST_ID,
                    claim_id=OTHER_CLAIM_ID,
                    title="Other claim's estimate",
                )
            )
        )

        # B's valid credentials, A's request id.
        response = _upload(client, code_b, request_id=REQUEST_ID)

        assert response.status_code == 404
        assert response.json()["detail"] == GENERIC_404
        row = _run(patched_mongo.document_requests.find_one({"id": REQUEST_ID}))
        assert row["status"] == "requested"  # A's request untouched
        assert (
            _run(patched_mongo.claim_documents.count_documents({})) == 0
        )  # neither claim got a document
        assert _run(patched_mongo.audit_log.count_documents({})) == 0
        # ...and A's own code still works (sanity on the seeded state).
        assert _upload(client, code_a).status_code == 201

    def test_unknown_request_id_is_generic_404(self, client, patched_mongo):
        code = _seed(patched_mongo)

        response = _upload(client, code, request_id="dreq_missing000")

        assert response.status_code == 404
        assert response.json()["detail"] == GENERIC_404


class TestLookupCarriesChecklist:
    def test_lookup_returns_public_projection_only(self, client, patched_mongo):
        """AC-4.1's read side: the lookup lists the claim's requests, projected
        to the public fields — never requested_by, document_id, or timestamps."""
        code = _seed(patched_mongo, status="received", document_id="doc_existing01")

        response = client.post(
            "/api/status/lookup",
            json={"claimNumber": CLAIM_ID, "accessCode": code},
        )

        assert response.status_code == 200, response.text
        requests = response.json()["documentRequests"]
        assert requests == [
            {
                "id": REQUEST_ID,
                "title": "Repair estimate",
                "description": "A signed estimate from the shop.",
                "status": "received",
            }
        ]

    def test_lookup_without_requests_returns_empty_list(self, client, patched_mongo):
        doc, code = _claim_doc()
        _run(patched_mongo.claims.insert_one(doc))

        response = client.post(
            "/api/status/lookup",
            json={"claimNumber": CLAIM_ID, "accessCode": code},
        )

        assert response.status_code == 200
        assert response.json()["documentRequests"] == []
