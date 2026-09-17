"""Settlement recording tests (spec: record-only fields + audit_log entry).

No money moves anywhere in these tests — the endpoint is the system of
record: it stamps the claim, appends an audit row, and emits one event.
"""

from fastapi.testclient import TestClient

import server

SETTLE_BODY = {
    "amount": 1150.0,
    "method": "bank_transfer",
    "reference": "BNK-2026-000123",
}
CLAIM_ID = "CLM-SETTLE-1"


def _claim_doc(**overrides):
    doc = {
        "id": CLAIM_ID,
        "policy_number": "AUTO-2024-001847",
        "status": "approved",
        "claimed_amount": 1200.0,
        "risk_score": 12,
        "is_historical": False,
        "created_at": "2026-09-17T00:00:00+00:00",
    }
    doc.update(overrides)
    return doc


def _settle(**overrides):
    return {**SETTLE_BODY, **overrides}


def _settle_url(claim_id=CLAIM_ID):
    return f"/api/claims/{claim_id}/settlement"


class TestRecordSettlement:
    async def test_records_fields_and_audit_entry(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, email = make_authenticated_user(client, role="adjuster")
            response = client.post(_settle_url(), json=_settle(), headers=headers)

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["claimId"] == CLAIM_ID
        assert data["status"] == "approved"
        assert data["settlement"] == {
            "amount": 1150.0,
            "method": "bank_transfer",
            "reference": "BNK-2026-000123",
            "settled_at": data["settlement"]["settled_at"],  # server-stamped
            "recorded_by": email,
        }
        assert data["settlement"]["settled_at"]

        audit = data["auditEntry"]
        assert audit["claim_id"] == CLAIM_ID
        assert audit["action"] == "settlement_recorded"
        assert audit["actor_email"] == email
        assert audit["after"]["amount"] == 1150.0
        assert audit["reason"] == "BNK-2026-000123"

        # Stored audit_log row matches the response entry (append-only).
        rows = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(rows) == 1
        assert rows[0]["action"] == "settlement_recorded"
        assert rows[0]["after"]["method"] == "bank_transfer"

    async def test_claim_document_gains_settlement(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            client.post(_settle_url(), json=_settle(), headers=headers)

        stored = await patched_mongo.claims.find_one({"id": CLAIM_ID})
        assert stored["settlement"]["amount"] == 1150.0
        assert stored["settlement"]["reference"] == "BNK-2026-000123"

    async def test_durable_event_emitted(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            client.post(_settle_url(), json=_settle(), headers=headers)

        events = await patched_mongo.events.find({"claim_id": CLAIM_ID}).to_list(100)
        assert [e["event"] for e in events if e["event"] == "settlement_recorded"] == [
            "settlement_recorded"
        ]

    async def test_reference_is_optional(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _settle_url(), json=_settle(reference=""), headers=headers
            )

        assert response.status_code == 201
        assert response.json()["settlement"]["reference"] == ""
        audit = response.json()["auditEntry"]
        assert audit["reason"]  # audit rows always carry a reason

    async def test_unknown_claim_is_404(self, patched_mongo, make_authenticated_user):
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _settle_url("CLM-NOPE"), json=_settle(), headers=headers
            )
        assert response.status_code == 404

    async def test_second_settlement_is_409_and_appends_nothing(
        self, patched_mongo, make_authenticated_user
    ):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            first = client.post(_settle_url(), json=_settle(), headers=headers)
            second = client.post(
                _settle_url(), json=_settle(amount=10.0, method="cheque"), headers=headers
            )

        assert first.status_code == 201
        assert second.status_code == 409

        rows = await patched_mongo.audit_log.find({"claim_id": CLAIM_ID}).to_list(10)
        assert len(rows) == 1  # append-only: the rejected attempt wrote nothing
        stored = await patched_mongo.claims.find_one({"id": CLAIM_ID})
        assert stored["settlement"]["amount"] == 1150.0

    async def test_negative_amount_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _settle_url(), json=_settle(amount=-1.0), headers=headers
            )
        assert response.status_code == 422

    async def test_unknown_method_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(
                _settle_url(), json=_settle(method="wire_transfer"), headers=headers
            )
        assert response.status_code == 422

    async def test_missing_amount_is_422(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="adjuster")
            response = client.post(_settle_url(), json={"method": "upi"}, headers=headers)
        assert response.status_code == 422

    async def test_requires_authentication(self, patched_mongo):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            response = client.post(_settle_url(), json=_settle())
        assert response.status_code == 401

    async def test_customer_role_is_403(self, patched_mongo, make_authenticated_user):
        await patched_mongo.claims.insert_one(_claim_doc())
        with TestClient(server.app) as client:
            headers, _, _ = make_authenticated_user(client, role="customer")
            response = client.post(_settle_url(), json=_settle(), headers=headers)
        assert response.status_code == 403
