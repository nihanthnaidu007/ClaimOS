"""Pydantic request/response models for every API route.

Replaces zero-validation raw-dict bodies (audit finding 6): amounts must be
positive, dates must parse, strings are length-capped before they reach Mongo
or an LLM prompt. Field names deliberately preserve the existing JSON contract
so the current frontend keeps working.
"""

from datetime import datetime
from typing import Any, Literal, Optional

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
    # Public status portal credential: shown once to the submitting party and
    # displayable in the adjuster case view. The claim doc stores its hash.
    accessCode: str = ""


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


class FraudFlagOut(BaseModel):
    """One deterministic fraud cross-check flag (badge on case + queue)."""

    code: str
    severity: str = "low"  # high | medium | low
    detail: str = ""
    evidence: dict[str, Any] = {}


class ReviewFlag(BaseModel):
    """One adjuster review flag (spec F12 flag-for-review) on a claim."""

    reason: str
    flagged_by: str = ""
    flagged_at: str = ""


# Settlement methods are a closed vocabulary: reports group on this field.
SETTLEMENT_METHODS = ("bank_transfer", "cheque", "upi", "other")


class SettlementCreate(BaseModel):
    """Record-only settlement facts. No money moves here by design (spec:
    real payment rails are out of scope); this is the system of record."""

    amount: float = Field(ge=0, le=CLAIMED_AMOUNT_MAX)
    method: Literal["bank_transfer", "cheque", "upi", "other"]
    reference: str = Field(default="", max_length=120)


class SettlementRecordOut(BaseModel):
    amount: float
    method: str
    reference: str = ""
    settled_at: str
    recorded_by: str


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
    # Fraud cross-check outputs (fraud agent): flags drive the case badge;
    # the incident fingerprint backs future duplicate detection.
    fraud_flags: list[FraudFlagOut] = []
    incident_fingerprint: str = ""
    # Adjuster review flags (spec F12): appended by flag-for-review bulk
    # actions; absent on seeded/legacy claim docs.
    flags: list[ReviewFlag] = []
    # Assignment (spec F10 field name): set by manual reassign or bulk reassign.
    assignee_id: str = ""
    assignee_email: str = ""
    # Customer-provided contact for milestone notifications; adjuster-visible.
    contact_email: str = ""
    # Public status portal credential (adjuster case view displays it; the
    # public lookup matches only its SHA-256 hash).
    access_code: str | None = None
    # Set when the claim failed or was escalated by the pipeline worker.
    failure_reason: str | None = None
    escalation_reason: str | None = None
    # Per-claim LLM usage rollup attached by the worker at finalize.
    usage: dict[str, Any] | None = None
    # Record-only settlement facts (no payment rails; settlement PR).
    settlement: Optional[SettlementRecordOut] = None


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
    # Queue badge: non-empty when the fraud cross-check flagged the claim.
    fraud_flags: list[FraudFlagOut] = []
    # Adjuster review flags (spec F12 flag-for-review bulk action); absent on
    # seeded/legacy docs, coerced to [] like fraud_flags.
    flags: list[ReviewFlag] = []
    # Current assignee (spec F10 field name); empty until something assigns.
    assignee_id: str = ""


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


# ---- Ops analytics (Tier 3, AC-9) ----

class CycleTimeStats(BaseModel):
    p50Seconds: float
    p95Seconds: float
    decided: int


class StpStats(BaseModel):
    decided: int
    autoApproved: int
    escalated: int
    rate: float


class FraudFlagStats(BaseModel):
    totalClaims: int
    flaggedClaims: int
    rate: float


class DecisionCount(BaseModel):
    status: str
    count: int


class SlaSeverityStats(BaseModel):
    severity: str
    slaHours: float
    decided: int
    breaches: int
    breachRate: float


class OpsAnalyticsResponse(BaseModel):
    cycleTime: CycleTimeStats
    stp: StpStats
    fraud: FraudFlagStats
    decisions: list[DecisionCount]
    sla: dict[str, Any]


# ---- Document uploads (Tier 3) ----

class UploadedDocumentResponse(BaseModel):
    id: str
    claim_id: str
    file_name: str
    content_type: str
    size_bytes: int
    storage_key: str
    uploaded_at: str
    uploaded_by: str
    sha256: str


