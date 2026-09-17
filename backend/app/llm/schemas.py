"""Typed output schemas for the five pipeline agents.

Implements the Agent Prompt Quality Review's rewrite proposals (art_rqtooc3G
§6): flat models inside the structured-output subset (no recursion, no numeric
constraints — ranges are enforced post-parse in code), explicit uncertainty
(`unknown` statuses, `inputGaps`), and citation fields for the glass-box
evidence pack.

Field names are camelCase on purpose: the stored agent trace and SSE payloads
keep the exact shape the pipeline emitted before this change, which is the JSON
contract the dashboard, claim history, and evidence pack consume.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ---- Agent 1: Intake -------------------------------------------------------

ValidationFlagCode = Literal[
    "future_date",
    "high_amount",
    "thin_description",
    "internal_inconsistency",
    "normalization_applied",
]


class ValidationFlag(BaseModel):
    code: ValidationFlagCode
    detail: str = ""


class NormalizedClaim(BaseModel):
    policyNumber: str = ""
    # ISO YYYY-MM-DD — the structured-output subset has no date type.
    incidentDate: str = ""
    incidentType: str = ""
    claimedAmount: float = 0.0
    description: str = ""


class IntakeResult(BaseModel):
    valid: bool
    normalizedData: NormalizedClaim
    missingFields: list[str] = Field(default_factory=list)
    flags: list[ValidationFlag] = Field(default_factory=list)
    summary: str = ""


# ---- Agent 2: Policy -------------------------------------------------------

PolicyStatus = Literal["active", "expired", "suspended", "unknown"]
CoverageVerdict = Literal["covered", "not_covered", "unknown"]


class PolicyVerification(BaseModel):
    """Judgment only: the orchestrator computes withinLimits, adjustedPayout,
    and deductibleApplied from the policy record and merges them into the
    stored policy result."""

    status: PolicyStatus = "unknown"
    statusDetail: str = ""
    coverage: CoverageVerdict = "unknown"
    coverageDetail: str = ""
    appearsOverLimit: bool = False
    claimFrequencyFlag: bool = False
    priorClaims12mo: int = 0
    citedFields: list[str] = Field(default_factory=list)
    summary: str = ""


# ---- Agent 3: Document -----------------------------------------------------

ConsistencyCategory = Literal[
    "consistent", "partially_consistent", "contradicts", "no_documents"
]


class AmountFact(BaseModel):
    value: float
    context: str = ""


class ExtractedFacts(BaseModel):
    # ISO where parseable, else the verbatim string found in the text.
    datesFound: list[str] = Field(default_factory=list)
    locationMentioned: str = ""
    partiesInvolved: str = ""
    damageDescribed: str = ""
    amountsMentioned: list[AmountFact] = Field(default_factory=list)


RedFlagCode = Literal[
    "date_contradiction", "amount_mismatch", "template_text", "damage_type_mismatch"
]


class RedFlag(BaseModel):
    code: RedFlagCode
    description: str = ""
    # Verbatim quote from the document text — no quote, no red flag.
    excerpt: str = ""


class EvidenceItem(BaseModel):
    excerpt: str
    note: str = ""


class DocumentAnalysis(BaseModel):
    extracted: ExtractedFacts
    consistency: ConsistencyCategory = "no_documents"
    redFlags: list[RedFlag] = Field(default_factory=list)
    supportingEvidence: list[EvidenceItem] = Field(default_factory=list)
    summary: str = ""


# ---- Agent 4: Eligibility --------------------------------------------------

Recommendation = Literal["auto_approve", "escalate", "auto_reject"]


class RiskFactor(BaseModel):
    code: str
    points: int = 0
    # Upstream field the factor is based on, e.g. "policy.status=expired".
    sourceRef: str = ""
    basis: str = ""


class EligibilityResult(BaseModel):
    """The model enumerates factors with the prompt's schedule; the orchestrator
    recomputes the total in code (app/rating.py) and overrides eligible and
    recommendation where they disagree with the rules."""

    eligible: bool
    riskFactors: list[RiskFactor] = Field(default_factory=list)
    fraudIndicators: list[str] = Field(default_factory=list)
    recommendation: Recommendation = "escalate"
    inputGaps: list[str] = Field(default_factory=list)
    summary: str = ""


# ---- Agent 5: Decision -----------------------------------------------------


class DecisionCitation(BaseModel):
    fact: str
    sourceRef: str = ""
    customerFriendlyExplanation: str = ""


class DecisionResult(BaseModel):
    verdict: Literal["approved", "rejected", "under_review"]
    payoutAmount: float = 0.0
    letterSubject: str = ""
    letterBody: str = ""
    nextSteps: list[str] = Field(default_factory=list)
    citations: list[DecisionCitation] = Field(default_factory=list)
    summary: str = ""
    # Calibrated self-reported confidence (0.0-1.0); feeds the STP gate, which
    # auto-finalizes only claims at or above the configured threshold.
    confidence: float = Field(default=0.0, ge=0, le=1)
