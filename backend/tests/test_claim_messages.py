"""F5 claim messaging: threads, notifications, limits, scoping.

Covers the AC-5 acceptance set: thread order in both directions and the
provider-boundary notifications (AC-5.1), plain-text hygiene including the
length cap (AC-5.2), and the customer-side rate limit plus cross-claim
denial with generic 404s (AC-5.3). Hostile-string RENDERING is asserted by
the frontend RTL suites — here bodies are asserted to be stored verbatim
after control-char hygiene, which is what makes them render inert.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import database
import server
from app.config import settings
from app.status_portal import access_code_hash, status_not_found

TEST_ACCESS_CODE = "test-access-code-0123456789"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:5173"])
    with TestClient(server.app) as test_client:  # lifespan: seeding
        yield test_client


@pytest.fixture
def limiter_on():
    from app.rate_limit import limiter

    limiter.enabled = True
    yield
    limiter.enabled = False


@pytest.fixture
def email_calls(monkeypatch):
    """Record every reply-notice email; the real driver is never touched."""
    calls = []

    async def _fake(claim_number, recipient_email, sender_name, snippet):
        calls.append(
            {
                "claim": claim_number,
                "to": recipient_email,
                "sender": sender_name,
                "snippet": snippet,
            }
        )

    monkeypatch.setattr("app.claim_messages.send_reply_notice_email", _fake)
    return calls


def _seed_claim(
    claim_id="CLM-MSG-1",
    contact_email="",
    policy_number="AUTO-2024-001847",
    code=TEST_ACCESS_CODE,
):
    _run(
        database.claims_col.insert_one(
            {
                "id": claim_id,
                "status": "pending",
                "holder_name": "Test Holder",
                "contact_email": contact_email,
                "policy_number": policy_number,
                "access_code_hash": access_code_hash(code),
                "agent_trace": {},
            }
        )
    )
    if policy_number:
        _run(
            database.policies_col.update_one(
                {"policy_number": policy_number},
                {"$set": {"holder_email": "holder.policy@test.example"}},
                upsert=True,
            )
        )
    return claim_id


def _seed_adjuster(email="onduty-adjuster@test.example"):
    _run(
        database.users_col.insert_one(
            {
                "id": f"usr_{email}",
                "email": email,
                "password_hash": "not-a-real-hash",
                "role": "adjuster",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    return email


def _post_message(client, claim_id, body, headers):
    return client.post(
        f"/api/workbench/claims/{claim_id}/messages", json={"body": body}, headers=headers
    )


def _portal_send(client, claim_number, body, code=TEST_ACCESS_CODE):
    return client.post(
        f"/api/portal/claims/{claim_number}/messages",
        json={"accessCode": code, "body": body},
    )


# ---- Thread order, both directions (AC-5.1) ----


def test_thread_order_adjuster_then_customer(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    first = _post_message(client, "CLM-MSG-1", "First note", headers)
    assert first.status_code == 201, first.text
    second = _post_message(client, "CLM-MSG-1", "Second note", headers)
    assert second.status_code == 201, second.text

    listing = client.get("/api/workbench/claims/CLM-MSG-1/messages", headers=headers)
    assert listing.status_code == 200
    messages = listing.json()["messages"]
    assert [m["body"] for m in messages] == ["First note", "Second note"]
    assert all(m["authorRole"] == "adjuster" for m in messages)


def test_thread_order_customer_then_adjuster(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    portal = _portal_send(client, "CLM-MSG-1", "Customer question first")
    assert portal.status_code == 201, portal.text
    reply = _post_message(client, "CLM-MSG-1", "Adjuster answer", headers)
    assert reply.status_code == 201, reply.text

    messages = client.get(
        "/api/workbench/claims/CLM-MSG-1/messages", headers=headers
    ).json()["messages"]
    assert [m["authorRole"] for m in messages] == ["customer", "adjuster"]
    assert messages[0]["body"] == "Customer question first"


def test_list_thread_sorts_chronologically_regardless_of_insert_order(client):
    _seed_claim()
    base = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    docs = []
    for offset, body in [(2, "second"), (0, "first"), (5, "third")]:
        docs.append(
            {
                "id": f"msg_{offset}",
                "claim_id": "CLM-MSG-1",
                "author_role": "customer",
                "author_id": "portal-customer",
                "body": body,
                "created_at": (base + timedelta(seconds=offset)).isoformat(),
                "read_at": None,
            }
        )
    _run(database.claim_messages_col.insert_many(docs))

    from app.claim_messages import list_thread

    thread = _run(list_thread("CLM-MSG-1"))
    assert [d["body"] for d in thread] == ["first", "second", "third"]


# ---- Notifications through the F1 provider boundary (AC-5.1) ----


def test_adjuster_send_emails_customer(client, make_authenticated_user, email_calls):
    _seed_claim(contact_email="holder.contact@test.example")
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _post_message(client, "CLM-MSG-1", "We received your photos.", headers)
    assert response.status_code == 201

    assert len(email_calls) == 1
    sent = email_calls[0]
    assert sent["claim"] == "CLM-MSG-1"
    assert sent["to"] == "holder.contact@test.example"
    assert "photos" in sent["snippet"]


def test_adjuster_send_falls_back_to_policy_holder_email(
    client, make_authenticated_user, email_calls
):
    _seed_claim(contact_email="")
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _post_message(client, "CLM-MSG-1", "Contact-less claim", headers)
    assert response.status_code == 201

    assert len(email_calls) == 1
    assert email_calls[0]["to"] == "holder.policy@test.example"


def test_customer_send_emails_adjusters_and_rings_bell(client, email_calls):
    _seed_claim()
    adjuster_email = _seed_adjuster()
    response = _portal_send(client, "CLM-MSG-1", "Any update on my claim?")
    assert response.status_code == 201, response.text

    assert len(email_calls) == 1
    assert email_calls[0]["to"] == adjuster_email
    bells = _run(
        database.notifications_col.find({"milestone": "new_message"}, {"_id": 0}).to_list(10)
    )
    assert len(bells) == 1
    assert bells[0]["recipient_email"] == [adjuster_email]
    assert bells[0]["claim_id"] == "CLM-MSG-1"
    assert "Any update" in bells[0]["body"]


def test_second_customer_message_refreshes_the_bell(client, email_calls):
    """The (claim_id, milestone) unique index means one bell per claim: a new
    customer message upserts it (latest snippet, unread again) — never a
    duplicate-key failure and never a pile of stale bells."""
    _seed_claim()
    adjuster_email = _seed_adjuster()
    first = _portal_send(client, "CLM-MSG-1", "First customer message")
    assert first.status_code == 201, first.text
    second = _portal_send(client, "CLM-MSG-1", "Second customer message")
    assert second.status_code == 201, second.text

    bells = _run(
        database.notifications_col.find({"milestone": "new_message"}, {"_id": 0}).to_list(10)
    )
    assert len(bells) == 1
    assert bells[0]["recipient_email"] == [adjuster_email]
    assert "Second customer message" in bells[0]["body"]
    assert bells[0]["read"] is False


def test_adjuster_send_does_not_bell_adjusters(client, make_authenticated_user, email_calls):
    _seed_claim()
    _seed_adjuster()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _post_message(client, "CLM-MSG-1", "Reply from the team", headers)
    assert response.status_code == 201
    assert email_calls  # the customer was emailed

    bells = _run(database.notifications_col.find({"milestone": "new_message"}).to_list(10))
    assert bells == []


def test_driver_failure_does_not_fail_the_send(client, make_authenticated_user, monkeypatch):
    _seed_claim()
    _seed_adjuster()
    headers, _, _ = make_authenticated_user(client, role="adjuster")

    class ExplodingDriver:
        async def send(self, notification):
            raise RuntimeError("smtp down")

    from app.notifications import emails as emails_module

    monkeypatch.setattr(emails_module, "get_driver", lambda: ExplodingDriver())
    response = _post_message(client, "CLM-MSG-1", "Message despite outage", headers)
    assert response.status_code == 201
    thread = client.get(
        "/api/workbench/claims/CLM-MSG-1/messages", headers=headers
    ).json()
    assert thread["messages"][0]["body"] == "Message despite outage"


def test_send_journals_claim_message_posted_event(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    _post_message(client, "CLM-MSG-1", "On the record", headers)
    events = _run(
        database.events_col.find(
            {"claim_id": "CLM-MSG-1", "event": "claim_message_posted"}, {"_id": 0}
        ).to_list(10)
    )
    assert len(events) == 1
    assert events[0]["data"]["author_role"] == "adjuster"


# ---- Plain-text hygiene and the length cap (AC-5.2) ----


def test_sanitization_strips_control_chars_keeps_newlines(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    hostile = "line1\x00\x07\x1b[31mred\x00line2\r\n\tkept"
    response = _post_message(client, "CLM-MSG-1", hostile, headers)
    assert response.status_code == 201

    stored = response.json()["message"]["body"]
    assert "\x00" not in stored and "\x07" not in stored
    assert "\x1b" not in stored and "\r" not in stored
    assert "line1[31mred" in stored
    assert "\n\t" in stored


def test_length_cap_enforced(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    ok = _post_message(
        client, "CLM-MSG-1", "x" * settings.claim_message_max_chars, headers
    )
    assert ok.status_code == 201, ok.text
    too_long = _post_message(
        client, "CLM-MSG-1", "x" * (settings.claim_message_max_chars + 1), headers
    )
    assert too_long.status_code == 422


def test_empty_body_rejected(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _post_message(client, "CLM-MSG-1", "   \n\t  ", headers)
    assert response.status_code == 422


def test_hostile_body_stored_verbatim_after_hygiene(client, make_authenticated_user):
    """The XSS defense is rendering (React text nodes, RTL-tested); storage
    keeps the payload as-is minus control chars — no mangling, no surprises."""
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    payload = '<script>alert(1)</script><img src=x onerror="pwn()">'
    response = _post_message(client, "CLM-MSG-1", payload, headers)
    assert response.status_code == 201
    assert response.json()["message"]["body"] == payload


# ---- Rate limits and scoping (AC-5.3) ----


def test_portal_send_rate_limited(client, limiter_on):
    _seed_claim()
    # The endpoint's limit was captured from settings at import — exercise the
    # real default (portal_message_rate_limit) by going one over its budget.
    budget = int(settings.portal_message_rate_limit.split("/")[0])
    codes = [
        _portal_send(client, "CLM-MSG-1", f"Message {i}").status_code
        for i in range(budget)
    ]
    assert codes == [201] * budget
    assert _portal_send(client, "CLM-MSG-1", "One over the cap").status_code == 429


def test_portal_list_rate_limited_independently(client, limiter_on):
    _seed_claim()
    budget = int(settings.portal_message_rate_limit.split("/")[0])
    for _ in range(budget):
        response = client.post(
            "/api/portal/claims/CLM-MSG-1/messages/list",
            json={"accessCode": TEST_ACCESS_CODE},
        )
        assert response.status_code == 200
    response = client.post(
        "/api/portal/claims/CLM-MSG-1/messages/list", json={"accessCode": TEST_ACCESS_CODE}
    )
    assert response.status_code == 429


def test_cross_claim_access_denied_generic_404(client):
    _seed_claim(claim_id="CLM-MSG-1")
    other_code = "another-access-code-9876543210"
    _seed_claim(claim_id="CLM-MSG-2", code=other_code, policy_number="")

    # CLM-MSG-2's code must not unlock CLM-MSG-1's thread — list or send.
    response = client.post(
        "/api/portal/claims/CLM-MSG-1/messages/list", json={"accessCode": other_code}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": status_not_found()["detail"]}
    response = client.post(
        "/api/portal/claims/CLM-MSG-1/messages",
        json={"accessCode": other_code, "body": "sneak"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": status_not_found()["detail"]}


def test_portal_unknown_claim_generic_404(client):
    response = client.post(
        "/api/portal/claims/CLM-NONE/messages/list", json={"accessCode": TEST_ACCESS_CODE}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": status_not_found()["detail"]}


def test_adjuster_routes_require_authentication(client):
    _seed_claim()
    assert client.get("/api/workbench/claims/CLM-MSG-1/messages").status_code == 401
    assert (
        client.post("/api/workbench/claims/CLM-MSG-1/messages", json={"body": "hi"}).status_code
        == 401
    )


def test_customer_role_forbidden_on_adjuster_routes(client, make_authenticated_user):
    _seed_claim()
    headers, _, _ = make_authenticated_user(client, role="customer")
    response = client.get("/api/workbench/claims/CLM-MSG-1/messages", headers=headers)
    assert response.status_code == 403


def test_adjuster_unknown_claim_generic_404(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = client.get(
        "/api/workbench/claims/CLM-DOES-NOT-EXIST/messages", headers=headers
    )
    assert response.status_code == 404


# ---- Read receipts ----


def test_read_receipts_stamp_the_other_party(client, make_authenticated_user):
    _seed_claim()
    _seed_adjuster()
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    _portal_send(client, "CLM-MSG-1", "Customer writes")

    listing = client.get("/api/workbench/claims/CLM-MSG-1/messages", headers=headers)
    assert listing.json()["messages"][0]["readAt"] is not None

    _post_message(client, "CLM-MSG-1", "Team reply", headers)
    portal = client.post(
        "/api/portal/claims/CLM-MSG-1/messages/list", json={"accessCode": TEST_ACCESS_CODE}
    )
    by_role = {m["authorRole"]: m for m in portal.json()["messages"]}
    assert by_role["adjuster"]["readAt"] is not None
    assert by_role["customer"]["readAt"] is not None


# ---- Portal payload flag ----


def test_public_status_payload_reports_messaging_enabled():
    from app.status_portal import public_status_payload

    payload = public_status_payload(
        {"id": "C1", "holder_name": "Jo", "status": "pending", "agent_trace": {}}, []
    )
    assert payload["messagesEnabled"] is True
