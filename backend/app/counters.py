"""Atomic MongoDB counters.

Replaces the in-process claim counter (server.py:29) that breaks with more than
one worker: read-modify-write in Python memory collides across workers. All
sequences now live in a dedicated collection advanced with an atomic
find_one_and_update.
"""

from pymongo import ReturnDocument

import database


async def next_sequence(key: str) -> int:
    """Atomically increment and return the sequence for `key`."""
    doc = await database.counters_col.find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


async def next_claim_number(date_str: str) -> int:
    """Per-day claim sequence, so claim IDs stay dense per date."""
    return await next_sequence(f"claim_id:{date_str}")
