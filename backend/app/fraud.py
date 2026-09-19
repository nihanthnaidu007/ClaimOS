"""Deterministic fraud cross-check rules (spec Tier 3).

Pure functions only: given claim facts and prior claim rows, return flags.
The LLM similarity judgment lives in the fraud agent (agents.py) — this
module never calls a model, so the rules stay testable without keys and the
risk-score bump math stays in app/rating.py.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"

# Claimed-to-limit ratio at or above this is flagged. 1.0 means "claiming
# at or beyond the full coverage limit"; anything under is normal behavior
# (over-limit payouts are already handled by the rating math).
AMOUNT_RATIO_FLAG_THRESHOLD = 1.0

CODE_DUPLICATE_INCIDENT = "DUPLICATE_INCIDENT_FINGERPRINT"
CODE_FUTURE_INCIDENT_DATE = "FUTURE_INCIDENT_DATE"
CODE_INCIDENT_BEFORE_POLICY_START = "INCIDENT_BEFORE_POLICY_START"
CODE_CLAIMED_AMOUNT_RATIO = "CLAIMED_AMOUNT_OVER_LIMIT_RATIO"


@dataclass(frozen=True)
class FraudFlag:
    """One deterministic fraud signal, with evidence cited from the record."""

    code: str
    severity: str  # high | medium | low
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def incident_fingerprint(
    policy_number: str, incident_date: str, incident_type: str
) -> str:
    """Stable duplicate-incident fingerprint.

    Same policy + same incident date + same incident type = same incident.
    Normalized (case/whitespace) so formatting variance cannot dodge the
    match; works identically for seeded rows and pipeline-saved claims.
    """
    raw = "|".join(
        (part or "").strip().lower()
        for part in (policy_number, incident_date, incident_type)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_iso_date(value: str | None) -> date | None:
    """Tolerant ISO date parse ('2026-09-01' or a full ISO timestamp)."""
    if not value:
        return None
    head = str(value).split("T")[0].split(" ")[0]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def prior_claim_fingerprints(prior_rows: list[dict]) -> dict[str, str]:
    """Fingerprint map {fingerprint: claim_id} for prior claims.

    Uses the stored incident_fingerprint when present (pipeline-saved rows),
    recomputes from fields otherwise (seeded rows).
    """
    fingerprints: dict[str, str] = {}
    for row in prior_rows:
        fp = row.get("incident_fingerprint") or incident_fingerprint(
            str(row.get("policy_number", "")),
            str(row.get("incident_date", "")),
            str(row.get("incident_type", "")),
        )
        if fp:
            fingerprints[fp] = str(row.get("id", ""))
    return fingerprints


def detect_fraud_flags(
    *,
    claimed_amount: float,
    coverage_limit: float,
    incident_date: str | None,
    policy_start_date: str | None,
    fingerprint: str,
    prior_incident_fingerprints: dict[str, str],
    today: date,
) -> list[FraudFlag]:
    """Run the deterministic rules; each flag cites its evidence.

    `today` is injected so the rules are deterministic under test.
    """
    flags: list[FraudFlag] = []

    duplicate_of = prior_incident_fingerprints.get(fingerprint)
    if duplicate_of:
        flags.append(
            FraudFlag(
                code=CODE_DUPLICATE_INCIDENT,
                severity=SEVERITY_HIGH,
                detail=f"Duplicate incident fingerprint — matches prior claim {duplicate_of}",
                evidence={
                    "duplicate_of_claim_id": duplicate_of,
                    "fingerprint": fingerprint,
                },
            )
        )

    incident = _parse_iso_date(incident_date)
    if incident is not None:
        if incident > today:
            flags.append(
                FraudFlag(
                    code=CODE_FUTURE_INCIDENT_DATE,
                    severity=SEVERITY_HIGH,
                    detail=f"Incident date {incident.isoformat()} is in the future",
                    evidence={
                        "incident_date": incident.isoformat(),
                        "checked_at": today.isoformat(),
                    },
                )
            )
        policy_start = _parse_iso_date(policy_start_date)
        if policy_start is not None and incident < policy_start:
            flags.append(
                FraudFlag(
                    code=CODE_INCIDENT_BEFORE_POLICY_START,
                    severity=SEVERITY_MEDIUM,
                    detail=(
                        f"Incident date {incident.isoformat()} precedes policy "
                        f"start {policy_start.isoformat()}"
                    ),
                    evidence={
                        "incident_date": incident.isoformat(),
                        "policy_start_date": policy_start.isoformat(),
                    },
                )
            )

    if coverage_limit > 0 and claimed_amount > 0:
        ratio = claimed_amount / coverage_limit
        if ratio >= AMOUNT_RATIO_FLAG_THRESHOLD:
            flags.append(
                FraudFlag(
                    code=CODE_CLAIMED_AMOUNT_RATIO,
                    severity=SEVERITY_MEDIUM,
                    detail=(
                        f"Claimed amount is {ratio:.2f}x the coverage limit"
                        + (" (over limit)" if ratio > 1.0 else "")
                    ),
                    evidence={
                        "claimed_amount": claimed_amount,
                        "coverage_limit": coverage_limit,
                        "ratio": round(ratio, 4),
                    },
                )
            )

    return flags
