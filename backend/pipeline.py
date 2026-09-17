"""Durable claim pipeline: the claim_runs queue, checkpointed execution, resume.

Splits execution out of the API process (audit finding 4 — single-worker
architecture). The API only mints a claim ID and enqueues a run document; a
worker process claims it with an atomic find_one_and_update (queued -> running),
so any number of workers can poll concurrently and each claim still runs exactly
once per attempt. Every agent stage is checkpointed into the run document the
moment it completes; a re-run creates attempt N+1 pre-seeded with the previous
attempt's completed checkpoints and resumes instead of re-executing.

Docker/CI impact: none — the worker is the same backend image with a different
command (`python worker.py`; the compose worker service owns the command).
"""

import logging
import time
from datetime import datetime, timezone

from pymongo import ReturnDocument

import database
from agents import PIPELINE_STAGES
from app.config import settings
from app.events import emit_event
from app.status_portal import access_code_hash
from app.stp import assess_claim_severity, evaluate_stp_gate
from app.usage import UsageLogger

logger = logging.getLogger("claimos.pipeline")

# Run lifecycle: queued -> running -> auto_approved | escalated | failed | halted.
# A run that reached the Decision agent always ends auto_approved or escalated
# (the STP gate decides); it never sits in a vague "completed" state.
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_AUTO_APPROVED = "auto_approved"
RUN_ESCALATED = "escalated"
RUN_FAILED = "failed"
RUN_HALTED = "halted"

TERMINAL_RUN_STATUSES = frozenset(
    {RUN_AUTO_APPROVED, RUN_ESCALATED, RUN_FAILED, RUN_HALTED}
)

