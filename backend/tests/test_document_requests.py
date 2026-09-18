"""Document checklist tests (spec F3, AC-3.1).

Round-trips the adjuster surface: create → list → edit → waive, asserting
the stored document shape, the append-only audit row per mutation, the
durable event per mutation, and the auth contract (customer tokens 403).
"""

from fastapi.testclient import TestClient

import server

CLAIM_ID = "CLM-DOCREQ-1"


def _claim_doc(**overrides):
    doc = {
        "id": CLAIM_ID,
        "policy_number": "AUTO-2024-001847",
        "status": "escalated",
        "claimed_amount": 1200.0,
        "risk_score": 12,
        "is_historical": False,
        "created_at": "2026-09-17T00:00:00+00:00",
    }
    doc.update(overrides)
    return doc


def _requests_url(claim_id=CLAIM_ID, suffix=""):
    return f"/api/claims/{claim_id}/document-requests{suffix}"


def _request_doc(**overrides):
    doc = {
        "id": "dreq_abc123def456",
        "claim_id": CLAIM_ID,
        "title": "Repair estimate",
        "description": "A signed estimate from the shop.",
        "status": "requested",
        "requested_by": "usr_adjuster_1",
        "document_id": None,
        "created_at": "2026-09-17T10:00:00+00:00",
        "updated_at": "2026-09-17T10:00:00+00:00",
    }
    doc.update(overrides)
    return doc


async def _seed_request(patched_mongo, **overrides):
    await patched_mongo.document_requests.insert_one(_request_doc(**overrides))


class TestCreateDocumentRequest:
    async def test_creates_stored_document_with_requested_status(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, email = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(),
                json={"title": "Repair estimate", "description": "Signed, from the shop."},
                headers=headers,
            )

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["claim_id"] == CLAIM_ID
        assert data["title"] == "Repair estimate"
        assert data["description"] == "Signed, from the shop."
        assert data["status"] == "requested"
        assert data["requested_by"]  # stamped from the adjuster's user id
        assert data["document_id"] is None
        assert data["created_at"] == data["updated_at"]

        stored = await patched_mongo.document_requests.find_one(
            {"id": data["id"]}, {"_id": 0}
        )
        assert stored == data

    async def test_create_appends_audit_row_and_event(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, email = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(), json={"title": "Repair estimate"}, headers=headers
            )

        request_id = response.json()["id"]
        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(audits) == 1
        assert audits[0]["action"] == "document_request_created"
        assert audits[0]["actor_email"] == email
        assert audits[0]["after"]["title"] == "Repair estimate"
        assert audits[0]["after"]["status"] == "requested"
        assert audits[0]["reason"]  # every audit row carries a reason

        events = await patched_mongo.events.find({"claim_id": CLAIM_ID}).to_list(10)
        created = [e for e in events if e["event"] == "document_requested"]
        assert len(created) == 1
        assert created[0]["data"]["requestId"] == request_id

    async def test_description_defaults_to_empty(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(), json={"title": "Photos of the damage"}, headers=headers
            )
        assert response.status_code == 201
        assert response.json()["description"] == ""

    async def test_unknown_claim_is_404(self, patched_mongo, make_authenticated_user):
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url("CLM-NOPE"), json={"title": "Repair estimate"}, headers=headers
            )
        assert response.status_code == 404
        rows = await patched_mongo.document_requests.find({}).to_list(10)
        assert rows == []  # nothing written for a missing claim

    async def test_blank_title_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(), json={"title": "   "}, headers=headers
            )
        assert response.status_code == 422

    async def test_title_over_limit_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(), json={"title": "x" * 121}, headers=headers
            )
        assert response.status_code == 422

    async def test_description_over_limit_is_422(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _requests_url(),
                json={"title": "Repair estimate", "description": "x" * 2001},
                headers=headers,
            )
        assert response.status_code == 422

    async def test_requires_authentication(self, patched_mongo):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            response = client.post(_requests_url(), json={"title": "Repair estimate"})
        assert response.status_code == 401

    async def test_customer_role_is_403(self, patched_mongo, make_authenticated_user):
        """Customer tokens never touch the adjuster checklist (AC-3.1)."""
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="customer")
            create = client.post(
                _requests_url(), json={"title": "Repair estimate"}, headers=headers
            )
            listed = client.get(_requests_url(), headers=headers)
        assert create.status_code == 403
        assert listed.status_code == 403
        rows = await patched_mongo.document_requests.find({}).to_list(10)
        assert rows == []  # the 403 wrote nothing


