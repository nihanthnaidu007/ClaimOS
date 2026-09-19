"""Hot-path index coverage: seed-time index creation, redundant legacy index
drops, TTL backed by a real BSON Date, and the demo-seeding production gate.

Mirrors test_durable_state's harness — mongomock via patched_mongo,
module-level asyncio markers (CI runs pytest from the repo root). Collections
are reached through the fixture (or `database.*` at call time) because the
fixture swaps the module-level handles for mock bindings.
"""

from datetime import datetime

import pytest

pytestmark = pytest.mark.asyncio

from app.config import settings  # noqa: E402
from app.usage import UsageLogger  # noqa: E402
from database import (  # noqa: E402
    LLM_USAGE_COLLECTION,
    seed_database,
    seed_demo_users,
)


async def _index_specs(collection):
    specs = await collection.list_indexes().to_list(length=None)
    return {index["name"]: index for index in specs}


async def test_hot_path_indexes_exist(patched_mongo):
    await seed_database()

    assert "email_1" in await _index_specs(patched_mongo.users)

    refresh = await _index_specs(patched_mongo.refresh_tokens)
    assert "token_hash_1" in refresh
    assert "family_id_1" in refresh

    notifications = await _index_specs(patched_mongo.notifications)
    assert "recipient_email_1" in notifications
    assert "id_1" in notifications

    usage = await _index_specs(patched_mongo[LLM_USAGE_COLLECTION])
    assert "claim_id_1" in usage
    assert "logged_at_1" in usage


async def test_llm_usage_ttl_expires_after_90_days(patched_mongo):
    await seed_database()
    specs = await _index_specs(patched_mongo[LLM_USAGE_COLLECTION])
    assert specs["logged_at_1"].get("expireAfterSeconds") == 90 * 24 * 60 * 60


async def test_llm_usage_logged_at_is_bson_date(patched_mongo):
    # TTL indexes expire on BSON Dates only — an ISO string makes the TTL a
    # silent no-op (the exact bug the ISO-string write used to ship).
    await UsageLogger().log(
        agent="fraud",
        model="test-model",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )
    doc = await patched_mongo[LLM_USAGE_COLLECTION].find_one({}, {"_id": 0})
    assert isinstance(doc["logged_at"], datetime)


async def test_redundant_audit_log_indexes_dropped(patched_mongo):
    # Simulate a pre-existing deployment that still carries the single-field
    # indexes the compound superseded; reseeding must drop them.
    audit_log = patched_mongo.audit_log
    await audit_log.create_index("claim_id")
    await audit_log.create_index("at")

    await seed_database()

    names = set(await _index_specs(audit_log))
    assert "claim_id_1_at_-1" in names
    assert "claim_id_1" not in names
    assert "at_1" not in names


async def test_demo_seeding_skipped_in_production(patched_mongo, monkeypatch):
    # Production provisions users server-side: even with demo credentials
    # present, no demo account may be created.
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "demo_adjuster_email", "demo-adjuster@claimos.dev")
    monkeypatch.setattr(settings, "demo_adjuster_password", "demo-password")

    await seed_demo_users()

    assert await patched_mongo.users.count_documents({}) == 0
