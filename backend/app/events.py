"""Append-only claim event store.

Every pipeline event is persisted to `claim_events` with a per-claim monotonic
sequence number. The SSE endpoint consumes this log directly (replay + live
tail), so event delivery works across many worker processes and API replicas —
there is no in-process pub/sub anywhere in the path.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import structlog

import database
from app.counters import next_sequence
from app.notifications.fanout import dispatch_milestone, milestone_for_event

logger = structlog.get_logger("claimos.events")

# Events that end a claim's story: the SSE stream closes after one of these.
# run_finalized is the runner's always-last bookkeeping event, so a stream
# that reached pipeline_complete keeps reading until the runner's final
# status (auto-finalize, escalate, halt, or failure) is also replayed.
TERMINAL_EVENTS = frozenset(
    {"run_finalized", "pipeline_complete", "pipeline_halted", "claim_failed", "pipeline_error"}
)

# Floors for the live tail. Polling the indexed log is what keeps the tail
# portable (works identically on real Motor and mongomock in tests) and
# multi-replica safe; a claim emits ~13 events per run, each stage taking
# seconds, so sub-second delivery latency is well inside the product budget.
TAIL_POLL_INTERVAL_S = 0.5
TAIL_HEARTBEAT_S = 15.0
TAIL_BATCH_SIZE = 100


async def emit_event(claim_id: str, event: dict) -> int:
    """Persist one pipeline event; returns its sequence number.

    `event` is the pipeline payload (must include an "event" type key); the
    type is stored beside the data so replay can reconstruct the exact frame.
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

    # Milestone fan-out: derived customer notifications fire from the same
    # choke point every event passes through, and records are unique per
    # claim+milestone so replays cannot double-notify. A fan-out failure is
    # logged and dropped — the event write itself must never fail because of
    # derived data.
    event_type = doc["event"]
    if milestone_for_event(event_type, doc["data"]) is not None:
        try:
            await dispatch_milestone(claim_id, event_type, doc["data"])
        except Exception:
            logger.exception(
                "notification_fanout_failed", claim_id=claim_id, event=event_type
            )
    return seq


async def get_claim_events(claim_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    """Replay helper: ordered events for a claim (used by SSE resume and tests)."""
    cursor = (
        database.events_col.find({"claim_id": claim_id, "seq": {"$gt": after_seq}}, {"_id": 0})
        .sort("seq", 1)
        .limit(limit)
    )
    return await cursor.to_list(limit)


async def tail_claim_events(
    claim_id: str,
    *,
    after_seq: int = 0,
    poll_interval: float = TAIL_POLL_INTERVAL_S,
    heartbeat_every: float = TAIL_HEARTBEAT_S,
    terminal_events: frozenset[str] = TERMINAL_EVENTS,
) -> AsyncIterator[dict | None]:
    """Tail the claim's event log: replay `after_seq..`, then follow new writes.

    Yields stored event docs (each carrying its `seq`) in order, then None as a
    heartbeat tick once the log stays idle past `heartbeat_every`. Terminates
    right after a terminal event. Reading only the events collection — no
    in-process queue — is what lets this serve every subscriber of every API
    replica regardless of which worker process emitted the events.
    """
    cursor = after_seq
    last_event_at = time.monotonic()
    last_heartbeat_at = last_event_at
    while True:
        batch = await get_claim_events(claim_id, after_seq=cursor, limit=TAIL_BATCH_SIZE)
        for doc in batch:
            cursor = doc["seq"]
            yield doc
            if doc.get("event") in terminal_events:
                return
        if batch:
            last_event_at = time.monotonic()
        now = time.monotonic()
        idle = now - last_event_at >= heartbeat_every
        due_beat = now - last_heartbeat_at >= heartbeat_every
        if idle and due_beat:
            last_heartbeat_at = now
            yield None
            continue
        # A full batch means more is waiting: loop again without sleeping.
        if len(batch) < TAIL_BATCH_SIZE:
            await asyncio.sleep(poll_interval)
