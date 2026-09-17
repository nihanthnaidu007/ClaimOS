"""Five-agent claim pipeline: prompts, typed outputs, and orchestration glue.

Implements the Agent Prompt Quality Review (art_rqtooc3G): rewritten prompts
keyed to the typed schemas in app/llm/schemas.py, a shared cached compliance
preamble, per-agent temperature/max_tokens, fail-closed upstream-input checks,
one refusal retry with a template-letter fallback for the customer letter, and
document-text normalization for OCR/multi-page submissions.

Division of labor (glass-box rule): the LLM supplies judgment fields —
classifications, flags, factors, letter prose, citations — while all arithmetic
(payouts, limits, risk totals, validity against objective rules) is computed
here or in app/rating.py and overlaid onto the stored output.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.config import settings
from app.llm.adapter import LLMAdapter, LLMError, LLMRefusal, get_adapter
from app.fraud import (
    FraudFlag,
    detect_fraud_flags,
    incident_fingerprint,
    prior_claim_fingerprints,
)
from app.llm.schemas import (
    DecisionResult,
    DocumentAnalysis,
    EligibilityResult,
    IntakeResult,
    PolicyVerification,
    ValidationFlag,
)
from app.rating import (
    calculate_adjusted_payout,
    consistency_score_for,
    compute_risk_assessment,
)
from database import claims_col, policies_col

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logger = logging.getLogger(__name__)

# Process-wide adapter; tests rebind this attribute to inject a mocked client.
adapter: LLMAdapter = get_adapter()

# Re-applied after normalization so a reformatted (e.g. page-joined) document
# payload can never exceed the API schema's documentText cap.
MAX_DOCUMENT_CHARS = 20_000
THIN_DESCRIPTION_WORDS = 30

# Per-agent generation parameters (review §7 items 6-7): deterministic stages
# run at temperature 0, judgment stages just above, letter prose highest.
# max_tokens: 2048 default; Document (extracted lists) and Decision (letter +
# citations) get headroom. Stop sequences: none anywhere — schema-constrained
# decoding makes them unnecessary and they risk truncating valid output.
AGENT_PARAMS: dict[str, dict] = {
    "intake": {"temperature": 0.0, "max_tokens": 2048},
    "policy": {"temperature": 0.0, "max_tokens": 2048},
    "eligibility": {"temperature": 0.1, "max_tokens": 2048},
    "document": {"temperature": 0.2, "max_tokens": 3000},
    "decision": {"temperature": 0.4, "max_tokens": 2500},
}

# Fixed verdict mapping (Decision agent): recommendation -> customer verdict.
VERDICT_BY_RECOMMENDATION = {
    "auto_approve": "approved",
    "auto_reject": "rejected",
    "escalate": "under_review",
}


# ============ PROMPTS ============
# System prompts are static: the shared preamble is the cached first block and
# agent-specific instructions are the second; every per-claim value (dates,
# thresholds, names, amounts) rides in the user message. Decision's system
# prompt must never embed the holder name — that is what broke cacheability.

PROMPT_PREAMBLE = """You are one agent in ClaimOS, a five-agent insurance claim pipeline. ClaimOS issues claim decisions and a glass-box audit trail: every stored output may be quoted in an evidence pack reviewed by auditors, adjusters, and the policyholder. Rules that bind every agent:

1. Judgment only. All arithmetic — payouts, limits, risk totals — is computed by deterministic code downstream. Never compute a number the code can compute; state the values you observe instead.
2. Fail closed. If an input you were promised is missing, empty, or contradictory, record the gap where your schema says to. Never invent a value; never assume the benign interpretation of missing evidence.
3. Cite as you decide. Where your schema has a citation or cited-fields field, quote the exact upstream values your conclusion relied on and name their source (e.g. "policy.end_date=2027-01-01"). If you cannot quote the text, it is not a finding.
4. Summary discipline. The summary field is for a human auditor: one or two plain sentences with the conclusion and its basis. No step-by-step narration.
5. Output contract. Match the provided response schema exactly; never wrap output in markdown or code fences."""

INTAKE_PROMPT = """You are the Intake Agent in ClaimOS's five-agent claim pipeline. Required-field, type, and range validation already happened at the API boundary; your job is the judgment layer: normalization and risk-relevant flagging.

