"""ClaimOS pipeline worker — claim_runs queue consumer.

Same backend image as the API, different command: the api service runs
`uvicorn server:app ...`, this worker runs `python worker.py` (the compose
worker service owns the command; CI builds one image for both).

The loop atomically claims queued claim_runs documents — one find_one_and_update
per claim, safe with any number of worker processes — executes the checkpointed
pipeline, and on SIGTERM/SIGINT stops claiming, lets the in-flight stage finish
and checkpoint, requeues the unfinished remainder, and exits. No claim state
lives in this process: a crash loses at most the stage in flight, and a re-run
resumes from the last completed checkpoint.
"""

import asyncio
import logging
import os
import signal
import uuid

from app.config import settings
from pipeline import PipelineRunner, claim_next_run

logger = logging.getLogger("claimos.worker")


class PipelineWorker:
    """Polls the claim_runs queue and executes one run at a time."""

    def __init__(self, worker_id: str | None = None, poll_interval: float | None = None):
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else settings.worker_poll_interval_seconds
        )
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Signal graceful shutdown; safe to call from a signal handler."""
        logger.info("worker_stop_requested worker_id=%s", self.worker_id)
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    async def run_once(self) -> str | None:
        """Claim and execute one run; returns its terminal status (None = idle)."""
        run_doc = await claim_next_run(self.worker_id)
        if run_doc is None:
            return None
        runner = PipelineRunner(run_doc, should_stop=self._stop.is_set)
        status = await runner.run()
        logger.info(
            "run_finished claim_id=%s attempt=%s status=%s",
            run_doc["claim_id"],
            run_doc["attempt"],
            status,
        )
        return status

    async def run_forever(self) -> None:
        logger.info("worker_started worker_id=%s pid=%s", self.worker_id, os.getpid())
        while not self._stop.is_set():
            status = await self.run_once()
            if status is None:
                # Nap in wait_for so a stop signal ends the idle wait instantly.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
        logger.info("worker_stopped worker_id=%s", self.worker_id)


async def main() -> None:
    from database import seed_database  # after logging config; creates claim_runs indexes

    worker = PipelineWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)

    await seed_database()
    await worker.run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