# Claim-row status per terminal run status (halts keep the pre-decision
# "pending" status the original pipeline used).
_CLAIM_STATUS_BY_RUN = {
    RUN_AUTO_APPROVED: "auto_approved",
    RUN_ESCALATED: "escalated",
    RUN_FAILED: "failed",
    RUN_HALTED: "pending",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def enqueue_claim_run(
    claim_id: str, submission: dict, access_code: str | None = None
) -> dict:
    """Insert the first (or next) run for a claim; returns the queued document.

    Idempotent while a run is still in flight: if the latest attempt is queued
    or running it is returned unchanged, so a re-submission can never spawn a
    second execution of the same claim. A new attempt (N+1, seeded with the
    latest run's completed checkpoints) is created only when the previous run
    reached a terminal state. Recovery for runs orphaned in `running` by a
    hard crash is lease/reaper territory — the graceful-shutdown path requeues
    them explicitly.

    `access_code` is the public status portal credential minted at submission;
    it rides on the run document so `_save_claim` carries it onto the claim
    row when the worker persists the run's result.
    """
    latest = await database.claim_runs_col.find_one(
        {"claim_id": claim_id}, sort=[("attempt", -1)]
    )
    if latest and latest.get("status") in (RUN_QUEUED, RUN_RUNNING):
        return latest
    checkpoints = _completed_checkpoints(latest) if latest else {}
    attempt = int(latest["attempt"]) + 1 if latest else 1
    now = _now()
    doc = {
        "claim_id": claim_id,
        "attempt": attempt,
        "status": RUN_QUEUED,
        "input": submission,
        "access_code": access_code,
        "stages": checkpoints,
        "failure_reason": None,
        "usage": None,
        "worker_id": None,
        "claimed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await database.claim_runs_col.insert_one(doc)
    logger.info("run_enqueued claim_id=%s attempt=%s", claim_id, attempt)
    return doc


def _completed_checkpoints(run_doc: dict) -> dict:
    """Stage-name -> checkpoint for every stage a previous attempt finished."""
    stages = run_doc.get("stages") or {}
    return {name: cp for name, cp in stages.items() if cp.get("status") == "done"}


async def claim_next_run(worker_id: str) -> dict | None:
    """Atomically claim one queued run for this worker (None when queue empty).

    The status flip inside find_one_and_update is the entire claim semantic:
    two pollers can race the same query, but only one receives the document.
    """
    return await database.claim_runs_col.find_one_and_update(
        {"status": RUN_QUEUED},
        {
            "$set": {
                "status": RUN_RUNNING,
                "worker_id": worker_id,
                "claimed_at": _now(),
                "updated_at": _now(),
            }
        },
        sort=[("created_at", 1), ("attempt", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def requeue_run(claim_id: str, attempt: int) -> None:
    """Return an interrupted run to the queue so a worker can resume it.

    Guarded on status: a run that already finished is never resurrected.
    """
    await database.claim_runs_col.update_one(
        {"claim_id": claim_id, "attempt": attempt, "status": RUN_RUNNING},
        {
            "$set": {
                "status": RUN_QUEUED,
                "worker_id": None,
                "claimed_at": None,
                "updated_at": _now(),
            }
        },
    )


class PipelineRunner:
    """Executes one claimed run: durable events, per-stage checkpoints, resume."""

    def __init__(self, run_doc: dict, *, should_stop=None):
        self.run_doc = run_doc
        self.claim_id = run_doc["claim_id"]
        # Cooperative-cancel hook (graceful shutdown): checked between stages,
        # after the previous stage's checkpoint is safely persisted.
        self.should_stop = should_stop or (lambda: False)
        self.state = self._rebuild_state()

    def _rebuild_state(self) -> dict:
        """Rebuild orchestrator state from the run input + checkpointed outputs."""
        state = {
            "claimId": self.claim_id,
            "submittedAt": self.run_doc.get("created_at") or _now(),
            "input": dict(self.run_doc.get("input") or {}),
            "intake": {},
            "policy": {},
            "documents": {},
            "eligibility": {},
            "decision": {},
            "agentLogs": [],
        }
        for stage in PIPELINE_STAGES:
            cp = (self.run_doc.get("stages") or {}).get(stage["name"])
            if cp and cp.get("status") == "done":
                state[stage["stateKey"]] = cp.get("output") or {}
                # Rebuild the trace entry so a resumed run's claim document
                # still shows the full stage history, not just the new work.
                state["agentLogs"].append(
                    {
                        "agent": stage["name"],
                        "status": "done",
                        "startTime": cp.get("started_at"),
                        "endTime": cp.get("finished_at"),
                        "toolsCalled": cp.get("tools_called") or [],
                        "durationMs": cp.get("duration_ms", 0),
                    }
                )
        return state

    async def run(self) -> str:
        """Execute the remaining stages; returns the run's terminal status."""
        checkpoints = self.run_doc.setdefault("stages", {})
        final_status: str | None = None

        for stage in PIPELINE_STAGES:
            if checkpoints.get(stage["name"], {}).get("status") == "done":
                continue
            if self.should_stop():
                # Graceful shutdown: everything up to here is checkpointed;
                # hand the remainder back to the queue instead of racing exit.
                await requeue_run(self.claim_id, self.run_doc["attempt"])
                logger.info(
                    "run_requeued_for_shutdown claim_id=%s attempt=%s",
                    self.claim_id,
                    self.run_doc["attempt"],
                )
                return RUN_QUEUED

            outcome = await self._execute_stage(stage, checkpoints)
            if outcome == "halted":
                final_status = RUN_HALTED
                break
            if outcome == "failed":
                final_status = RUN_FAILED
                break

        if final_status is None:
            # All stages done and a Decision exists: the STP gate decides
            # auto-finalization vs escalation (spec AC-5).
            final_status = await self._apply_stp_gate()

        if final_status != RUN_FAILED:  # the failing stage already saved its row
            await self._save_claim(status=_CLAIM_STATUS_BY_RUN[final_status])

        await self._emit(
            {
                "event": "run_finalized",
                "status": final_status,
                "attempt": self.run_doc["attempt"],
                "failure_reason": self.run_doc.get("failure_reason"),
            }
        )
        await self._finalize_run(final_status)
        return final_status

    async def _apply_stp_gate(self) -> str:
        """Straight-through gate after the Decision agent (spec AC-5).

        Every leg must hold — confidence at or above the configured threshold,
        low derived severity, clean eligibility — for the claim to auto-finalize
        as auto_approved. Any failing leg escalates, and the escalation reason
        enumerates exactly which legs failed.
        """
        decision = self.state.get("decision") or {}
        normalized = self.state.get("intake", {}).get("normalizedData", {})
        severity = assess_claim_severity(
            claimed_amount=float(normalized.get("claimedAmount", 0) or 0),
            incident_type=str(normalized.get("incidentType", "") or ""),
            low_amount_threshold=settings.stp_low_severity_amount,
            low_types={
                part.strip().lower()
                for part in settings.stp_low_severity_types.split(",")
                if part.strip()
            },
        )
        gate = evaluate_stp_gate(
            confidence=float(decision.get("confidence", 0.0)),
            severity=severity,
            eligibility=self.state.get("eligibility") or {},
            threshold=settings.stp_confidence_threshold,
        )
        self.run_doc["stp"] = {
            "decision": "auto_approved" if gate.auto_finalize else "escalated",
            "confidence": decision.get("confidence", 0.0),
            "severity": severity,
            "threshold": settings.stp_confidence_threshold,
        }

        if gate.auto_finalize:
            await self._emit(
                {
                    "event": "stp_finalized",
                    "decision": "auto_approved",
                    "confidence": decision.get("confidence", 0.0),
                    "severity": severity,
                }
            )
            return RUN_AUTO_APPROVED

        reason = gate.escalation_reason()
        self.run_doc["escalation_reason"] = reason
        await self._emit(
            {
                "event": "stp_escalated",
                "reason": reason,
                "confidence": decision.get("confidence", 0.0),
                "severity": severity,
            }
        )
        return RUN_ESCALATED

    async def _execute_stage(self, stage: dict, checkpoints: dict) -> str:
        """Run one agent stage; returns "ok" | "halted" | "failed"."""
        started = time.perf_counter()
        log_entry = {
            "agent": stage["name"],
            "status": "running",
            "startTime": _now(),
            "endTime": None,
            "toolsCalled": [],
            "durationMs": 0,
        }
        self.state["agentLogs"].append(log_entry)
        await self._emit(
            {
                "event": "agent_start",
                "agent": stage["name"],
                "label": stage["label"],
                "description": stage["desc"],
                "timestamp": log_entry["startTime"],
            }
        )

        try:
            self.state = await stage["fn"](self.state)
        except Exception:  # noqa: BLE001 — the failure is recorded, then the run halts
            duration = int((time.perf_counter() - started) * 1000)
            log_entry["status"] = "error"
            log_entry["endTime"] = _now()
            log_entry["durationMs"] = duration
            logger.exception(
                "agent_failed agent=%s claim_id=%s attempt=%s",
                stage["name"], self.claim_id, self.run_doc["attempt"],
            )
            # Sanitized: raw exception text never reaches the client stream.
            await self._emit(
                {
                    "event": "agent_error",
                    "agent": stage["name"],
                    "label": stage["label"],
                    "error": "Internal pipeline error",
                    "duration": duration,
                }
            )
            failure = f"Agent {stage['name']} failed"
            await self._emit({"event": "claim_failed", "reason": failure})
            self.run_doc["failure_reason"] = failure
            await self._save_claim(status=RUN_FAILED, failure_reason=failure)
            return "failed"

        duration = int((time.perf_counter() - started) * 1000)
        log_entry["status"] = "done"
        log_entry["endTime"] = _now()
        log_entry["durationMs"] = duration
        output = self.state[stage["stateKey"]]
        tools = next(
            (
                log.get("toolsCalled") or []
                for log in self.state["agentLogs"]
                if log["agent"] == stage["name"]
            ),
            [],
        )

        # Checkpoint BEFORE announcing completion: the event stream may claim a
        # stage is done only once the checkpoint that proves it exists.
        checkpoints[stage["name"]] = {
            "status": "done",
            "output": output,
            "duration_ms": duration,
            "started_at": log_entry["startTime"],
            "finished_at": log_entry["endTime"],
            "tools_called": tools,
        }
        await self._persist_checkpoint(stage["name"], checkpoints[stage["name"]])
        await self._emit(
            {
                "event": "agent_complete",
                "agent": stage["name"],
                "label": stage["label"],
                "duration": duration,
                "output": output,
                "toolsCalled": tools,
            }
        )

        # Halt conditions — same semantics the in-process orchestrator had.
        if stage["name"] == "INTAKE_AGENT" and not self.state["intake"].get("valid", False):
            await self._emit(
                {
                    "event": "pipeline_halted",
                    "reason": "Invalid submission",
                    "data": self.state["intake"],
                }
            )
            return "halted"
        if stage["name"] == "POLICY_AGENT" and not self.state["policy"].get("found", False):
            await self._emit(
                {
                    "event": "pipeline_halted",
                    "reason": "Policy not found",
                    "data": self.state["policy"],
                }
            )
            return "halted"
        return "ok"

    async def _emit(self, event: dict) -> None:
        """Append to the durable event log; live SSE tails it from Mongo."""
        try:
            await emit_event(self.claim_id, event)
        except Exception:  # noqa: BLE001 — events are derived data; checkpoints are truth
            logger.exception("event_persist_failed claim_id=%s", self.claim_id)

    async def _persist_checkpoint(self, stage_name: str, checkpoint: dict) -> None:
        """Atomically record one stage's output on the run document."""
        await database.claim_runs_col.update_one(
            {"claim_id": self.claim_id, "attempt": self.run_doc["attempt"]},
            {"$set": {f"stages.{stage_name}": checkpoint, "updated_at": _now()}},
        )

    async def _finalize_run(self, status: str) -> None:
        """Terminal run update: status plus the per-claim LLM usage rollup."""
        usage = None
        try:
            usage = await UsageLogger().rollup_for_claim(self.claim_id)
        except Exception:  # noqa: BLE001 — telemetry must never fail a finished run
            logger.exception("usage_rollup_failed claim_id=%s", self.claim_id)

        update: dict = {
            "$set": {
                "status": status,
                "updated_at": _now(),
                "finished_at": _now(),
                "usage": usage,
            }
        }
        if self.run_doc.get("failure_reason"):
            update["$set"]["failure_reason"] = self.run_doc["failure_reason"]
        if self.run_doc.get("escalation_reason"):
            update["$set"]["escalation_reason"] = self.run_doc["escalation_reason"]
        if self.run_doc.get("stp"):
            update["$set"]["stp"] = self.run_doc["stp"]
        await database.claim_runs_col.update_one(
            {"claim_id": self.claim_id, "attempt": self.run_doc["attempt"]}, update
        )
        # The claim record carries the same rollup for API consumers.
        if usage is not None:
            await database.claims_col.update_one(
                {"id": self.claim_id}, {"$set": {"usage": usage}}
            )

    async def _save_claim(self, *, status: str, failure_reason: str | None = None) -> None:
        """Persist the claim document; replaces any row from a prior attempt."""
        claim_doc = {
            "id": self.claim_id,
            "policy_number": self.state["input"].get("policyNumber", ""),
            "claim_date": _now(),
            "incident_date": self.state["intake"].get("normalizedData", {}).get("incidentDate", ""),
            "incident_type": self.state["intake"].get("normalizedData", {}).get(
                "incidentType", self.state["input"].get("incidentType", "")
            ),
            "claimed_amount": float(
                self.state["intake"].get("normalizedData", {}).get(
                    "claimedAmount", self.state["input"].get("claimedAmount", 0)
                )
            ),
            "status": status,
            "risk_score": self.state.get("eligibility", {}).get("riskScore", 0),
            "decision_reason": self.state.get("decision", {}).get("summary", ""),
            "agent_trace": {
                "intake": self.state.get("intake", {}),
                "policy": {k: v for k, v in self.state.get("policy", {}).items() if k != "policyData"},
                "documents": self.state.get("documents", {}),
                "eligibility": self.state.get("eligibility", {}),
                "decision": self.state.get("decision", {}),
            },
            "agent_logs": self.state.get("agentLogs", []),
            "holder_name": self.state.get("policy", {}).get("policyData", {}).get("holder_name", ""),
            "is_historical": False,
            "created_at": _now(),
            # Contact email for milestone notifications; the access code feeds
            # the public status portal (plaintext for the adjuster case view,
            # hash for the portal lookup).
            "contact_email": self.state["input"].get("contactEmail", ""),
            "access_code": self.run_doc.get("access_code"),
            "access_code_hash": (
                access_code_hash(self.run_doc["access_code"])
                if self.run_doc.get("access_code")
                else None
            ),
        }
        if failure_reason:
            claim_doc["failure_reason"] = failure_reason
        if self.run_doc.get("escalation_reason"):
            claim_doc["escalation_reason"] = self.run_doc["escalation_reason"]
            claim_doc["stp"] = self.run_doc.get("stp")

        doc_text = self.state["input"].get("documentText", "")
        if doc_text:
            already = await database.claim_documents_col.count_documents(
                {"claim_id": self.claim_id, "document_type": "evidence_text"}
            )
            if already == 0:  # re-attempts must not duplicate the evidence row
                await database.claim_documents_col.insert_one(
                    {
                        "claim_id": self.claim_id,
                        "document_type": "evidence_text",
                        "content_text": doc_text,
                        "uploaded_at": _now(),
                    }
                )

        # Upsert: a resumed attempt overwrites the row a prior attempt saved
        # instead of colliding with the unique {id} index.
        await database.claims_col.replace_one({"id": self.claim_id}, claim_doc, upsert=True)
        logger.info(
            "claim_saved claim_id=%s attempt=%s status=%s",
            self.claim_id, self.run_doc["attempt"], status,
        )