# ---- Auth (auth backend PR) ----

UserRole = Literal["adjuster", "customer"]

# Strict email shape for auth flows (no empty-string variant — that is only
# valid for optional contact emails on claims).
_LOGIN_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class UserRecord(BaseModel):
    """A stored user. `password_hash` is bcrypt; never exposed by any route."""

    id: str
    email: str
    password_hash: str
    role: UserRole
    created_at: str = ""


class PublicUser(BaseModel):
    """The user fields safe to return to clients."""

    id: str
    email: str
    role: UserRole
    createdAt: str


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254, pattern=_LOGIN_EMAIL_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    role: UserRole
    inviteCode: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254, pattern=_LOGIN_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class AuthTokensResponse(BaseModel):
    """Login/refresh response: short-TTL access token in the body (client
    holds it in memory) — the refresh token travels only as an httpOnly cookie."""

    accessToken: str
    tokenType: str = "bearer"
    expiresInSeconds: int
    user: PublicUser


# ---- Adjuster workbench (workbench PR) ----

SEVERITY_VALUES = Literal["low", "elevated"]
SLA_STATES = Literal["ok", "at_risk", "breached"]
DECISION_VALUES = Literal["approved", "rejected"]

OVERRIDE_REASON_MAX = 2000


class WorkbenchSLA(BaseModel):
    """SLA aging for one queue row, computed from the claim's stored age."""

    targetHours: float
    hoursElapsed: float
    hoursRemaining: float
    breached: bool
    state: SLA_STATES


class WorkbenchQueueRow(BaseModel):
    id: str
    policy_number: str = ""
    holder_name: str = ""
    incident_type: str = ""
    claimed_amount: float = 0.0
    status: str
    risk_score: float = 0.0
    created_at: str = ""
    severity: SEVERITY_VALUES
    sla: WorkbenchSLA
    escalation_reason: str | None = None
    failure_reason: str | None = None
    # Queue badge: non-empty when the fraud cross-check flagged the claim.
    fraud_flags: list[FraudFlagOut] = []


class WorkbenchQueueResponse(BaseModel):
    rows: list[WorkbenchQueueRow]
    generatedAt: str


class CaseStageSummary(BaseModel):
    agent: str
    label: str
    reached: bool
    status: str
    durationMs: int | None = None
    reasoning: str | None = None


class CaseSummaryResponse(BaseModel):
    """Deterministic case summary assembled from stored agent traces (no LLM)."""

    claimId: str
    holderName: str = ""
    policyNumber: str = ""
    incidentType: str = ""
    incidentDate: str = ""
    claimedAmount: float = 0.0
    status: str
    severity: SEVERITY_VALUES
    riskScore: float = 0.0
    # Case badge: non-empty when the fraud cross-check flagged the claim.
    fraudFlags: list[FraudFlagOut] = []
    recommendation: str | None = None
    confidence: float | None = None
    eligibility: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    documents: dict[str, Any] = {}
    decision: dict[str, Any] = {}
    intakeValid: bool | None = None
    stages: list[CaseStageSummary] = []
    sla: WorkbenchSLA | None = None
    escalationReason: str | None = None
    failureReason: str | None = None
    override: dict[str, Any] | None = None
    source: str = "stored agent traces"


class AuditEntry(BaseModel):
    """One append-only audit_log row: who did what to which claim, and why."""

    id: str
    claim_id: str
    actor: str
    actor_email: str = ""
    action: str
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    reason: str
    at: str


class SettlementResponse(BaseModel):
    claimId: str
    status: str
    settlement: SettlementRecordOut
    auditEntry: AuditEntry


class OverrideRequest(BaseModel):
    """Adjuster decision on a claim. The reason is what makes the override
    legitimate — a missing or blank one is rejected with 422 (spec AC-7)."""

    decision: DECISION_VALUES = "approved"
    reason: str = Field(min_length=1, max_length=OVERRIDE_REASON_MAX)
    payoutAmount: float | None = Field(default=None, ge=0, le=CLAIMED_AMOUNT_MAX)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required to override")
        return value


