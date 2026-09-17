"""Auth backend tests: registration gating, login, refresh rotation with
reuse detection, role enforcement (401/403), CSRF rejection, rate limits,
and demo-user seeding. Runs entirely on mongomock — no MongoDB needed.

Covers spec AC-1: every non-public endpoint 401s unauthenticated and 403s
cross-role; login, refresh rotation, logout, and CSRF rejection are covered.
"""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from starlette.testclient import TestClient

import server
from app.config import settings
from app.deps import CSRF_COOKIE, REFRESH_COOKIE, require_customer
from app.schemas import UserRecord

from conftest import TEST_INVITE_CODE, TEST_PASSWORD


def _run(coro):
    """Sync tests reach the mock collections through asyncio.run — mongomock
    mirrors motor's async API (find_one/count_documents are coroutines)."""
    import asyncio

    return asyncio.run(coro)

ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:8001"]


class _StubOrchestrator:
    """Route behavior is under test; the LLM pipeline is not."""

    def __init__(self, claim_id, queue):
        self.claim_id = claim_id
        self.queue = queue

    async def run(self, form_data):
        return None


@pytest.fixture
def client(patched_mongo, monkeypatch):
    monkeypatch.setattr(server, "ClaimOrchestrator", _StubOrchestrator)
    # Explicit origin allowlist so the CSRF Origin/Referer check is exercised
    # (a wildcard allowlist bypasses it — that mode is development-only).
    monkeypatch.setattr(settings, "cors_origins", list(ALLOWED_ORIGINS))
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        yield test_client


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


# ============ Registration (invite-only) ============


def test_register_with_valid_invite_creates_user(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "new.adjuster@test.example",
            "password": TEST_PASSWORD,
            "role": "adjuster",
            "inviteCode": TEST_INVITE_CODE,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.adjuster@test.example"
    assert body["role"] == "adjuster"
    assert "password_hash" not in body  # hash never crosses the wire


def test_register_with_wrong_invite_code_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "intruder@test.example",
            "password": TEST_PASSWORD,
            "role": "adjuster",
            "inviteCode": "wrong-code",
        },
    )
    assert response.status_code == 403


