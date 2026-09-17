import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.events import emit_event
from app.llm.adapter import LLMAdapter, get_adapter
from database import claims_col, policies_col

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logger = logging.getLogger(__name__)

# Process-wide adapter; tests rebind this attribute to inject a mocked client.
adapter: LLMAdapter = get_adapter()


# ============ AGENT OUTPUT MODELS ============
# Field names are camelCase so the stored agent trace and SSE payloads keep the
# exact shape the pipeline emitted before the adapter migration.

class NormalizedData(BaseModel):
    policyNumber: str = ""
    incidentDate: str = ""
    incidentType: str = ""
    claimedAmount: float = 0.0
    description: str = ""


class IntakeOutput(BaseModel):
    valid: bool
    normalizedData: NormalizedData
    missingFields: list[str] = Field(default_factory=list)
    validationNotes: str = ""
    reasoning: str = ""


class PolicyOutput(BaseModel):
    found: bool
    statusCheck: str = ""
    coverageCheck: str = ""
    withinLimits: bool = False
    adjustedPayout: float = 0.0
    deductibleApplied: float = 0.0
    claimFrequencyFlag: bool = False
    reasoning: str = ""


class ExtractedFacts(BaseModel):
    datesFound: list[str] = Field(default_factory=list)
    locationMentioned: str = ""
    partiesInvolved: str = ""
    damageDescribed: str = ""
    amountsMentioned: list[float] = Field(default_factory=list)


class DocumentOutput(BaseModel):
    extracted: ExtractedFacts
    consistencyScore: int = 0
    redFlags: list[str] = Field(default_factory=list)
    supportingEvidence: list[str] = Field(default_factory=list)
    reasoning: str = ""


class EligibilityOutput(BaseModel):
    eligible: bool
    riskScore: int = 0
    riskFactors: list[str] = Field(default_factory=list)
    fraudIndicators: list[str] = Field(default_factory=list)
    recommendation: str = ""
    reasoning: str = ""


class DecisionOutput(BaseModel):
    verdict: str
    payoutAmount: float = 0.0
    letterSubject: str = ""
    letterBody: str = ""
    nextSteps: list[str] = Field(default_factory=list)
    reasoning: str = ""


async def _complete(agent, system_prompt, user_text, output_schema, claim_id):
    """One schema-constrained LLM call for an agent; returns the validated model."""
    return await adapter.complete_structured(
        model=adapter.model_for(agent),
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
        output_schema=output_schema,
        agent=agent,
        claim_id=claim_id,
    )


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
    """Get claim history for a policy from last 12 months."""
    start = time.time()
    claims = await claims_col.find(
        {"policy_number": policy_number},
        {"_id": 0}
    ).to_list(100)
    duration = int((time.time() - start) * 1000)
    return {"claims": claims, "count": len(claims), "duration_ms": duration}


# ============ AGENT FUNCTIONS ============

async def intake_agent(state):
    """Agent 1: Validate and normalize raw claim submission."""
    system_prompt = """You are the Intake Agent in a 5-agent insurance claim processing pipeline. Your sole responsibility is to validate and normalize the raw claim submission.

Your tasks:
1. Check all required fields are present: policyNumber, incidentDate, incidentType, claimedAmount, description
2. Validate data types: date must be a real date in the past, amount must be positive number
3. Normalize: dates to YYYY-MM-DD, amounts to float with 2 decimals, incidentType to lowercase_underscore
4. Flag issues: future dates, unrealistically high amounts (>$500,000), empty descriptions (<20 chars)
5. If all required fields present and valid, mark as valid. If critical fields missing, mark invalid.

Return the structured output defined by the response schema."""

    user_text = f"Raw claim submission data: {json.dumps(state['input'])}"
    result = await _complete("intake", system_prompt, user_text, IntakeOutput, state['claimId'])
    state['intake'] = result.model_dump()
    return state