class OverrideResponse(BaseModel):
    claimId: str
    status: str
    auditEntry: AuditEntry


# ---- Saved views + bulk actions (workbench spec F12) ----

VIEW_NAME_MAX = 80
BULK_CLAIM_IDS_MAX = 100


class SavedViewCreate(BaseModel):
    """Save the current queue filters under a name (owner-private)."""

    name: str = Field(min_length=1, max_length=VIEW_NAME_MAX)
    filters: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("View name cannot be blank")
        return value.strip()


class SavedViewOut(BaseModel):
    """One saved view. `filters` is the parsed preset ready to apply."""

    id: str
    name: str
    filters: dict[str, Any] = {}
    owner_id: str = ""
    createdAt: str = ""


class SavedViewApplyResponse(BaseModel):
    """Applying a view runs the queue machinery over its stored filters."""

    view: SavedViewOut
    rows: list[WorkbenchQueueRow]
    generatedAt: str


class BulkActionRequest(BaseModel):
    """Bulk queue action (spec F12).

    Applied claim-by-claim on the server: every touched claim gets its own
    audit entry, and per-claim failures are reported, never dropped.
    """

    action: Literal["reassign", "flag"]
    claimIds: list[str] = Field(min_length=1, max_length=BULK_CLAIM_IDS_MAX)
    # Reassign target: an adjuster's user id or email. Required for reassign,
    # validated against the users store before anything is written.
    target: str = Field(default="", max_length=254)
    reason: str = Field(min_length=1, max_length=OVERRIDE_REASON_MAX)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required for a bulk action")
        return value

    @field_validator("claimIds")
    @classmethod
    def _claim_ids_not_blank(cls, value: list[str]) -> list[str]:
        if any(not claim_id.strip() for claim_id in value):
            raise ValueError("Claim ids cannot be blank")
        return [claim_id.strip() for claim_id in value]


class BulkActionResultItem(BaseModel):
    """Per-claim outcome of one bulk action — failures surface here."""

    claimId: str
    status: Literal["updated", "failed"]
    auditId: str | None = None
    detail: str | None = None


class BulkActionResponse(BaseModel):
    action: str
    results: list[BulkActionResultItem]
    updated: int
    failed: int


# ---- Public status portal (customer communications PR) ----

_CLAIM_NUMBER_PATTERN = r"^CLM-[0-9]{8}-[0-9]{1,6}$|^CLM-HIST-[0-9]{1,6}$"


class StatusAccessRequest(BaseModel):
    """Claim number + access code — the public portal's only credential pair."""

    claimNumber: str = Field(min_length=4, max_length=40, pattern=_CLAIM_NUMBER_PATTERN)
    accessCode: str = Field(min_length=16, max_length=200)


class StatusLookupRequest(StatusAccessRequest):
    pass


class StatusLetterRequest(StatusAccessRequest):
    pass


class StatusMilestone(BaseModel):
    key: str
    label: str
    at: Optional[str] = None
    done: bool = False


class StatusLookupResponse(BaseModel):
    """Masked portal payload: first name + claim status are the only identity
    data; amounts, contact details, and policy numbers never appear."""

    claimNumber: str
    firstName: str = ""
    status: str
    statusLabel: str
    currentStage: Optional[str] = None
    incidentType: str = ""
    decisionOutcome: Optional[str] = None
    decisionReady: bool = False
    pdfAvailable: bool = False
    milestones: list[StatusMilestone] = []
    # F2: plain-language next steps + an honest ETA. expectedResolution is
    # omitted from the response body entirely when the projection leaves it
    # unset (the lookup route sets response_model_exclude_unset) — absent
    # beats an empty string or a fabricated date.
    nextSteps: list[str] = []
    expectedResolution: Optional[str] = None


class StatusLetterResponse(BaseModel):
    pdf: str
    claimId: str


# ---- In-app notification center (customer communications PR) ----


class NotificationItem(BaseModel):
    id: str
    claimId: str
    milestone: str
    title: str
    body: str
    read: bool = False
    createdAt: str = ""


class NotificationListResponse(BaseModel):
    notifications: list[NotificationItem] = []
    unreadCount: int = 0


class MarkReadRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class MarkReadResponse(BaseModel):
    markedRead: int
