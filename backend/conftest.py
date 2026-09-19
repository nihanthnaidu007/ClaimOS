"""Pytest environment defaults.

Tests run without MongoDB or an Anthropic API key: the Motor client is built
lazily by Motor itself (no connection until a call is awaited), and every LLM
test injects a mocked client instead of constructing a real AsyncAnthropic.
"""

import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "claimos_test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production")

import pytest
from mongomock_motor import AsyncMongoMockClient


@pytest.fixture
def patched_mongo(monkeypatch):
    """Swap every Mongo binding for a mongomock instance.

    server.py and agents.py bind collection names at import time, so patching
    the `database` module alone is not enough — every static importer gets the
    same mock handles.
    """
    import agents
    import database
    import server

    client = AsyncMongoMockClient()
    db = client["claimos_test"]
    # Attribute names carry a _col suffix that the real collection names drop.
    for attr in (
        "policies_col",
        "claims_col",
        "claim_documents_col",
        "counters_col",
        "events_col",
        "claim_runs_col",
        "seed_state_col",
        "users_col",
        "refresh_tokens_col",
        "audit_log_col",
        "notifications_col",
        "document_requests_col",
        "workbench_views_col",    ):
        monkeypatch.setattr(database, attr, db[attr.removesuffix("_col")])
    monkeypatch.setattr(database, "db", db)
    monkeypatch.setattr(database, "client", client)
    monkeypatch.setattr(agents, "policies_col", db.policies)
    monkeypatch.setattr(agents, "claims_col", db.claims)
    monkeypatch.setattr(server, "policies_col", db.policies)
    monkeypatch.setattr(server, "claims_col", db.claims)
    monkeypatch.setattr(server, "claim_documents_col", db.claim_documents)
    monkeypatch.setattr(server, "audit_log_col", db.audit_log)
    monkeypatch.setattr(server, "db", db)
    return db


# ---- Auth test support ----

TEST_INVITE_CODE = "test-invite-code"
TEST_PASSWORD = "Sup3rSecret!"


@pytest.fixture(autouse=True)
def auth_test_env(monkeypatch):
    """Auth-suite defaults: an invite code, cheap bcrypt rounds, rate limiting off.

    Individual tests opt back into real limits by flipping `limiter.enabled`.
    """
    from app import security
    from app.config import settings
    from app.rate_limit import limiter

    monkeypatch.setattr(settings, "invite_code", TEST_INVITE_CODE)
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    limiter.enabled = False
    yield
    limiter.enabled = False


def _promote_role_in_store(db, email: str, role: str) -> None:
    """Grant a role directly in the user store (server-side provisioning).

    Public registration no longer grants roles — it always creates a customer.
    Tests that need another role register through the public route and then
    flip the stored role, the same shape as the seeded demo accounts. The
    write runs on a worker thread: async tests already own the event loop,
    and the mongomock collections are loop-agnostic (the suite also drives
    them through per-call asyncio.run).
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    async def _promote():
        await db.users.update_one({"email": email}, {"$set": {"role": role}})

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, _promote()).result()


@pytest.fixture
def make_authenticated_user(patched_mongo):
    """Register + login a user; returns (auth_headers, csrf_token, email).

    Uses a fresh email per call so tests never collide on the unique index.
    The TestClient keeps the refresh/CSRF cookies in its jar automatically.
    `role` is granted server-side (see _promote_role_in_store); only
    customer accounts come straight from the public registration route.
    """
    from uuid import uuid4

    def _make(client, role="adjuster"):
        email = f"{role}-{uuid4().hex[:8]}@test.example"
        response = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "inviteCode": TEST_INVITE_CODE,
            },
        )
        assert response.status_code == 201, response.text
        if role != "customer":
            _promote_role_in_store(patched_mongo, email, role)
        response = client.post(
            "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['accessToken']}"}
        return headers, response.cookies.get("claimos_csrf", ""), email

    return _make
