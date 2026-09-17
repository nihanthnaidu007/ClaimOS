"""Ops analytics: five server-side metric groups over the claim timeline.

Spec Tier 3 "Ops analytics" (AC-9): cycle-time percentiles, STP auto-approval
rate, fraud-flag rate, decision distribution, and SLA breaches by severity —
computed server-side from the durable claim/run timeline and rendered in the
ops dashboard.

Determinism rule (spec AC-4 applies to money AND metric math): Mongo supplies
the rows; Python supplies the arithmetic. Percentile interpolation and rate
math live in pure functions here so seeded-fixture tests assert exact values
and identical inputs yield identical outputs across runs.

Cycle-time source: a claim's runs are its durable timeline. Submitted = the
earliest run enqueue (created_at); decided = the latest finished_at among runs
that ended in a decided terminal state (auto_approved or escalated). Failed,
halted, and still-open runs carry no decision and are excluded. Historical
seed rows have no run timeline and therefore contribute to decision
distribution and fraud rate but never to cycle time or SLA math.
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from app.stp import assess_claim_severity
from pipeline import RUN_AUTO_APPROVED, RUN_ESCALATED

# Runs that represent a decision (cycle-time denominator + STP gate outcome).
DECIDED_RUN_STATUSES = frozenset({RUN_AUTO_APPROVED, RUN_ESCALATED})

LOW = "low"
ELEVATED = "elevated"


def parse_timestamp(value: object) -> datetime | None:
    """Parse the ISO timestamps the pipeline persists; naive values are UTC.

    Historical seed rows store plain dates ("2024-06-15"); run checkpoints
    store timezone-aware ISO strings. Both parse; None means "no timeline".
    """
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile over a pre-sorted ascending list.

    Same method as numpy's default ('linear'): rank = pct * (n - 1); when the
    rank is integral the value is returned exactly, otherwise neighbors are
    blended. Empty input is 0.0 so empty dashboards render, not crash.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    rank = pct * (len(values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(values[int(rank)])
    fraction = rank - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def group_runs_by_claim(runs: list[dict]) -> dict[str, list[dict]]:
    """claim_id -> that claim's run documents (any attempt)."""
    grouped: dict[str, list[dict]] = {}
    for run in runs:
        grouped.setdefault(str(run.get("claim_id", "")), []).append(run)
    return grouped


def claim_cycle_seconds(runs: list[dict]) -> float | None:
    """Submitted -> decided duration for one claim's runs, or None.

    Submitted = earliest run created_at across attempts; decided = latest
    finished_at among runs that reached a decided terminal state.
    """
    submitted_candidates = [
        t for t in (parse_timestamp(run.get("created_at")) for run in runs) if t
    ]
    decided_candidates = [
        parse_timestamp(run.get("finished_at"))
        for run in runs
        if run.get("status") in DECIDED_RUN_STATUSES
    ]
    decided_candidates = [t for t in decided_candidates if t]
    if not submitted_candidates or not decided_candidates:
        return None
    return (max(decided_candidates) - min(submitted_candidates)).total_seconds()


@dataclass(frozen=True)
class SlaBucket:
    """One severity's SLA outcome. breach_rate is 0.0 when nothing decided."""

    severity: str
    sla_hours: float
    decided: int
    breaches: int

    @property
    def breach_rate(self) -> float:
        return self.breaches / self.decided if self.decided else 0.0


def build_ops_analytics(
    claims: list[dict],
    runs: list[dict],
    *,
    low_types: set[str],
    low_amount_threshold: float,
    sla_hours_by_severity: dict[str, float],
) -> dict:
    """Assemble the five metric groups. Pure: exact, repeatable, unit-testable."""
    # ---- Cycle time percentiles ----
    runs_by_claim = group_runs_by_claim(runs)
    cycles = [
        seconds
        for claim_id in runs_by_claim
        if (seconds := claim_cycle_seconds(runs_by_claim[claim_id])) is not None
    ]
    cycles.sort()
    cycle_time = {
        "p50Seconds": percentile(cycles, 0.50),
        "p95Seconds": percentile(cycles, 0.95),
        "decided": len(cycles),
    }

    # ---- STP rate ----
    auto_approved = sum(1 for run in runs if run.get("status") == RUN_AUTO_APPROVED)
    escalated = sum(1 for run in runs if run.get("status") == RUN_ESCALATED)
    stp_decided = auto_approved + escalated
    stp = {
        "decided": stp_decided,
        "autoApproved": auto_approved,
        "escalated": escalated,
        "rate": auto_approved / stp_decided if stp_decided else 0.0,
    }

    # ---- Fraud-flag rate (claims carrying at least one cross-check flag) ----
    flagged = sum(
        1
        for claim in claims
        if ((claim.get("fraud_check") or {}).get("flags") or [])
    )
    fraud = {
        "totalClaims": len(claims),
        "flaggedClaims": flagged,
        "rate": flagged / len(claims) if claims else 0.0,
    }

    # ---- Decision distribution (the whole book, historical rows included) ----
    status_counts = Counter(str(claim.get("status") or "unknown") for claim in claims)
    decisions = [
        {"status": status, "count": count}
        for status, count in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # ---- SLA breaches by severity ----
    breach_counts = {severity: 0 for severity in sla_hours_by_severity}
    decided_counts = {severity: 0 for severity in sla_hours_by_severity}
    for claim_id, claim_runs in runs_by_claim.items():
        cycle = claim_cycle_seconds(claim_runs)
        if cycle is None:
            continue
        claim_row = next(
            (c for c in claims if str(c.get("id", "")) == claim_id), None
        )
        if claim_row is None:
            continue
        severity = assess_claim_severity(
            claimed_amount=float(claim_row.get("claimed_amount", 0) or 0),
            incident_type=str(claim_row.get("incident_type", "") or ""),
            low_amount_threshold=low_amount_threshold,
            low_types=low_types,
        )
        if severity not in sla_hours_by_severity:
            continue
        decided_counts[severity] += 1
        if cycle > sla_hours_by_severity[severity] * 3600:
            breach_counts[severity] += 1

    by_severity = [
        SlaBucket(
            severity=severity,
            sla_hours=sla_hours_by_severity[severity],
            decided=decided_counts[severity],
            breaches=breach_counts[severity],
        )
        for severity in sorted(sla_hours_by_severity)
    ]
    sla = {
        "bySeverity": [
            {
                "severity": bucket.severity,
                "slaHours": bucket.sla_hours,
                "decided": bucket.decided,
                "breaches": bucket.breaches,
                "breachRate": bucket.breach_rate,
            }
            for bucket in by_severity
        ]
    }

    return {
        "cycleTime": cycle_time,
        "stp": stp,
        "fraud": fraud,
        "decisions": decisions,
        "sla": sla,
    }


async def collect_ops_analytics() -> dict:
    """Fetch the timeline rows and build the ops analytics payload.

    Collections are read through the `database` module at call time so test
    fixtures can swap the bindings (the same pattern pipeline.py uses).
    """
    import database

    claims = await database.claims_col.find({}, {"_id": 0}).to_list(None)
    runs = await database.claim_runs_col.find({}, {"_id": 0}).to_list(None)
    from app.config import settings

    return build_ops_analytics(
        claims,
        runs,
        low_types={
            part.strip().lower()
            for part in settings.stp_low_severity_types.split(",")
            if part.strip()
        },
        low_amount_threshold=settings.stp_low_severity_amount,
        sla_hours_by_severity={
            LOW: float(settings.sla_low_hours),
            ELEVATED: float(settings.sla_elevated_hours),
        },
    )