async def policy_agent(state):
    """Agent 2: Verify policy coverage using database tools."""
    policy_number = state['intake'].get('normalizedData', {}).get('policyNumber', state['input'].get('policyNumber', ''))
    
    # Execute tools
    lookup_result = await tool_policy_lookup(policy_number)
    history_result = await tool_claim_history(policy_number)
    
    tool_calls = [
        {
            "tool": "policyLookup",
            "input": policy_number,
            "output": f"Found: {lookup_result['found']}",
            "duration_ms": lookup_result["duration_ms"]
        },
        {
            "tool": "claimHistory",
            "input": policy_number,
            "output": f"{history_result['count']} prior claims",
            "duration_ms": history_result["duration_ms"]
        }
    ]
    
    # Update agent logs with tool calls
    for log in state['agentLogs']:
        if log['agent'] == 'POLICY_AGENT':
            log['toolsCalled'] = tool_calls
    
    if not lookup_result['found']:
        state['policy'] = {
            "found": False,
            "statusCheck": f"Policy {policy_number} not found in database",
            "coverageCheck": "N/A",
            "withinLimits": False,
            "adjustedPayout": 0,
            "deductibleApplied": 0,
            "claimFrequencyFlag": False,
            "reasoning": f"Policy lookup for {policy_number} returned no results. Pipeline must halt."
        }
        return state

    policy_data = lookup_result['data']
    state['policy']['policyData'] = policy_data
    
    system_prompt = """You are the Policy Verification Agent. You receive live database query results from two tools and must interpret them to verify claim coverage.

Analyze:
1. Policy status on the incident date — is the policy active? Check start_date and end_date against the incident date.
2. Coverage match — is the incident_type in the covered_events array?
3. Amount check — is claimed amount <= (coverage_limit - deductible)?
4. Calculate: adjustedPayout = claimedAmount - deductible (if within limit), or coverage_limit - deductible (if over limit)
5. Claim frequency — if 3+ claims in past 12 months, flag it

Return the structured output defined by the response schema."""

    normalized = state['intake'].get('normalizedData', {})
    user_text = f"""Policy lookup result: {json.dumps(policy_data)}
Claim history result: {json.dumps(history_result['claims'])}
Submitted claim data - incident date: {normalized.get('incidentDate', '')}, incident type: {normalized.get('incidentType', '')}, claimed amount: {normalized.get('claimedAmount', 0)}"""
    
    result = await _complete("policy", system_prompt, user_text, PolicyOutput, state['claimId'])
    policy_result = result.model_dump()
    policy_result['policyData'] = policy_data
    state['policy'] = policy_result
    return state


async def document_agent(state):
    """Agent 3: Analyze claim documents and description."""
    system_prompt = """You are the Document Analysis Agent. You receive the claim description and any supporting document text submitted by the claimant. Your job is forensic analysis.

Tasks:
1. Extract key facts from the text: any dates mentioned, location, parties involved, damage descriptions, amounts referenced
2. Cross-check: do extracted facts match the submitted claim data? (dates, amounts, incident type)
3. Assess document quality: is the description detailed and specific, or vague and generic?
4. Identify red flags:
   - Dates in document contradict submitted incident date
   - Amounts mentioned differ significantly from claimed amount
   - Description is extremely vague (<30 words of actual detail)
   - Description seems copy-pasted or template-like
   - Claimed damage type inconsistent with policy type
5. Identify supporting evidence: specific details that corroborate the claim

Scoring: consistencyScore 0-100.

Return the structured output defined by the response schema."""

    normalized = state['intake'].get('normalizedData', {})
    doc_text = state['input'].get('documentText', '')
    user_text = f"""Claim description: {normalized.get('description', state['input'].get('description', ''))}
Supporting document text: {doc_text if doc_text else 'No additional documents submitted.'}
Submitted claim data: incident date={normalized.get('incidentDate', '')}, incident type={normalized.get('incidentType', '')}, claimed amount=${normalized.get('claimedAmount', 0)}, policy type={state.get('policy', {}).get('policyData', {}).get('policy_type', 'unknown')}"""
    
    result = await _complete("document", system_prompt, user_text, DocumentOutput, state['claimId'])
    state['documents'] = result.model_dump()
    return state


async def eligibility_agent(state):
    """Agent 4: Calculate risk score and eligibility."""
    system_prompt = """You are the Eligibility & Risk Assessment Agent. You receive all prior agent outputs and must calculate a definitive risk score and eligibility verdict.

RISK SCORING FORMULA (additive):
- Policy expired or suspended: +40 points
- Incident type NOT covered: +35 points
- Claimed amount exceeds coverage limit: +25 points
- Claim frequency flag (3+ claims/12mo): +25 points
- Document consistency score < 50: +20 points
- Document consistency score 50-70: +10 points
- Each red flag in documents: +8 points (max +24)
- Base score for clean claim: 5 to 15 based on claim size relative to limit

ROUTING:
- Risk score 0-29: recommend "auto_approve"
- Risk score 30-69: recommend "escalate" (human review)
- Risk score 70-100: recommend "auto_reject"
- If policy not covered OR policy expired: always "auto_reject" regardless of score

ELIGIBILITY:
- eligible = true only if: policy active, incident covered, amount within limits

Return the structured output defined by the response schema."""

    user_text = f"""Prior agent outputs:
INTAKE: {json.dumps(state['intake'])}
POLICY: {json.dumps({k: v for k, v in state['policy'].items() if k != 'policyData'})}
Policy Data: status={state['policy'].get('policyData', {}).get('status', 'unknown')}, coverage_limit={state['policy'].get('policyData', {}).get('coverage_limit', 0)}, deductible={state['policy'].get('policyData', {}).get('deductible', 0)}
DOCUMENTS: {json.dumps(state['documents'])}"""
    
    result = await _complete("eligibility", system_prompt, user_text, EligibilityOutput, state['claimId'])
    state['eligibility'] = result.model_dump()
    return state


