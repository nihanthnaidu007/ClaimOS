"""Append-only claim event store.

Every pipeline event is persisted to `claim_events` with a per-claim monotonic
sequence number. This is the durability foundation for SSE replay (Last-Event-ID
resume); the SSE endpoint rewrite consumes it in the pipeline PR. The live
in-memory queue delivery stays in place meanwhile — writes go to both.
"""

from datetime import datetime, timezone

import database
from app.counters import next_sequence


async def emit_event(claim_id: str, event: dict) -> int:
    """Persist one pipeline event; returns its sequence number.

    `event` is the same payload handed to the live SSE queue (must include an
    "event" type key).
    """
    seq = await next_sequence(f"claim_events:{claim_id}")
    doc = {
        "claim_id": claim_id,
        "seq": seq,
        "event": event.get("event", "unknown"),
        "data": {k: v for k, v in event.items() if k != "event"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await database.events_col.insert_one(doc)
    return seq


async def get_claim_events(claim_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    """Replay helper: ordered events for a claim (used by SSE resume and tests)."""
    cursor = (
        database.events_col.find({"claim_id": claim_id, "seq": {"$gt": after_seq}}, {"_id": 0})
        .sort("seq", 1)
        .limit(limit)
    )
    return await cursor.to_list(limit)