def test_register_without_invite_code_configured_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "invite_code", "")
    response = client.post(
        "/api/auth/register",
        json={
            "email": "anyone@test.example",
            "password": TEST_PASSWORD,
            "role": "customer",
            "inviteCode": "",
        },
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_register_duplicate_email_conflict(client):
    payload = {
        "email": "dup@test.example",
        "password": TEST_PASSWORD,
        "role": "customer",
        "inviteCode": TEST_INVITE_CODE,
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_register_rejects_short_password(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "shortpw@test.example",
            "password": "short",
            "role": "adjuster",
            "inviteCode": TEST_INVITE_CODE,
        },
    )
    assert response.status_code == 422


def test_password_stored_hashed(client, patched_mongo):
    client.post(
        "/api/auth/register",
        json={
            "email": "hashcheck@test.example",
            "password": TEST_PASSWORD,
            "role": "adjuster",
            "inviteCode": TEST_INVITE_CODE,
        },
    )
    doc = _run(patched_mongo.users.find_one({"email": "hashcheck@test.example"}))
    assert doc["password_hash"].startswith("$2b$")
    assert TEST_PASSWORD not in doc["password_hash"]


# ============ Login ============


def test_login_returns_access_token_and_cookies(client, make_authenticated_user):
    _, _, email = make_authenticated_user(client, role="adjuster")
    # Re-login to inspect the raw login response.
    response = client.post(
        "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tokenType"] == "bearer"
    assert body["accessToken"]
    assert body["expiresInSeconds"] == settings.access_token_ttl_minutes * 60
    assert body["user"]["role"] == "adjuster"
    assert "password_hash" not in body["user"]

    set_cookies = "\n".join(response.headers.get_list("set-cookie"))
    assert f"{REFRESH_COOKIE}=" in set_cookies
    assert "HttpOnly" in set_cookies
    assert "SameSite=lax" in set_cookies
    assert "Path=/api/auth" in set_cookies
    assert "Secure" not in set_cookies  # development default is http-safe


def test_login_wrong_password_generic_401(client, make_authenticated_user):
    _, _, email = make_authenticated_user(client, role="adjuster")
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "WrongPassword1!"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_same_message(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "ghost@test.example", "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    # Same generic message as wrong-password: no account enumeration.
    assert response.json()["detail"] == "Invalid email or password"


# ============ Route protection: 401 unauthenticated ============


PROTECTED_GETS = [
    "/api/claims",
    "/api/claims/CLM-X",
    "/api/claims/CLM-X/pdf",
    "/api/claims/stream/CLM-X",
    "/api/policies",
    "/api/policies/lookup?policy_number=AUTO-2024-001847",
    "/api/policies/search?q=auto",
    "/api/dashboard/stats",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_get_routes_require_auth(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_get_routes_reject_garbage_token(client, path):
    response = client.get(path, headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


def test_submit_claim_requires_auth(client):
    assert client.post("/api/claims", json=VALID_CLAIM).status_code == 401


def test_public_routes_open_unauthenticated(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/").status_code == 200


def test_expired_access_token_rejected(client, make_authenticated_user):
    _, _, email = make_authenticated_user(client, role="adjuster")
    login_body = client.post(
        "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
    ).json()
    now = datetime.now(timezone.utc)
    expired = pyjwt.encode(
        {
            "sub": login_body["user"]["id"],
            "email": email,
            "role": "adjuster",
            "type": "access",
            "iat": now - timedelta(hours=1),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/api/claims", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_wrong_token_type_rejected(client):
    """A refresh-token-shaped JWT must not pass as an access token."""
    now = datetime.now(timezone.utc)
    forged = pyjwt.encode(
        {"sub": "usr_x", "type": "refresh", "exp": now + timedelta(minutes=10)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/api/claims", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# ============ Role enforcement: 403 cross-role ============


def test_customer_forbidden_on_adjuster_routes(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="customer")
    for path in ("/api/claims", "/api/policies", "/api/dashboard/stats"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, path


@pytest.mark.asyncio  # strict mode: CI invokes pytest from the repo root, outside backend/pyproject's auto mode
async def test_require_customer_dependency_rejects_adjuster(patched_mongo):
    adjuster = UserRecord(
        id="usr_a", email="a@t.example", password_hash="x", role="adjuster"
    )
    with pytest.raises(Exception) as exc_info:
        await require_customer(adjuster)
    assert getattr(exc_info.value, "status_code", None) == 403


# ============ Refresh rotation and reuse detection ============


def _refresh(client, csrf_token=None, cookies=None):
    headers = {"X-CSRF-Token": csrf_token} if csrf_token is not None else {}
    return client.post("/api/auth/refresh", headers=headers, cookies=cookies)


def test_refresh_rotates_tokens_and_cookie(client, make_authenticated_user):
    _, csrf_token, _ = make_authenticated_user(client, role="adjuster")
    old_refresh = client.cookies[REFRESH_COOKIE]

    response = _refresh(client, csrf_token)
    assert response.status_code == 200, response.text
    assert response.json()["accessToken"]
    assert client.cookies[REFRESH_COOKIE] != old_refresh  # rotated
    assert client.cookies[CSRF_COOKIE] != csrf_token  # csrf rotates too


def test_refresh_without_csrf_header_rejected(client, make_authenticated_user):
    make_authenticated_user(client, role="adjuster")
    assert _refresh(client, None).status_code == 403


def test_refresh_with_wrong_csrf_header_rejected(client, make_authenticated_user):
    make_authenticated_user(client, role="adjuster")
    assert _refresh(client, "forged-csrf-token").status_code == 403


def test_refresh_without_cookie_rejected(client):
    assert _refresh(client, "any").status_code == 401


def test_refresh_with_unknown_token_rejected(client):
    response = client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": "any"},
        cookies={REFRESH_COOKIE: "forged-token-value", CSRF_COOKIE: "any"},
    )
    assert response.status_code == 401


def test_refresh_reuse_detection_revokes_family(client, make_authenticated_user, patched_mongo):
    _, csrf_token, _ = make_authenticated_user(client, role="adjuster")
    stolen_refresh = client.cookies[REFRESH_COOKIE]
    stolen_csrf = csrf_token

    # Legitimate client refreshes: rotation revokes the stolen token.
    first = _refresh(client, csrf_token)
    assert first.status_code == 200
    second_token = client.cookies[REFRESH_COOKIE]
    second_csrf = client.cookies[CSRF_COOKIE]

    # Attacker replays the stolen (now revoked) token -> family revoked.
    replay = client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": stolen_csrf},
        cookies={REFRESH_COOKIE: stolen_refresh, CSRF_COOKIE: stolen_csrf},
    )
    assert replay.status_code == 401
    assert "reuse" in replay.json()["detail"].lower()

    # Even the legitimate successor token is dead — re-auth required.
    response = client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": second_csrf},
        cookies={REFRESH_COOKIE: second_token, CSRF_COOKIE: second_csrf},
    )
    assert response.status_code == 401
    assert _run(patched_mongo.refresh_tokens.count_documents({})) == 2  # login + one rotation
    assert _run(patched_mongo.refresh_tokens.count_documents({"revoked": False})) == 0


def test_refresh_expired_token_rejected(client, make_authenticated_user, patched_mongo):
    make_authenticated_user(client, role="adjuster")
    # Age every stored session past its expiry (aware datetime — the store
    # always persists tz-aware values; a naive one would be data corruption).
    _run(
        patched_mongo.refresh_tokens.update_many(
            {},
            {
                "$set": {
                    "expires_at": (
                        datetime.now(timezone.utc) - timedelta(days=1)
                    ).isoformat()
                }
            },
        )
    )
    response = client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_logout_revokes_family_and_clears_cookies(client, make_authenticated_user):
    make_authenticated_user(client, role="adjuster")
    csrf_token = client.cookies[CSRF_COOKIE]
    refresh_token = client.cookies[REFRESH_COOKIE]

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert response.status_code == 204

    # Revoked family: replaying the old cookie is dead — re-auth required.
    response = client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": csrf_token},
        cookies={REFRESH_COOKIE: refresh_token, CSRF_COOKIE: csrf_token},
    )
    assert response.status_code == 401


# ============ CSRF defense (Origin/Referer + token binding) ============


def test_unsafe_request_with_foreign_origin_rejected(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = client.post(
        "/api/claims", json=VALID_CLAIM, headers={**headers, "Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert "Cross-site" in response.json()["detail"]


def test_unsafe_request_with_allowed_origin_accepted(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = client.post(
        "/api/claims",
        json=VALID_CLAIM,
        headers={**headers, "Origin": ALLOWED_ORIGINS[0]},
    )
    assert response.status_code == 200


def test_unsafe_request_with_foreign_referer_rejected(client, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = client.post(
        "/api/claims",
        json=VALID_CLAIM,
        headers={**headers, "Referer": "https://evil.example/attack"},
    )
    assert response.status_code == 403


def test_login_with_foreign_origin_rejected(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "x@test.example", "password": TEST_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_non_browser_request_without_origin_still_needs_auth(client):
    """curl-style clients send no Origin: CSRF is moot, but auth still applies."""
    assert client.post("/api/claims", json=VALID_CLAIM).status_code == 401


# ============ Rate limiting ============


@pytest.fixture
def limiter_on():
    from app.rate_limit import limiter

    limiter.enabled = True
    limiter._storage.reset()  # private attr: the only way to isolate counters between tests
    yield limiter
    limiter.enabled = False
    limiter._storage.reset()


def test_login_rate_limited_after_five_per_ip(client, limiter_on):
    payload = {"email": "ratelimit@test.example", "password": TEST_PASSWORD}
    statuses = [client.post("/api/auth/login", json=payload).status_code for _ in range(6)]
    assert statuses[:5] == [401] * 5  # failed logins still consume budget
    assert statuses[5] == 429
    assert client.post("/api/auth/login", json=payload).status_code == 429


def test_fnol_rate_limited_after_ten_per_ip(client, make_authenticated_user, limiter_on):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    statuses = [
        client.post("/api/claims", json=VALID_CLAIM, headers=headers).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


# ============ Demo-user seeding ============


def test_demo_users_seeded_idempotently(patched_mongo, monkeypatch):
    import asyncio

    import database

    monkeypatch.setattr(settings, "demo_adjuster_email", "demo.adjuster@claimos.dev")
    monkeypatch.setattr(settings, "demo_adjuster_password", "DemoAdjuster#123")
    monkeypatch.setattr(settings, "demo_customer_email", "demo.customer@claimos.dev")
    monkeypatch.setattr(settings, "demo_customer_password", "DemoCustomer#123")

    asyncio.run(database.seed_demo_users())
    asyncio.run(database.seed_demo_users())  # second pass must not duplicate

    assert _run(patched_mongo.users.count_documents({})) == 2
    adjuster = _run(patched_mongo.users.find_one({"email": "demo.adjuster@claimos.dev"}))
    assert adjuster["role"] == "adjuster"
    assert adjuster["password_hash"].startswith("$2b$")


def test_demo_user_can_login_after_startup_seeding(client, monkeypatch):
    import asyncio

    import database

    monkeypatch.setattr(settings, "demo_adjuster_email", "login.demo@claimos.dev")
    monkeypatch.setattr(settings, "demo_adjuster_password", "DemoLogin#123")
    # Same call the startup hook makes; demo settings arrive after the test
    # client's lifespan already ran.
    asyncio.run(database.seed_demo_users())
    response = client.post(
        "/api/auth/login",
        json={"email": "login.demo@claimos.dev", "password": "DemoLogin#123"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "adjuster"


def test_empty_demo_password_skips_seeding(patched_mongo, monkeypatch):
    import asyncio

    import database

    monkeypatch.setattr(settings, "demo_adjuster_email", "half@claimos.dev")
    monkeypatch.setattr(settings, "demo_adjuster_password", "")
    asyncio.run(database.seed_demo_users())
    assert _run(patched_mongo.users.count_documents({})) == 0