async def decision_agent(state):
    """Agent 5: Issue decision and generate communication."""
    policy_data = state['policy'].get('policyData', {})
    holder_name = policy_data.get('holder_name', 'Policyholder')
    
    system_prompt = f"""You are the Decision & Communication Agent — the final agent in the pipeline. You issue the official claim decision and write the communication to the policyholder.

Based on the eligibility verdict and recommendation:
- "auto_approve": Issue approval, state payout amount, express congratulations, give 5-7 business day payout timeline
- "auto_reject": Issue rejection, cite specific reasons from eligibility analysis, explain appeal process (30 days, in writing)
- "escalate": Inform policyholder of manual review, explain why (cite risk factors), give 5-7 business day review timeline

LETTER REQUIREMENTS:
- 300-400 words, professional but warm tone
- Use policyholder's actual name: {holder_name}
- Reference specific claim details
- For approvals: include exact payout amount after deductible
- Sign as "ClaimOS Claims Processing Team"

Return the structured output defined by the response schema."""

    user_text = f"""All agent outputs:
INTAKE: {json.dumps(state['intake'])}
POLICY: found={state['policy'].get('found')}, statusCheck={state['policy'].get('statusCheck')}, adjustedPayout={state['policy'].get('adjustedPayout')}, deductibleApplied={state['policy'].get('deductibleApplied')}
DOCUMENTS: consistencyScore={state['documents'].get('consistencyScore')}, redFlags={state['documents'].get('redFlags')}
ELIGIBILITY: eligible={state['eligibility'].get('eligible')}, riskScore={state['eligibility'].get('riskScore')}, recommendation={state['eligibility'].get('recommendation')}
Claim ID: {state['claimId']}
Policy Number: {policy_data.get('policy_number', '')}
Holder Name: {holder_name}"""
    
    result = await _complete("decision", system_prompt, user_text, DecisionOutput, state['claimId'])
    
    # Mock email (always false as user requested)
    decision = result.model_dump()
    decision['emailSent'] = False
    state['decision'] = decision
    return state


# ============ ORCHESTRATOR ============

