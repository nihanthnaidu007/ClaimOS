"""Per-call LLM usage log — one Mongo document per adapter call.

Powers per-claim cost attribution (research art_hxr3LsdW, "Observability"): model,
input/output tokens, latency, and the claim_id when the call runs inside the pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

USAGE_COLLECTION = "llm_usage"


class UsageLogger:
    """Writes one usage record per LLM call.

    Failures are logged, never raised: telemetry must not fail a claim run.
    """

    def __init__(self, collection=None):
        self._collection = collection

    @property
    def collection(self):
        if self._collection is None:
            # Deferred so importing this module never constructs the Mongo client.
            from database import db

            self._collection = db[USAGE_COLLECTION]
        return self._collection

    async def log(
        self,
        *,
        agent: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        claim_id: str | None = None,
        call_type: str = "complete_structured",
    ) -> None:
        record = {
            "agent": agent,
            "model": model,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "latency_ms": int(latency_ms),
            "claim_id": claim_id,
            "call_type": call_type,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            collection = self.collection
            await collection.insert_one(record)
        except Exception:
            logger.exception("Failed to write LLM usage record to Mongo")

    async def rollup_for_claim(self, claim_id: str) -> dict:
        """Aggregate one claim's usage records into a rollup summary.

        Returns zeros when no records exist so consumers always see a
        well-shaped rollup. Called at run finalization; telemetry failures
        are the caller's concern (see pipeline._finalize_run).
        """
        pipeline = [
            {"$match": {"claim_id": claim_id}},
            {
                "$group": {
                    "_id": None,
                    "total_calls": {"$sum": 1},
                    "input_tokens": {"$sum": "$input_tokens"},
                    "output_tokens": {"$sum": "$output_tokens"},
                    "latency_ms": {"$sum": "$latency_ms"},
                }
            },
        ]
        rows = await self.collection.aggregate(pipeline).to_list(1)
        if not rows:
            return {"total_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
        rollup = rows[0]
        rollup.pop("_id", None)
        return rollup
