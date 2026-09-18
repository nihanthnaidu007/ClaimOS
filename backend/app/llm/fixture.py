"""Deterministic fixture LLM adapter — full pipeline, zero API spend.

Selected when LLM_PROVIDER=fixture (settings.llm_provider). Subclasses the
Anthropic adapter so agents and the pipeline see an identical interface; the
only difference is where the structured outputs come from. Every output is
derived deterministically from the call's own user message (the prompts embed
the raw submission, the policy lookup result, and the prior agent outputs, so
a small parser can reflect real input values back), and all arithmetic still
runs in the orchestrator/rating code — the fixture never computes money math.

Fault injection replays the live-LLM failure classes the FNOL E2E wave
observed (schema misses that burn LLM round-trips, refusals, timeouts) so the
recovery paths — the adapter's bounded schema retry, the agent-level
refusal-retry with the template-letter fallback, and the 10s polling
fallback — are covered deterministically, with no metered API key.

FIXTURE_FAULT grammar (comma-separated, applied in the process that runs the
pipeline — the worker):
    timeout:<agent>        raise LLMTimeout on every call for <agent>
    refusal:<agent>        raise LLMRefusal on every call (the agent-level
                           retry re-runs once, then the second refusal
                           propagates — Decision falls back to the template
                           letter, other agents fail the run closed)
    refusal_once:<agent>   refuse the first call only — the retry rescues it
    schema:<agent>         raise LLMSchemaValidationError on every call —
                           retried as a transient fault up to the cap, then
                           the typed terminal error
    schema_once:<agent>    one schema miss, then valid output — the bounded
                           schema retry rescues it (the live schema-miss wart)

<agent> is the short agent key: intake|policy|document|fraud|eligibility|decision.
FIXTURE_DELAY_MS adds a fixed per-call pause (pacing for SSE reconnect tests);
FIXTURE_CONFIDENCE overrides the Decision agent's self-reported confidence
(default 0.93, above the STP gate's 0.85 threshold).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from app.llm.adapter import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_S,
    LLMAdapter,
    LLMError,
    LLMRefusal,
    LLMSchemaValidationError,
    LLMTimeout,
    StreamEvent,
    _terminal_error,
)
from app.llm.schemas import (
    DecisionCitation,
    DecisionResult,
    DocumentAnalysis,
    EligibilityResult,
    EvidenceItem,
    ExtractedFacts,
    FraudSimilarityOutput,
    IntakeResult,
    NormalizedClaim,
    PolicyVerification,
)
from app.usage import UsageLogger

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Verdict mapping for the Decision agent — mirrors agents.VERDICT_BY_RECOMMENDATION,
# duplicated here because agents.py imports this package (agents -> adapter ->
# fixture) and importing back would be circular.
_VERDICT_BY_RECOMMENDATION = {
    "auto_approve": "approved",
    "auto_reject": "rejected",
    "escalate": "under_review",
}

_FIXTURE_AGENT_KEYS = ("intake", "policy", "document", "fraud", "eligibility", "decision")


def parse_fixture_faults(raw: str) -> list[tuple[str, str]]:
    """Parse FIXTURE_FAULT into (kind, agent) pairs, ignoring unknown entries."""
    faults: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        kind, _, agent = part.partition(":")
        if kind in ("timeout", "refusal", "refusal_once", "schema", "schema_once") and (
            agent in _FIXTURE_AGENT_KEYS
        ):
            faults.append((kind, agent))
        else:
            logger.warning("fixture_fault_ignored entry=%r", part)
    return faults


def _extract_json(text: str, marker: str) -> dict:
    """Parse the balanced JSON object that starts after `marker`."""
    start = text.find(marker)
    if start == -1:
        raise LLMError(f"fixture adapter: marker {marker!r} not found in prompt")
    opening = text.find("{", start + len(marker))
    if opening == -1:
        raise LLMError(f"fixture adapter: no JSON object after {marker!r}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[opening : index + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError(f"fixture adapter: unparsable JSON: {exc}") from exc
    raise LLMError(f"fixture adapter: unterminated JSON object after {marker!r}")


def _after(text: str, marker: str, *, until: str = "\n") -> str:
    """Text between `marker` and the next `until` (or end of string)."""
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = text.find(until, start)
    return text[start:] if end == -1 else text[start:end].strip()


def _intake_output(user_text: str) -> IntakeResult:
    raw = _extract_json(user_text, "Raw claim submission data: ")
    incident_type = str(raw.get("incidentType", "")).strip().lower().replace(" ", "_")
    try:
        amount = float(raw.get("claimedAmount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return IntakeResult(
        valid=True,
        normalizedData=NormalizedClaim(
            policyNumber=str(raw.get("policyNumber", "")),
            incidentDate=str(raw.get("incidentDate", "")),
            incidentType=incident_type,
            claimedAmount=amount,
            description=str(raw.get("description", "")),
        ),
        missingFields=[],
        flags=[],
        summary="Fixture intake: fields normalized; validity is re-derived in code.",
    )


def _policy_output(user_text: str) -> PolicyVerification:
    policy = _extract_json(user_text, "Policy lookup result: ")
    incident_type = _after(user_text, "incident type: ", until=",").strip().lower()
    try:
        claimed = float(_after(user_text, "claimed amount: ").strip() or 0)
    except ValueError:
        claimed = 0.0
    covered_events = [str(event) for event in (policy.get("covered_events") or [])]
    covered = incident_type in covered_events
    status = str(policy.get("status", "unknown") or "unknown")
    limit = float(policy.get("coverage_limit", 0) or 0)
    return PolicyVerification(
        status=status if status in ("active", "expired", "suspended") else "unknown",
        statusDetail=f"Fixture: policy status read from the record ({status}).",
        coverage="covered" if covered else "not_covered",
        coverageDetail=(
            f"Fixture: {incident_type or 'incident'} vs covered events {covered_events}."
        ),
        appearsOverLimit=claimed > limit,
        claimFrequencyFlag=False,  # recomputed in code from the history tool
        priorClaims12mo=0,
        citedFields=[
            f"policy_number={policy.get('policy_number', '')}",
            f"status={status}",
            f"covered_events={covered_events}",
            f"coverage_limit={limit}",
        ],
        summary="Fixture policy verification: status and coverage read from the record.",
    )


def _document_output(user_text: str) -> DocumentAnalysis:
    description = _after(user_text, "Claim description: ")
    supporting = _after(
        user_text, "Supporting document text: ", until="\nSubmitted claim data:"
    )
    has_docs = bool(supporting) and "No additional documents submitted" not in supporting
    return DocumentAnalysis(
        extracted=ExtractedFacts(
            damageDescribed=description[:300],
            amountsMentioned=[],
        ),
        consistency="consistent" if has_docs else "no_documents",
        redFlags=[],
        supportingEvidence=(
            [EvidenceItem(excerpt=supporting[:200], note="Fixture: verbatim supporting text.")]
            if has_docs
            else []
        ),
        summary="Fixture document analysis: consistency category from document presence.",
    )


def _fraud_output(user_text: str) -> FraudSimilarityOutput:
    return FraudSimilarityOutput(
        similar=False,
        confidence=0.5,
        reasoning="Fixture similarity judgment: duplicate candidates judged not the same event.",
        cited_evidence=[],
    )


def _eligibility_output(user_text: str) -> EligibilityResult:
    return EligibilityResult(
        eligible=True,
        riskFactors=[],
        fraudIndicators=[],
        recommendation="auto_approve",
        inputGaps=[],
        summary="Fixture eligibility: clean pass; the orchestrator recomputes score and routing.",
    )


def _decision_output(user_text: str, claim_id: str | None) -> DecisionResult:
    holder = _after(user_text, "Holder Name: ") or "Policyholder"
    payout_text = _after(user_text, "Computed payout amount: $", until=" ").replace(",", "")
    try:
        payout = float(payout_text)
    except ValueError:
        payout = 0.0
    recommendation = _after(user_text, "recommendation=", until=",").strip() or "escalate"
    verdict = _VERDICT_BY_RECOMMENDATION.get(recommendation, "under_review")
    incident_date = _after(user_text, "incident date=", until=",") or "the reported date"
    incident_type = _after(user_text, "incident type=").strip().replace("_", " ")

    if verdict == "approved":
        subject = f"Your Claim {claim_id} Has Been Approved"
        body = (
            f"Dear {holder},\n\nWe have completed the review of your claim {claim_id} "
            f"for the {incident_type} reported on {incident_date}. We are pleased to "
            f"confirm the claim has been approved. The payout of ${payout:,.2f}, after "
            f"the policy deductible was applied, will be issued within 5-7 business "
            f"days.\n\nThank you for the documentation you provided.\n\n"
            f"Sincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = ["No action is needed; the payout is issued within 5-7 business days."]
    elif verdict == "rejected":
        subject = f"Your Claim {claim_id} Has Been Declined"
        body = (
            f"Dear {holder},\n\nWe have completed the review of your claim {claim_id} "
            f"for the {incident_type} reported on {incident_date}. We are unable to "
            f"approve this claim under the policy terms. If you disagree with this "
            f"decision, you may appeal within 30 days by writing to us with any new "
            f"information.\n\nSincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = ["To appeal, send a written appeal within 30 days with any new information."]
    else:
        subject = f"Your Claim {claim_id} Is Under Review"
        body = (
            f"Dear {holder},\n\nYour claim {claim_id} for the {incident_type} reported "
            f"on {incident_date} is being reviewed by a specialist. The review will "
            f"complete within 5-7 business days, and we will contact you if anything "
            f"further is needed.\n\nSincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = [
            "Watch for messages from your claim specialist over the next 5-7 business days."
        ]

    confidence = float(os.environ.get("FIXTURE_CONFIDENCE", "0.93"))
    return DecisionResult(
        verdict=verdict,  # code re-enforces verdict and payout downstream
        payoutAmount=payout,
        letterSubject=subject,
        letterBody=body,
        nextSteps=next_steps,
        citations=[
            DecisionCitation(
                fact="Deterministic fixture decision over the stored agent outputs.",
                sourceRef="fixture.adapter",
                customerFriendlyExplanation=(
                    "This decision was produced by the deterministic test fixture."
                ),
            )
        ],
        summary=f"Fixture decision ({verdict}) with a customer letter.",
        confidence=confidence,
    )


class FixtureLLMAdapter(LLMAdapter):
    """Same interface as the Anthropic adapter; deterministic fixture outputs."""

    def __init__(self, *, usage_logger: "UsageLogger | None" = None) -> None:
        # No client is ever built: the fixture never touches the network, so
        # the lazy client construction in the base class stays dormant.
        super().__init__(
            timeout_s=float(os.environ.get("LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            usage_logger=usage_logger,
        )
        self._faults = parse_fixture_faults(os.environ.get("FIXTURE_FAULT", ""))
        self._delay_ms = float(os.environ.get("FIXTURE_DELAY_MS", "0") or 0)
        # Per (claim, agent) call attempts — powers the *_once fault modes.
        self._calls: dict[tuple[str | None, str], int] = {}

    def _maybe_fault(self, agent: str | None, claim_id: str | None) -> None:
        """Raise the injected fault for this agent, or return normally."""
        for kind, fault_agent in self._faults:
            if fault_agent != (agent or ""):
                continue
            key = (claim_id, agent)
            attempt = self._calls.get(key, 0)
            self._calls[key] = attempt + 1
            if kind == "timeout":
                raise LLMTimeout(f"Fixture fault: LLMTimeout injected at agent {agent!r}")
            if kind == "refusal":
                raise LLMRefusal(f"Fixture fault: LLMRefusal injected at agent {agent!r}")
            if kind == "refusal_once" and attempt == 0:
                raise LLMRefusal(f"Fixture fault: one-shot LLMRefusal at agent {agent!r}")
            if kind == "schema":
                raise LLMSchemaValidationError(
                    f"Fixture fault: schema miss injected at agent {agent!r}"
                )
            if kind == "schema_once" and attempt == 0:
                raise LLMSchemaValidationError(
                    f"Fixture fault: one-shot schema miss at agent {agent!r}"
                )

    async def complete_structured(
        self,
        *,
        model: str,
        system: object,
        messages: list[dict],
        output_schema: type[T],
        agent: str | None = None,
        claim_id: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> T:
        started = time.perf_counter()
        attempt = 0
        while True:
            if self._delay_ms:
                await asyncio.sleep(self._delay_ms / 1000.0)
            try:
                self._maybe_fault(agent, claim_id)
                result = self._fixture_output(agent or "", messages, output_schema, claim_id)
                await self._log_usage(
                    agent=agent, model=model, usage=None,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    claim_id=claim_id, call_type="complete_structured",
                )
                return result
            except LLMSchemaValidationError as exc:
                # Mirror the real adapter: a schema miss is transient output
                # quality — retried under the bounded cap (schema_once
                # recovers; a persistent schema fault hits the typed terminal).
                if attempt >= self._max_retries:
                    raise _terminal_error("schema", exc) from exc
                attempt += 1
                await asyncio.sleep(0.01)
            except LLMRefusal:
                # Refusals never retry at the adapter level; the agent layer
                # owns the one identical retry (see _complete_with_refusal_retry).
                await self._log_usage(
                    agent=agent, model=model, usage=None,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    claim_id=claim_id, call_type="complete_structured",
                )
                raise

    def _fixture_output(
        self, agent: str, messages: list[dict], output_schema: type[T], claim_id: str | None
    ) -> T:
        user_text = messages[-1]["content"] if messages else ""
        output: T
        if agent == "intake":
            output = _intake_output(user_text)
        elif agent == "policy":
            output = _policy_output(user_text)
        elif agent == "document":
            output = _document_output(user_text)
        elif agent == "fraud":
            output = _fraud_output(user_text)
        elif agent == "eligibility":
            output = _eligibility_output(user_text)
        elif agent == "decision":
            output = _decision_output(user_text, claim_id)
        else:
            raise LLMError(f"fixture adapter: unknown agent {agent!r}")
        if not isinstance(output, output_schema):
            # The builders return their own model types; a mismatch means an
            # internal wiring bug — fail loudly instead of storing junk.
            raise LLMError(
                f"fixture adapter: output for {agent!r} is {type(output).__name__}, "
                f"expected {output_schema.__name__}"
            )
        return output

    async def stream(
        self,
        *,
        model: str,
        system: object,
        messages: list[dict],
        agent: str | None = None,
        claim_id: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[StreamEvent]:
        """No pipeline agent streams letter prose; one synthetic done event."""
        yield StreamEvent(type="done", usage=None)