class ClaimOrchestrator:
    def __init__(self, claim_id, queue):
        self.claim_id = claim_id
        self.queue = queue
        self.state = self._create_initial_state(claim_id)
    
    def _create_initial_state(self, claim_id):
        return {
            "claimId": claim_id,
            "submittedAt": datetime.now(timezone.utc).isoformat(),
            "input": {},
            "intake": {},
            "policy": {},
            "documents": {},
            "eligibility": {},
            "decision": {},
            "agentLogs": []
        }
    
    async def stream(self, data):
        """Fan out to the live queue and the durable event log."""
        await self.queue.put(data)
        try:
            await emit_event(self.claim_id, data)
        except Exception:  # noqa: BLE001 — live streaming must survive a durability hiccup
            logger.exception("event_persist_failed for claim %s", self.claim_id)
    
    async def run(self, form_data):
        self.state['input'] = form_data
        
        pipeline = [
            {"name": "INTAKE_AGENT", "fn": intake_agent, "stateKey": "intake",
             "label": "Intake & Validation", "desc": "Validating claim fields and normalizing data"},
            {"name": "POLICY_AGENT", "fn": policy_agent, "stateKey": "policy",
             "label": "Policy Verification", "desc": "Querying policy database for coverage verification"},
            {"name": "DOCUMENT_AGENT", "fn": document_agent, "stateKey": "documents",
             "label": "Document Analysis", "desc": "Analyzing claim documents for evidence and consistency"},
            {"name": "ELIGIBILITY_AGENT", "fn": eligibility_agent, "stateKey": "eligibility",
             "label": "Eligibility & Risk", "desc": "Calculating risk score and eligibility verdict"},
            {"name": "DECISION_AGENT", "fn": decision_agent, "stateKey": "decision",
             "label": "Decision & Communication", "desc": "Issuing final verdict and drafting communication"},
        ]
        
        for step in pipeline:
            start_time = time.time()
            
            # Add agent log entry
            log_entry = {
                "agent": step["name"],
                "status": "running",
                "startTime": datetime.now(timezone.utc).isoformat(),
                "endTime": None,
                "toolsCalled": [],
                "durationMs": 0
            }
            self.state['agentLogs'].append(log_entry)
            
            # Stream: agent starting
            await self.stream({
                "event": "agent_start",
                "agent": step["name"],
                "label": step["label"],
                "description": step["desc"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            try:
                self.state = await step["fn"](self.state)
                duration = int((time.time() - start_time) * 1000)
                
                # Update log
                log_entry["status"] = "done"
                log_entry["endTime"] = datetime.now(timezone.utc).isoformat()
                log_entry["durationMs"] = duration
                
                # Get tool calls for this agent
                tools = []
                for log in self.state['agentLogs']:
                    if log['agent'] == step['name']:
                        tools = log.get('toolsCalled', [])
                        break
                
                # Stream: agent complete
                await self.stream({
                    "event": "agent_complete",
                    "agent": step["name"],
                    "label": step["label"],
                    "duration": duration,
                    "output": self.state[step["stateKey"]],
                    "toolsCalled": tools
                })
                
                # Check halt conditions
                if step["name"] == "INTAKE_AGENT" and not self.state['intake'].get('valid', False):
                    await self.stream({
                        "event": "pipeline_halted",
                        "reason": "Invalid submission",
                        "data": self.state['intake']
                    })
                    break
                
                if step["name"] == "POLICY_AGENT" and not self.state['policy'].get('found', False):
                    await self.stream({
                        "event": "pipeline_halted",
                        "reason": "Policy not found",
                        "data": self.state['policy']
                    })
                    break
                    
            except Exception as err:  # noqa: BLE001 — one bad agent must not kill the stream
                duration = int((time.time() - start_time) * 1000)
                log_entry["status"] = "error"
                log_entry["endTime"] = datetime.now(timezone.utc).isoformat()
                log_entry["durationMs"] = duration
                
                logger.error(f"Agent {step['name']} error: {err}")
                await self.stream({
                    "event": "agent_error",
                    "agent": step["name"],
                    "label": step["label"],
                    "error": str(err),
                    "duration": duration
                })
        
        # Stream: pipeline complete
        await self.stream({
            "event": "pipeline_complete",
            "finalState": self.state
        })
        
        # Persist to MongoDB
        await self._save_to_database()
        
        return self.state
    
    async def _save_to_database(self):
        """Save claim to MongoDB."""
        try:
            claim_doc = {
                "id": self.state['claimId'],
                "policy_number": self.state['input'].get('policyNumber', ''),
                "claim_date": datetime.now(timezone.utc).isoformat(),
                "incident_date": self.state['intake'].get('normalizedData', {}).get('incidentDate', ''),
                "incident_type": self.state['intake'].get('normalizedData', {}).get('incidentType', self.state['input'].get('incidentType', '')),
                "claimed_amount": float(self.state['intake'].get('normalizedData', {}).get('claimedAmount', self.state['input'].get('claimedAmount', 0))),
                "status": self.state.get('decision', {}).get('verdict', 'pending'),
                "risk_score": self.state.get('eligibility', {}).get('riskScore', 0),
                "decision_reason": self.state.get('decision', {}).get('reasoning', ''),
                "agent_trace": {
                    "intake": self.state.get('intake', {}),
                    "policy": {k: v for k, v in self.state.get('policy', {}).items() if k != 'policyData'},
                    "documents": self.state.get('documents', {}),
                    "eligibility": self.state.get('eligibility', {}),
                    "decision": self.state.get('decision', {}),
                },
                "agent_logs": self.state.get('agentLogs', []),
                "holder_name": self.state.get('policy', {}).get('policyData', {}).get('holder_name', ''),
                "is_historical": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Store document text if provided
            doc_text = self.state['input'].get('documentText', '')
            if doc_text:
                await claims_col.database.claim_documents.insert_one({
                    "claim_id": self.state['claimId'],
                    "document_type": "evidence_text",
                    "content_text": doc_text,
                    "uploaded_at": datetime.now(timezone.utc).isoformat()
                })
            
            await claims_col.insert_one(claim_doc)
            logger.info(f"Claim {self.state['claimId']} saved to database")
        except Exception as e:  # noqa: BLE001 — persistence failure is logged, not raised
            logger.error(f"Error saving claim: {e}")
