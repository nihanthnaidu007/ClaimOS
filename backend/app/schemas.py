"""Pydantic request/response models for every API route.

Replaces zero-validation raw-dict bodies (audit finding 6): amounts must be
positive, dates must parse, strings are length-capped before they reach Mongo
or an LLM prompt. Field names deliberately preserve the existing JSON contract
so the current frontend keeps working.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# Accepts empty string (frontend sends it for optional emails) or a minimal
# name@domain.tld shape. Full deliverability is out of scope here.
_EMAIL_PATTERN = r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$"

CLAIMED_AMOUNT_MAX = 5_000_000
DESCRIPTION_MAX = 5000
DOCUMENT_TEXT_MAX = 20_000


class ClaimSubmission(BaseModel):
    policyNumber: str = Field(min_length=3, max_length=40)
    holderName: str = Field(default="", max_length=120)
    incidentDate: str
    incidentType: str = Field(min_length=2, max_length=40)
    claimedAmount: float = Field(gt=0, le=CLAIMED_AMOUNT_MAX)
    description: str = Field(min_length=20, max_length=DESCRIPTION_MAX)
    contactEmail: str = Field(default="", max_length=254, pattern=_EMAIL_PATTERN)
    documentText: str = Field(default="", max_length=DOCUMENT_TEXT_MAX)

    @field_validator("incidentDate")
    @classmethod
    def _incident_date_is_real_date(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%d")  # raises ValueError -> 422
        return value


class SubmitClaimResponse(BaseModel):
    claimId: str
    message: str


class PolicyRecord(BaseModel):
    id: str
    policy_number: str
    holder_name: str = ""
    holder_email: str = ""
    holder_phone: str = ""
    policy_type: str
    status: str
    coverage_limit: float
    deductible: float
    monthly_premium: float = 0.0
    start_date: str = ""
    end_date: str = ""
    covered_events: list[str] = []


class ClaimRecord(BaseModel):
    id: str
    policy_number: str = ""
    claim_date: str = ""
    incident_date: str = ""
    incident_type: str = ""
    claimed_amount: float = 0.0
    status: str
    risk_score: float = 0.0
    decision_reason: str = ""
    agent_trace: dict[str, Any] = {}
    agent_logs: list[dict[str, Any]] = []
    holder_name: str = ""
    is_historical: bool = False
    created_at: str = ""


class ClaimPdfResponse(BaseModel):
    pdf: str
    claimId: str


class RecentClaimRecord(BaseModel):
    id: str
    policy_number: str = ""
    status: str
    claimed_amount: float = 0.0
    risk_score: float = 0.0
    holder_name: Optional[str] = None
    incident_type: str = ""
    created_at: str = ""


class DashboardStatsResponse(BaseModel):
    totalClaims: int
    approved: int
    rejected: int
    underReview: int
    pending: int
    totalPayout: float
    avgRiskScore: float
    activePolicies: int
    recentClaims: list[RecentClaimRecord]


class RootStatusResponse(BaseModel):
    status: str
    service: str
    agents: int


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str