1. Normalize the submission: dates to YYYY-MM-DD; claimedAmount to a float rounded to 2 decimals; incidentType to lowercase_underscore ("Water Damage" -> "water_damage").
2. Check the incident date is strictly in the past (today's date is in the user message). A future date is a `future_date` flag — it does NOT invalidate the claim.
3. Flag, without invalidating: claimedAmount over the high-amount threshold stated in the user message (`high_amount`); descriptions under the meaningful-word minimum stated in the user message (`thin_description`); fields that contradict each other (`internal_inconsistency`).
4. Set valid=false ONLY when a required field is absent or unparseable. List every absent field in missingFields; every non-blocking issue goes in flags with a code and a one-line detail.
5. summary: one sentence an adjuster can audit — what you normalized, what you flagged."""

POLICY_PROMPT = """You are the Policy Verification Agent. The user message contains two database lookups for this claim: the policy record and the claim history for the policy number. The policy is known to exist — the not-found case never reaches you.

Verify, citing the exact field values you relied on:
1. Policy status on the incident date: compare start_date and end_date to the incident date. Bounds are INCLUSIVE — a claim on the end_date is covered. If start_date or end_date is empty or unparseable, set status="unknown" and say so; never guess coverage from a missing field.
2. Coverage: is the normalized incident type present in covered_events?
3. Limits: report coverage_limit and deductible verbatim in citedFields. Do NOT compute the payout — arithmetic is performed downstream in code. Only state whether the claimed amount appears to exceed the limit (appearsOverLimit).
4. Frequency: claimFrequencyFlag is true when the history shows 3+ claims in the 12-month window stated in the user message; priorClaims12mo is the number of claims in that window.

status is active | expired | suspended | unknown. coverage is covered | not_covered | unknown. In citedFields, list every policy field your verdict relied on as "field=value" strings (e.g. "end_date=2027-01-01", "covered_events=[fire, theft]")."""

DOCUMENT_PROMPT = """You are the Document Analysis Agent. You receive the claimant's description plus supporting document text. Supporting text may come from OCR or PDF extraction: it can contain page markers, page numbers, broken words, table fragments, and repeated boilerplate. Extraction artifacts are NOISE, not evidence of deception — never raise a red flag solely because the text is messy.

1. Facts: extract dates, location, parties, damage description, and amounts ONLY from the supporting document text (not the claimant's own description). For each amount record the surrounding phrase (context) so a reviewer can find it.
2. Consistency against the submitted claim data: consistent | partially_consistent | contradicts | no_documents. Use no_documents (never "consistent") when no supporting text was provided, so downstream scoring skips document evidence.
3. Red flags — each MUST carry a verbatim excerpt from the document text: date_contradiction, amount_mismatch, template_text (copy-pasted boilerplate), damage_type_mismatch. If you cannot quote the text, it is not a red flag.
4. Supporting evidence: verbatim excerpts that corroborate the claim, with a one-line note each.

Do not output a numeric consistency score; the consistency category is what downstream scoring consumes."""

ELIGIBILITY_PROMPT = """You are the Eligibility & Risk Assessment Agent. You receive structured outputs from the Intake, Policy, and Document agents. Policy arithmetic (payout, limit checks) has already been computed — do not re-derive it.

1. In riskFactors, list each risk factor with: a code, points per the schedule below, the exact upstream value it is based on (sourceRef, e.g. "policy.status=expired" or "documents.redFlags[0]=date_contradiction"), and a one-line basis.
2. Schedule: policy_expired_or_suspended +40; incident_not_covered +35; amount_over_limit +25; claim_frequency_flag +25; consistency=contradicts +20; consistency=partially_consistent +10; each red flag +8 (max 24 from red flags); clean-claim base 5-15 scaled by claim size relative to the coverage limit.
3. inputGaps: list every upstream field you expected but found missing or errored. If ANY gap exists, recommendation MUST be "escalate" regardless of score — missing evidence is a human-review condition, never an auto-rejection.
4. eligible = policy active AND incident covered AND within limits.
5. recommendation: auto_approve if the score is at most 29 AND eligible; auto_reject if the score is at least 70 OR the policy is expired/suspended OR the incident is not covered; otherwise escalate.

fraudIndicators carries indicator codes only (e.g. "inflated_amount", "template_document") — definitions live in this prompt, never in the output. Do not output a total score: the orchestrator recomputes it in code and overrides eligible and recommendation where they disagree with the rules above."""

DECISION_PROMPT = """You are the Decision & Communication Agent — the final stage of ClaimOS's five-agent pipeline. The user message carries the eligibility verdict, the policyholder's name, and the computed payout amount. The verdict mapping is fixed: auto_approve -> approved, auto_reject -> rejected, escalate -> under_review. payoutAmount is given to you; echo it — never recalculate.

Citations: for every decision driver (the approval basis, each rejection reason, each escalation reason) produce a citation — the exact upstream fact (e.g. "eligibility.riskFactors[0]: policy status expired on the incident date"), its sourceRef, and a customerFriendlyExplanation in plain language the policyholder can act on. These citations populate the glass-box evidence pack.

Letter rules:
- 300-400 words, professional but warm, addressed to the policyholder by name.
- Reference the specific incident (date, type). Approvals: exact payout amount and the 5-7 business day timeline. Rejections: plain-language reasons, then the appeal process (30 days, in writing). Under review: a specialist is reviewing, the reasons in plain language, and the 5-7 business day review timeline.
- NEVER expose internal machinery in the letter: no risk scores, no "fraud" language, no field names (adjustedPayout, consistency). Translate every driver into customer language; the citations field carries the precise record for auditors.
- nextSteps: 2-4 concrete actions for the policyholder.
- confidence: your calibrated confidence (0.0-1.0) in this verdict — 1.0 only when every upstream input is complete, consistent, and low-risk; missing evidence, contradictions, or fraud indicators lower it.
- Sign as "ClaimOS Claims Processing Team"."""


# ============ LLM CALL HELPERS ============

def _cached_system(agent_prompt: str) -> list[dict]:
    """Static system blocks: the shared compliance preamble as a cache-marked
    prefix (cross-agent cache hits) followed by the agent-specific block."""
    return [
        {"type": "text", "text": PROMPT_PREAMBLE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": agent_prompt},
    ]


class FraudCitedEvidence(BaseModel):
    """One citation: a field of a prior claim record that backs the judgment."""

    from_claim_id: str
    field: str
    value: str


class FraudSimilarityOutput(BaseModel):
    """LLM similarity judgment over duplicate-fingerprint candidates.

    The deterministic duplicate rule has already flagged; the model only
    judges whether the incidents plausibly describe the SAME event. Every
    cited piece of evidence must come from the claim records included in
    the prompt — no invented facts.
    """

    similar: bool
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasoning: str = ""
    cited_evidence: list[FraudCitedEvidence] = Field(default_factory=list)


async def _complete(agent, system_prompt, user_text, output_schema, claim_id):
    """One schema-constrained LLM call for an agent; returns the validated model."""
    params = AGENT_PARAMS[agent]
    return await adapter.complete_structured(
        model=adapter.model_for(agent),
        system=_cached_system(system_prompt),
        messages=[{"role": "user", "content": user_text}],
        output_schema=output_schema,
        agent=agent,
        claim_id=claim_id,
        temperature=params["temperature"],
        max_tokens=params["max_tokens"],
    )


async def _complete_with_refusal_retry(agent, system_prompt, user_text, output_schema, claim_id):
    """_complete with the review's refusal policy (§7 item 5): one identical retry.

    A first LLMRefusal re-runs the exact same call once; a second refusal
    propagates — Decision catches it for the template-letter fallback, every
    other agent fails the run closed.
    """
    try:
        return await _complete(agent, system_prompt, user_text, output_schema, claim_id)
    except LLMRefusal:
        logger.warning("llm_refusal_retrying agent=%s claim_id=%s", agent, claim_id)
        return await _complete(agent, system_prompt, user_text, output_schema, claim_id)


class MissingStageInputError(RuntimeError):
    """An agent started without required upstream outputs.

    Fail-closed: the pipeline marks the run failed instead of continuing with
    empty stage data (the review's silent-{}-propagation finding).
    """


def _require_upstream(state, keys) -> None:
    missing = [key for key in keys if not (state.get(key) or {})]
    if missing:
        raise MissingStageInputError(
            f"missing upstream stage output(s): {', '.join(missing)}"
        )


def _require_intake(state) -> dict:
    """The intake stage's normalized submission, or a fail-closed error."""
    normalized = (state.get('intake') or {}).get('normalizedData') or {}
    if not normalized:
        raise MissingStageInputError("intake.normalizedData is missing or empty")
    return normalized


# ============ TOOL FUNCTIONS ============

async def tool_policy_lookup(policy_number):
    """Look up policy in MongoDB."""
    start = time.time()
    doc = await policies_col.find_one(
        {"policy_number": policy_number},
        {"_id": 0}
    )
    duration = int((time.time() - start) * 1000)
    if doc:
        return {"found": True, "data": doc, "duration_ms": duration}
    return {"found": False, "data": None, "duration_ms": duration}


async def tool_claim_history(policy_number):
    """Get claim history for a policy from the last 12 months.

    The date filter lives in the query (review §7 item 11): the payload is
    capped to the window the frequency formula actually consumes.
    """
    start = time.time()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
    claims = await claims_col.find(
        {"policy_number": policy_number, "claim_date": {"$gte": cutoff}},
        {"_id": 0}
    ).to_list(100)
    duration = int((time.time() - start) * 1000)
    return {"claims": claims, "count": len(claims), "duration_ms": duration}


def _claim_frequency_flag(claims: list[dict]) -> bool:
    """True when the policy has 3+ claims in the last 12 months."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date()
    recent = 0
    for claim in claims:
        try:
            claim_date = date.fromisoformat(str(claim.get("claim_date", ""))[:10])
        except ValueError:
            continue
        if claim_date >= cutoff:
            recent += 1
    return recent >= 3


# ============ AGENT FUNCTIONS ============

def _red_flag_codes(red_flags) -> list[str]:
    """Codes of the document stage's red flags, tolerant of legacy checkpoints."""
    return [
        flag.get('code', '') if isinstance(flag, dict) else str(flag)
        for flag in (red_flags or [])
    ]


async def intake_agent(state):
    """Agent 1: validate and normalize raw claim submission."""
    if not state.get('input'):
        raise MissingStageInputError(
            "INTAKE_AGENT requires the raw submission (state['input']); it is missing"
        )
    # documentText can carry up to 20k chars that Intake never reads (review §4.1).
    payload = {k: v for k, v in state['input'].items() if k != 'documentText'}
    threshold = settings.intake_flag_amount_threshold
    user_text = (
        f"Today's date is {date.today().isoformat()}. "
        f"Flag thresholds: high_amount over ${threshold:,.0f}; "
        f"thin_description under {THIN_DESCRIPTION_WORDS} meaningful words. "
        f"Raw claim submission data: {json.dumps(payload)}"
    )
    result = await _complete_with_refusal_retry(
        "intake", INTAKE_PROMPT, user_text, IntakeResult, state['claimId']
    )
    state['intake'] = _deterministic_intake_verdict(result).model_dump()
    return state


_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _deterministic_intake_verdict(output: IntakeResult) -> IntakeResult:
    """Decide intake validity in code, not with the LLM.

    Updated to the review's flag semantics (§6.1): valid=false ONLY for
    absent/unparseable required fields. A future date no longer invalidates —
    it rides along as a `future_date` flag for downstream judgment, as do
    high amounts and thin descriptions. Code-owned flags replace any model
    flag of the same code so the two can never disagree.
    """
    data = output.normalizedData
    missing: list[str] = []
    if not data.policyNumber:
        missing.append("policyNumber")
    if not data.incidentType:
        missing.append("incidentType")
    if not data.description:
        missing.append("description")
    if data.claimedAmount <= 0:
        missing.append("claimedAmount")
    try:
        incident = date.fromisoformat(data.incidentDate)
    except ValueError:
        incident = None
        missing.append("incidentDate")

    flags: list[ValidationFlag] = [
        f for f in output.flags
        if f.code not in ("future_date", "high_amount", "thin_description")
    ]

    def _add(code: str, detail: str) -> None:
        flags.append(ValidationFlag(code=code, detail=detail))

    if incident is not None and incident > date.today():
        _add("future_date", f"Incident date {data.incidentDate} is in the future.")
    threshold = settings.intake_flag_amount_threshold
    if data.claimedAmount > threshold:
        _add(
            "high_amount",
            f"Claimed amount ${data.claimedAmount:,.2f} exceeds the "
            f"${threshold:,.0f} flag threshold.",
        )
    words = len(_WORD_RE.findall(data.description or ""))
    if data.description and words < THIN_DESCRIPTION_WORDS:
        _add(
            "thin_description",
            f"Description has {words} meaningful words (minimum {THIN_DESCRIPTION_WORDS}).",
        )

    update: dict = {"valid": not missing, "missingFields": missing, "flags": flags}
    if missing:
        update["summary"] = (
            f"Invalid submission: missing or unparseable {', '.join(missing)}."
        )
    return output.model_copy(update=update)


async def policy_agent(state):
    """Agent 2: verify policy coverage using database tools."""
    normalized = _require_intake(state)
    policy_number = normalized.get('policyNumber', '')
    if not policy_number:
        raise MissingStageInputError(
            "POLICY_AGENT requires intake.normalizedData.policyNumber; it is empty"
        )

    # Execute tools
    lookup_result = await tool_policy_lookup(policy_number)
    history_result = await tool_claim_history(policy_number)

    # Full tool I/O in the trace (review §7 item 3): the evidence pack needs the
    # actual query params and results, not summary strings.
    tool_calls = [
        {
            "tool": "policyLookup",
            "input": {"policy_number": policy_number},
            "output": {"found": lookup_result['found'], "data": lookup_result['data']},
            "duration_ms": lookup_result["duration_ms"],
        },
        {
            "tool": "claimHistory",
            "input": {"policy_number": policy_number, "window_days": 365},
            "output": {"count": history_result["count"], "claims": history_result["claims"]},
            "duration_ms": history_result["duration_ms"],
        },
    ]

    # Update agent logs with tool calls
    for log in state['agentLogs']:
        if log['agent'] == 'POLICY_AGENT':
            log['toolsCalled'] = tool_calls

    if not lookup_result['found']:
        # Deterministic not-found branch, typed to PolicyVerification: no LLM
        # call for a case code can decide. `found` is an orchestrator key the
        # pipeline halts on — deliberately not part of the LLM schema.
        state['policy'] = {
            "found": False,
            "status": "unknown",
            "statusDetail": f"Policy {policy_number} not found in database",
            "coverage": "unknown",
            "coverageDetail": "N/A — no policy record",
            "appearsOverLimit": False,
            "claimFrequencyFlag": False,
            "priorClaims12mo": 0,
            "citedFields": [],
            "summary": f"Policy lookup for {policy_number} returned no results. Pipeline must halt.",
        }
        return state

    policy_data = lookup_result['data']
    state['policy']['policyData'] = policy_data

    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
    user_text = (
        f"Today's date is {date.today().isoformat()}; the 12-month claim window "
        f"covers {cutoff} through today.\n"
        f"Policy lookup result: {json.dumps(policy_data)}\n"
        f"Claim history result (last 12 months): {json.dumps(history_result['claims'])}\n"
        f"Submitted claim data — incident date: {normalized.get('incidentDate', '')}, "
        f"incident type: {normalized.get('incidentType', '')}, "
        f"claimed amount: {normalized.get('claimedAmount', 0)}"
    )

    result = await _complete_with_refusal_retry(
        "policy", POLICY_PROMPT, user_text, PolicyVerification, state['claimId']
    )
    policy_result = result.model_dump()
    policy_result['found'] = True
    policy_result['policyData'] = policy_data

    # Deterministic money math and claim frequency: the LLM's narrative stands,
    # but the numbers feeding eligibility and the decision are computed here.
    payout = calculate_adjusted_payout(
        claimed_amount=float(normalized.get('claimedAmount', 0) or 0),
        coverage_limit=float(policy_data.get('coverage_limit', 0) or 0),
        deductible=float(policy_data.get('deductible', 0) or 0),
    )
    policy_result['withinLimits'] = payout.within_limits
    policy_result['adjustedPayout'] = payout.adjusted_payout
    policy_result['deductibleApplied'] = payout.deductible_applied
    policy_result['claimFrequencyFlag'] = _claim_frequency_flag(history_result['claims'])
    policy_result['priorClaims12mo'] = history_result['count']
    state['policy'] = policy_result
    return state


async def document_agent(state):
    """Agent 3: analyze claim documents and description."""
    _require_upstream(state, ("intake",))
    normalized = _require_intake(state)
    doc_text = _normalize_document_text(state['input'].get('documentText', ''))
    user_text = (
        f"Claim description: {normalized.get('description', '')}\n"
        f"Supporting document text: {doc_text if doc_text else 'No additional documents submitted.'}\n"
        f"Submitted claim data: incident date={normalized.get('incidentDate', '')}, "
        f"incident type={normalized.get('incidentType', '')}, "
        f"claimed amount=${normalized.get('claimedAmount', 0)}, "
        f"policy type={state.get('policy', {}).get('policyData', {}).get('policy_type', 'unknown')}"
    )

    result = await _complete_with_refusal_retry(
        "document", DOCUMENT_PROMPT, user_text, DocumentAnalysis, state['claimId']
    )
    documents = result.model_dump()
    # Numeric UI/evidence-pack score derived from the category in code (the
    # model never outputs a score — review §4.3).
    score = consistency_score_for(result.consistency)
    if score is not None:
        documents['consistencyScore'] = score
    state['documents'] = documents
    return state


async def eligibility_agent(state):
    """Agent 4: risk factors from the LLM; score, routing, eligibility in code."""
    _require_upstream(state, ("intake", "policy", "documents"))
    policy_data = state['policy'].get('policyData') or {}
    if not policy_data:
        raise MissingStageInputError(
            "ELIGIBILITY_AGENT requires the policy stage's policyData; it is missing"
        )
    documents = state['documents']
    red_flags = documents.get('redFlags') or []
    user_text = (
        "Prior agent outputs (structured):\n"
        f"INTAKE: valid={state['intake'].get('valid')}, "
        f"normalized data={json.dumps(state['intake'].get('normalizedData', {}))}, "
        f"flags={json.dumps(state['intake'].get('flags', []))}\n"
        f"POLICY: found={state['policy'].get('found')}, status={state['policy'].get('status')}, "
        f"coverage={state['policy'].get('coverage')}, withinLimits={state['policy'].get('withinLimits')}, "
        f"adjustedPayout={state['policy'].get('adjustedPayout')}, "
        f"claimFrequencyFlag={state['policy'].get('claimFrequencyFlag')}, "
        f"priorClaims12mo={state['policy'].get('priorClaims12mo', 0)}\n"
        f"DOCUMENTS: consistency={documents.get('consistency')}, "
        f"redFlags={json.dumps(_red_flag_codes(red_flags))}\n"
        "Policy record context: "
        f"status={policy_data.get('status', 'unknown')}, "
        f"coverage_limit={policy_data.get('coverage_limit', 0)}, "
        f"deductible={policy_data.get('deductible', 0)}, "
        f"fraudFlags={json.dumps(state.get('fraud', {}).get('flags', []))}, "
        f"fraudSimilarity={json.dumps(state.get('fraud', {}).get('similarity') or {})}"
    )

    result = await _complete_with_refusal_retry(
        "eligibility", ELIGIBILITY_PROMPT, user_text, EligibilityResult, state['claimId']
    )
    eligibility = result.model_dump()

    # Deterministic scoring: the LLM's typed factor enumeration is kept as its
    # judgment record (riskFactorDetails, with sourceRef for the evidence pack);
    # the score, routing, and eligibility flag are computed here and override
    # the model's. riskFactors stays the plain string list the dashboard and
    # evidence pack render today.
    assessment = compute_risk_assessment(
        policy_status=str(policy_data.get('status', '') or ''),
        incident_type=state['intake'].get('normalizedData', {}).get('incidentType', ''),
        covered_events=list(policy_data.get('covered_events') or []),
        claimed_amount=float(state['intake'].get('normalizedData', {}).get('claimedAmount', 0) or 0),
        coverage_limit=float(policy_data.get('coverage_limit', 0) or 0),
        deductible=float(policy_data.get('deductible', 0) or 0),
        claim_frequency_flag=bool(state['policy'].get('claimFrequencyFlag', False)),
        consistency=documents.get('consistency'),
        red_flag_count=len(red_flags),
        fraud_flag_severities=[f.get('severity', 'low') for f in state.get('fraud', {}).get('flags', [])],
    )
    eligibility['riskFactorDetails'] = eligibility.pop('riskFactors')
    eligibility['riskFactors'] = assessment.risk_factors
    eligibility['riskScore'] = assessment.risk_score
    eligibility['recommendation'] = assessment.recommendation
    eligibility['eligible'] = assessment.eligible
    state['eligibility'] = eligibility
    return state


# Internal machinery banned from the customer letter (review §7 item 12):
# risk/fraud/score vocabulary is scanned before the letter is persisted.
_JARGON_RE = re.compile(r"\b(risk\w*|fraud\w*|score\w*)\b", re.IGNORECASE)


def _letter_has_jargon(letter_body: str) -> bool:
    return bool(_JARGON_RE.search(letter_body or ""))


def _enforce_decision_invariants(
    decision: DecisionResult, *, verdict: str, payout: float, claim_id: str
) -> dict:
    """Verdict mapping and payout are code-owned: override a diverging model."""
    enforced = decision.model_dump()
    if enforced['verdict'] != verdict:
        logger.warning(
            "decision_verdict_overridden claim_id=%s model=%s code=%s",
            claim_id, enforced['verdict'], verdict,
        )
        enforced['verdict'] = verdict
    if enforced['payoutAmount'] != payout:
        logger.warning(
            "decision_payout_overridden claim_id=%s model=%s code=%s",
            claim_id, enforced['payoutAmount'], payout,
        )
        enforced['payoutAmount'] = payout
    return enforced


async def _model_decision(state, holder_name: str, payout: float) -> dict:
    """One Decision LLM attempt: complete, then enforce code-owned invariants."""
    policy_data = state['policy'].get('policyData') or {}
    normalized = state['intake'].get('normalizedData', {})
    documents = state['documents']
    recommendation = state['eligibility'].get('recommendation', 'escalate')
    user_text = (
        "All agent outputs (structured):\n"
        f"INTAKE: valid={state['intake'].get('valid')}, "
        f"incident date={normalized.get('incidentDate', '')}, "
        f"incident type={normalized.get('incidentType', '')}\n"
        f"POLICY: found={state['policy'].get('found')}, status={state['policy'].get('status')}, "
        f"adjustedPayout={payout}, deductibleApplied={state['policy'].get('deductibleApplied', 0)}\n"
        f"DOCUMENTS: consistency={documents.get('consistency')}, "
        f"redFlags={json.dumps(_red_flag_codes(documents.get('redFlags')))}\n"
        f"ELIGIBILITY: eligible={state['eligibility'].get('eligible')}, "
        f"riskScore={state['eligibility'].get('riskScore', 0)}, "
        f"recommendation={recommendation}, "
        f"riskFactors={json.dumps(state['eligibility'].get('riskFactors', []))}\n"
        f"Claim ID: {state['claimId']}\n"
        f"Policy Number: {policy_data.get('policy_number', '')}\n"
        f"Holder Name: {holder_name}\n"
        f"Computed payout amount: ${payout:,.2f} (echo this — never recalculate)"
    )
    result = await _complete_with_refusal_retry(
        "decision", DECISION_PROMPT, user_text, DecisionResult, state['claimId']
    )
    return _enforce_decision_invariants(
        result,
        verdict=VERDICT_BY_RECOMMENDATION.get(recommendation, "under_review"),
        payout=payout,
        claim_id=state['claimId'],
    )


def _template_decision(state) -> dict:
    """Deterministic DecisionResult when the model refuses — the claim must
    still end with a customer artifact (review §7 item 5). Built only from
    code-owned values; the 0.0 confidence makes the STP gate escalate it, so a
    fallback letter can never auto-finalize a claim."""
    recommendation = state['eligibility'].get('recommendation', 'escalate')
    verdict = VERDICT_BY_RECOMMENDATION.get(recommendation, 'under_review')
    payout = float(state['policy'].get('adjustedPayout', 0) or 0)
    policy_data = state['policy'].get('policyData') or {}
    holder = policy_data.get('holder_name') or 'Policyholder'
    normalized = state['intake'].get('normalizedData', {})
    incident_date = normalized.get('incidentDate', '') or 'the reported date'
    incident_type = (normalized.get('incidentType', '') or 'incident').replace('_', ' ')
    claim_id = state['claimId']
    factors = [str(f) for f in (state['eligibility'].get('riskFactors') or [])]

    if verdict == 'approved':
        subject = f"Your Claim {claim_id} Has Been Approved"
        body = (
            f"Dear {holder},\n\nWe are pleased to inform you that your claim {claim_id} "
            f"for the {incident_type} reported on {incident_date} has been approved. "
            f"The payout of ${payout:,.2f}, after the deductible was applied, will be "
            f"issued within 5-7 business days. Thank you for submitting the "
            f"documentation supporting this claim.\n\n"
            f"Sincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = [
            "No action is needed from you; the payout is issued within 5-7 business days.",
        ]
    elif verdict == 'rejected':
        reasons = "; ".join(factors[:3]) if factors else (
            "the incident is not covered by the policy terms"
        )
        subject = f"Your Claim {claim_id} Has Been Declined"
        body = (
            f"Dear {holder},\n\nAfter a full review, we are unable to approve your "
            f"claim {claim_id} for the {incident_type} reported on {incident_date}. "
            f"The reasons: {reasons}. If you disagree with this decision, you may "
            f"appeal within 30 days by writing to us with any new information.\n\n"
            f"Sincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = [
            "To appeal, send a written appeal within 30 days with any new information.",
            "Contact your claim adjuster with questions about the decision.",
        ]
    else:
        subject = f"Your Claim {claim_id} Is Under Review"
        review_note = f"What we are reviewing: {'; '.join(factors[:3])}. " if factors else ""
        body = (
            f"Dear {holder},\n\nYour claim {claim_id} for the {incident_type} reported "
            f"on {incident_date} is being reviewed by a specialist. {review_note}"
            f"A specialist will complete the review within 5-7 business days and will "
            f"contact you if anything further is needed.\n\n"
            f"Sincerely,\nClaimOS Claims Processing Team"
        )
        next_steps = [
            "Watch for messages from your claim specialist over the next 5-7 business days.",
            "Reply promptly if additional documentation is requested.",
        ]

    citations = [
        {
            "fact": factor,
            "sourceRef": f"eligibility.riskFactors[{i}]",
            "customerFriendlyExplanation": factor,
        }
        for i, factor in enumerate(factors[:5])
    ]
    return {
        "verdict": verdict,
        "payoutAmount": payout,
        "letterSubject": subject,
        "letterBody": body,
        "nextSteps": next_steps,
        "citations": citations,
        "summary": "Template letter issued deterministically after the model refused the decision call.",
        "confidence": 0.0,
    }

async def fraud_agent(state):
    """Agent 4: deterministic fraud cross-check + one LLM similarity judgment.

    Rules run first (pure functions, app/fraud.py); the LLM is invoked only
    when the duplicate-fingerprint rule found candidates, judging whether
    the incidents plausibly describe the same event, citing fields from the
    prior claim records. An LLM failure degrades to the deterministic flags
    (recorded, never silent) — fraud checking must not break claims intake.
    """
    claim_id = state['claimId']
    intake = state['intake'].get('normalizedData', {})
    policy_data = state['policy'].get('policyData', {})
    policy_number = (
        intake.get('policyNumber')
        or policy_data.get('policy_number')
        or state['input'].get('policyNumber', '')
    )
    incident_date = str(intake.get('incidentDate', '') or '')
    incident_type = str(intake.get('incidentType', '') or '')
    claimed_amount = float(intake.get('claimedAmount', 0) or 0)

    fingerprint = incident_fingerprint(policy_number, incident_date, incident_type)

    # Prior claims on the same policy (seeded and pipeline-saved rows) — the
    # duplicate rule and the similarity candidates share this one query.
    prior_rows = await claims_col.find(
        {"policy_number": policy_number, "id": {"$ne": claim_id}},
        {
            "_id": 0,
            "id": 1,
            "policy_number": 1,
            "incident_date": 1,
            "incident_type": 1,
            "incident_fingerprint": 1,
            "claimed_amount": 1,
            "claim_date": 1,
            "status": 1,
        },
    ).to_list(200)
    prior_fingerprints = prior_claim_fingerprints(prior_rows)

    flags: list[FraudFlag] = detect_fraud_flags(
        claimed_amount=claimed_amount,
        coverage_limit=float(policy_data.get('coverage_limit', 0) or 0),
        incident_date=incident_date,
        policy_start_date=str(policy_data.get('start_date', '') or ''),
        fingerprint=fingerprint,
        prior_incident_fingerprints=prior_fingerprints,
        today=date.today(),
    )

    # Similarity candidates: prior rows whose fingerprint equals this claim's.
    candidates = [
        row for row in prior_rows
        if (row.get("incident_fingerprint") or incident_fingerprint(
            str(row.get("policy_number", "")),
            str(row.get("incident_date", "")),
            str(row.get("incident_type", "")),
        )) == fingerprint
    ]

    similarity: dict | None = None
    if candidates:
        system_prompt = (
            "You are the Fraud Similarity Judge. The claims system detected that a new "
            "claim shares an incident fingerprint (same policy, incident date, incident "
            "type) with prior claims. Decide whether the incidents plausibly describe "
            "the SAME event or whether the match is a coincidence (e.g. a legitimate "
            "recurring event). Judge ONLY from the record excerpts provided — every "
            "cited_evidence entry must quote a field from those records verbatim, with "
            "the claim id it came from. Never invent facts."
        )
        user_text = json.dumps(
            {
                "new_claim": {
                    "claimId": claim_id,
                    "policyNumber": policy_number,
                    "incidentDate": incident_date,
                    "incidentType": incident_type,
                    "claimedAmount": claimed_amount,
                },
                "prior_claims": candidates,
            },
            default=str,
        )
        try:
            result = await _complete("fraud", system_prompt, user_text, FraudSimilarityOutput, claim_id)
            similarity = result.model_dump()
        except LLMError as exc:
            # Degraded mode: deterministic flags stand; record the failure.
            logger.warning("fraud_similarity_llm_failed claim_id=%s error=%s", claim_id, exc)
            similarity = {"error": str(exc)}

    state['fraud'] = {
        "fingerprint": fingerprint,
        "flags": [flag.as_payload() for flag in flags],
        "similarity": similarity,
    }
    return state



async def decision_agent(state):
    """Agent 5: issue decision and generate the customer communication.

    The customer letter never blocks on the model: after the refusal retry is
    exhausted a deterministic template letter is issued, and a letter that
    leaks internal jargon is regenerated once, then replaced by the template.
    Other LLM failures still fail the run closed — the durable queue retries
    transient outages instead of silently shipping a canned letter.
    """
    _require_upstream(state, ("intake", "policy", "documents", "eligibility"))
    policy_data = state['policy'].get('policyData') or {}
    holder_name = policy_data.get('holder_name') or 'Policyholder'
    payout = float(state['policy'].get('adjustedPayout', 0) or 0)

    try:
        decision = await _model_decision(state, holder_name, payout)
        if _letter_has_jargon(decision['letterBody']):
            logger.warning(
                "decision_letter_jargon claim_id=%s — regenerating once", state['claimId']
            )
            try:
                decision = await _model_decision(state, holder_name, payout)
            except LLMError:
                decision = _template_decision(state)
            if _letter_has_jargon(decision['letterBody']):
                decision = _template_decision(state)
    except LLMRefusal:
        logger.warning(
            "decision_refused claim_id=%s — template letter fallback", state['claimId']
        )
        decision = _template_decision(state)

    decision['emailSent'] = False  # mock email, unchanged contract
    state['decision'] = decision
    return state


# ============ DOCUMENT TEXT NORMALIZATION ============
# Review §5.1/§6.3: multi-page submissions arrive as one blob with page
# boundaries lost, and noisy scans invite false template_text/contradiction
# flags. Cleanup runs in code before prompting; the prompt tells the model
# extraction artifacts are noise, never evidence of deception.

_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    " ": " ",  # non-breaking space
    " ": " ",  # figure space
    " ": " ",  # narrow no-break space
})


def _normalize_document_text(text: str, max_chars: int = MAX_DOCUMENT_CHARS) -> str:
    """Join multi-page document blobs and clean OCR whitespace before prompting.

    Form-feed page splits become explicit "--- PAGE n ---" markers, ligatures
    and odd space characters fold to ASCII, words hyphenated across line
    breaks are rejoined, and runs of whitespace collapse.
    """
    if not text:
        return ""
    cleaned = text.translate(_LIGATURES).replace("\r\n", "\n").replace("\r", "\n")
    pages = [page for page in cleaned.split("\f") if page.strip()]
    if len(pages) > 1:
        cleaned = "\n\n".join(
            f"--- PAGE {number} ---\n{page.strip()}"
            for number, page in enumerate(pages, start=1)
        )
    cleaned = re.sub(r"(\w)-\n(\w)", r"\1\2", cleaned)  # de-hyphenate line breaks
    cleaned = re.sub(r"[^\S\n]+", " ", cleaned)          # horizontal runs -> one space
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()  # collapse blank-line runs
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + f"\n\n[Document truncated at {max_chars} characters]"
    return cleaned


# ============ PIPELINE STAGES ============
# Executed in order by pipeline.PipelineRunner. Each entry owns the state key
# its agent writes plus the display metadata the SSE stream and dashboard use.
PIPELINE_STAGES = [
    {"name": "INTAKE_AGENT", "fn": intake_agent, "stateKey": "intake",
     "label": "Intake & Validation", "desc": "Validating claim fields and normalizing data"},
    {"name": "POLICY_AGENT", "fn": policy_agent, "stateKey": "policy",
     "label": "Policy Verification", "desc": "Querying policy database for coverage verification"},
    {"name": "DOCUMENT_AGENT", "fn": document_agent, "stateKey": "documents",
     "label": "Document Analysis", "desc": "Analyzing claim documents for evidence and consistency"},
    {"name": "FRAUD_AGENT", "fn": fraud_agent, "stateKey": "fraud",
     "label": "Fraud Cross-Check", "desc": "Running deterministic fraud rules and duplicate-incident similarity"},
    {"name": "ELIGIBILITY_AGENT", "fn": eligibility_agent, "stateKey": "eligibility",
     "label": "Eligibility & Risk", "desc": "Calculating risk score and eligibility verdict"},
    {"name": "DECISION_AGENT", "fn": decision_agent, "stateKey": "decision",
     "label": "Decision & Communication", "desc": "Issuing final verdict and drafting communication"},
]
