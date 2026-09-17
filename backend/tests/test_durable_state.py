"""Tests for atomic counters, the append-only event store, and idempotent seeding.

Module-level asyncio markers: CI invokes pytest from the repo root, outside
backend/pyproject.toml's auto mode, so strict-mode markers are required here.
"""

import pytest

pytestmark = pytest.mark.asyncio

from app.counters import next_claim_number, next_sequence  # noqa: E402
from app.events import emit_event, get_claim_events  # noqa: E402
from database import SEED_POLICIES, seed_database  # noqa: E402


async def test_counter_increments_atomically(patched_mongo):
    assert await next_sequence("x") == 1
    assert await next_sequence("x") == 2
    # Independent keys start at 1.
    assert await next_sequence("other") == 1


async def test_claim_numbers_are_per_day(patched_mongo):
    assert await next_claim_number("20260917") == 1
    assert await next_claim_number("20260917") == 2
    assert await next_claim_number("20260918") == 1


async def test_events_append_monotonic(patched_mongo):
    assert await emit_event("c1", {"event": "agent_start", "agent": "A"}) == 1
    assert await emit_event("c1", {"event": "agent_complete", "agent": "A"}) == 2
    # Sequence numbers are per claim.
    assert await emit_event("c2", {"event": "agent_start", "agent": "B"}) == 1


async def test_events_replay_in_order(patched_mongo):
    await emit_event("c1", {"event": "a"})
    await emit_event("c1", {"event": "b"})
    await emit_event("c1", {"event": "c"})

    events = await get_claim_events("c1")
    assert [e["event"] for e in events] == ["a", "b", "c"]
    assert [e["seq"] for e in events] == [1, 2, 3]

    # after_seq skips everything at or below it (SSE Last-Event-ID resume).
    tail = await get_claim_events("c1", after_seq=1)
    assert [e["event"] for e in tail] == ["b", "c"]


async def test_seeding_is_idempotent(patched_mongo):
    await seed_database()
    policies = await patched_mongo.policies.count_documents({})
    claims = await patched_mongo.claims.count_documents({})
    assert policies == len(SEED_POLICIES)
    assert claims == 15

    # Second run: marker present, nothing duplicated, no error.
    await seed_database()
    assert await patched_mongo.policies.count_documents({}) == policies
    assert await patched_mongo.claims.count_documents({}) == claims


async def test_seeding_tolerates_partial_concurrent_seed(patched_mongo):
    """A seed that lost its marker but left rows behind converges without failing."""
    from database import SEED_MARKER_ID

    await patched_mongo.seed_state.insert_one({"_id": SEED_MARKER_ID})
    await patched_mongo.policies.insert_one(dict(SEED_POLICIES[0]))
    await patched_mongo.seed_state.delete_one({"_id": SEED_MARKER_ID})

    await seed_database()  # duplicate policy keys are tolerated

    assert await patched_mongo.policies.count_documents({}) == len(SEED_POLICIES)
    assert await patched_mongo.claims.count_documents({}) == 15


async def test_seeding_creates_unique_indexes(patched_mongo):
    from pymongo.errors import DuplicateKeyError

    await seed_database()
    with pytest.raises(DuplicateKeyError):
        await patched_mongo.policies.insert_one({"policy_number": SEED_POLICIES[0]["policy_number"]})