class TestListDocumentRequests:
    async def test_lists_claim_requests_oldest_first(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(
            patched_mongo, id="dreq_first", created_at="2026-09-17T10:00:00+00:00"
        )
        await _seed_request(
            patched_mongo, id="dreq_second", created_at="2026-09-17T11:00:00+00:00"
        )
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.get(_requests_url(), headers=headers)

        assert response.status_code == 200
        assert [r["id"] for r in response.json()] == ["dreq_first", "dreq_second"]

    async def test_scoped_to_the_claim(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo, claim_id="CLM-OTHER")
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.get(_requests_url(), headers=headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_unknown_claim_is_404(self, patched_mongo, make_authenticated_user):
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.get(_requests_url("CLM-NOPE"), headers=headers)
        assert response.status_code == 404


class TestEditDocumentRequest:
    async def test_edits_title_and_description_with_audit(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, email = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"title": "Repair estimate (signed)", "description": "Include the VIN."},
                headers=headers,
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["title"] == "Repair estimate (signed)"
        assert data["description"] == "Include the VIN."
        assert data["status"] == "requested"
        assert data["updated_at"] >= data["created_at"]

        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(audits) == 1
        assert audits[0]["action"] == "document_request_updated"
        assert audits[0]["before"]["title"] == "Repair estimate"
        assert audits[0]["after"]["title"] == "Repair estimate (signed)"
        assert audits[0]["actor_email"] == email

    async def test_edit_emits_update_event(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"description": "Include the VIN."},
                headers=headers,
            )

        events = await patched_mongo.events.find({"claim_id": CLAIM_ID}).to_list(10)
        updated = [e for e in events if e["event"] == "document_request_updated"]
        assert len(updated) == 1
        assert updated[0]["data"]["requestId"] == "dreq_abc123def456"

    async def test_unknown_request_is_404(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_nope"), json={"title": "New"}, headers=headers
            )
        assert response.status_code == 404

    async def test_noop_patch_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"), json={}, headers=headers
            )
        assert response.status_code == 422
        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert audits == []  # a no-op mutation writes no audit row

    async def test_edit_of_waived_request_is_409(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo, status="waived")
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"title": "Reopen attempt"},
                headers=headers,
            )
        assert response.status_code == 409

    async def test_customer_role_is_403_on_patch(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="customer")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"title": "Customer edit"},
                headers=headers,
            )
        assert response.status_code == 403


class TestWaiveDocumentRequest:
    async def test_waive_sets_status_and_audits(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, email = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"waive": True, "reason": "Customer emailed the photos directly."},
                headers=headers,
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "waived"

        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(audits) == 1
        assert audits[0]["action"] == "document_request_waived"
        assert audits[0]["before"]["status"] == "requested"
        assert audits[0]["after"]["status"] == "waived"
        assert audits[0]["reason"] == "Customer emailed the photos directly."

        events = await patched_mongo.events.find({"claim_id": CLAIM_ID}).to_list(10)
        waived = [e for e in events if e["event"] == "document_request_waived"]
        assert len(waived) == 1
        assert waived[0]["data"]["waivedBy"] == email

    async def test_waive_without_reason_gets_default_audit_reason(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"waive": True},
                headers=headers,
            )
        assert response.status_code == 200
        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert audits[0]["reason"]  # audit rows always carry a reason

    async def test_waive_received_request_is_409_and_writes_nothing(
        self, patched_mongo, make_authenticated_user
    ):
        """'received' is the F4 upload's transition — waive cannot forge it."""
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(
            patched_mongo, status="received", document_id="doc_123"
        )
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"waive": True},
                headers=headers,
            )
        assert response.status_code == 409
        stored = await patched_mongo.document_requests.find_one(
            {"id": "dreq_abc123def456"}
        )
        assert stored["status"] == "received"  # untouched
        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert audits == []

    async def test_waive_twice_is_409(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        await _seed_request(patched_mongo)
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            first = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"waive": True},
                headers=headers,
            )
            second = client.patch(
                _requests_url(suffix="/dreq_abc123def456"),
                json={"waive": True},
                headers=headers,
            )
        assert first.status_code == 200
        assert second.status_code == 409
        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(audits) == 1  # the rejected second waive appended nothing


class TestFullLifecycleAudit:
    async def test_create_edit_waive_each_append_one_audit_row(
        self, patched_mongo, make_authenticated_user
    ):
        """AC-3.1 core: every mutation is audited, in order."""
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            created = client.post(
                _requests_url(), json={"title": "Repair estimate"}, headers=headers
            )
            request_id = created.json()["id"]
            client.patch(
                _requests_url(suffix=f"/{request_id}"),
                json={"description": "Signed and itemized."},
                headers=headers,
            )
            waived = client.patch(
                _requests_url(suffix=f"/{request_id}"), json={"waive": True}, headers=headers
            )
        assert waived.status_code == 200

        audits = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert [a["action"] for a in audits] == [
            "document_request_created",
            "document_request_updated",
            "document_request_waived",
        ]

        stored = await patched_mongo.document_requests.find_one({"id": request_id})
        assert stored["status"] == "waived"
        assert stored["description"] == "Signed and itemized."
