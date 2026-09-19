"""Ops analytics tests — spec AC-9: aggregations assert exact values on seeded fixtures.

The math is pure (app/analytics.py): fixtures here are the seeded timeline
rows, and every assertion is an exact literal — same inputs must yield the
same numbers across runs, so golden values are copy-paste stable.
"""

import pytest

from app.analytics import (
    claim_cycle_seconds,
    group_runs_by_claim,
    parse_timestamp,
    percentile,
)

pytestmark = pytest.mark.asyncio

# Determinism knobs mirrored from settings (the tests pin the contract, not
# the env): severity derivation must match the STP gate's rules exactly.
LOW_TYPES = {"theft", "weather_damage", "vandalism"}
LOW_AMOUNT = 10_000.0
SLA_HOURS = {"elevated": 24.0, "low": 48.0}


def _run(claim_id, created_at, status="auto_approved", finished_at=None, attempt=1):
    return {
        "claim_id": claim_id,
        "attempt": attempt,
        "status": status,
        "created_at": created_at,
        "finished_at": finished_at,
    }


# ============ percentile (pure interpolation) ============


class TestPercentile:
    def test_exact_value_at_integral_rank(self):
        # rank = 0.5 * 3 = 1.5 -> blend of 2 and 3 = 2.5, exactly representable.
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.5

    def test_exact_value_on_single_element(self):
        assert percentile([42.5], 0.95) == 42.5

    def test_empty_is_zero(self):
        assert percentile([], 0.95) == 0.0

    def test_integral_rank_returns_element_exactly(self):
        # 21 values: rank = 0.95 * 20 = 19 (integral) -> the 20th value.
        values = [float(i) for i in range(21)]
        assert percentile(values, 0.95) == 19.0

    def test_interpolated_rank_is_linear(self):
        # rank = 0.5 * 1 = 0.5 -> midpoint, exactly representable.
        assert percentile([10.0, 20.0], 0.50) == 15.0


# ============ timestamps and cycle time ============


class TestParseTimestamp:
    def test_naive_and_aware_and_z_suffix_agree(self):
        aware = parse_timestamp("2026-09-01T08:00:00+00:00")
        z_suffix = parse_timestamp("2026-09-01T08:00:00Z")
        naive = parse_timestamp("2026-09-01T08:00:00")
        assert aware == z_suffix == naive

    def test_plain_seed_date_parses_as_utc_midnight(self):
        parsed = parse_timestamp("2024-06-15")
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == (2024, 6, 15)

    def test_garbage_is_none(self):
        assert parse_timestamp("not-a-date") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None


class TestClaimCycleSeconds:
    def test_two_day_cycle(self):
        runs = [
            _run("C1", "2026-09-01T08:00:00+00:00", finished_at="2026-09-03T08:00:00+00:00")
        ]
        assert claim_cycle_seconds(runs) == 2 * 24 * 3600.0

    def test_undecided_run_has_no_cycle(self):
        runs = [_run("C1", "2026-09-01T08:00:00+00:00", status="failed",
                     finished_at="2026-09-02T08:00:00+00:00")]
        assert claim_cycle_seconds(runs) is None

    def test_resume_counts_from_first_attempt(self):
        runs = [
            _run("C1", "2026-09-01T08:00:00+00:00", status="running", attempt=1),
            _run("C1", "2026-09-02T09:00:00+00:00", attempt=2,
                 finished_at="2026-09-03T09:00:00+00:00"),
        ]
        assert claim_cycle_seconds(runs) == 2 * 24 * 3600.0 + 3600.0


# ============ the AC-9 seeded fixture ============


class TestBuildOpsAnalytics:
    """One seeded fixture: 5 claims, 4 runs (3 decided), 1 flagged, 1 SLA breach."""

    @pytest.fixture
    def seeded(self):
        # Claims (the book; historical rows have no run timeline).
        claims = [
            {"id": "CLM-A", "status": "auto_approved", "incident_type": "theft",
             "claimed_amount": 1200.0, "fraud_check": {}},
            {"id": "CLM-B", "status": "escalated", "incident_type": "fire",
             "claimed_amount": 45000.0, "fraud_check": {}},
            {"id": "CLM-C", "status": "auto_approved", "incident_type": "vandalism",
             "claimed_amount": 900.0, "fraud_check": {"flags": [{"rule": "x"}]}},
            {"id": "CLM-D", "status": "rejected", "incident_type": "theft",
             "claimed_amount": 500.0, "fraud_check": {}},
            {"id": "CLM-HIST-1", "status": "approved", "incident_type": "theft",
             "claimed_amount": 1200.0, "fraud_check": {}},
        ]
        runs = [
            _run("CLM-A", "2026-09-01T08:00:00+00:00",
                 finished_at="2026-09-01T09:00:00+00:00"),  # 1h
            _run("CLM-B", "2026-09-01T08:00:00+00:00", status="escalated",
                 finished_at="2026-09-03T08:00:00+00:00"),  # 48h, elevated -> breach
            _run("CLM-C", "2026-09-01T08:00:00+00:00",
                 finished_at="2026-09-02T14:00:00+00:00"),  # 30h, low -> within
            _run("CLM-D", "2026-09-02T08:00:00+00:00", status="failed",
                 finished_at="2026-09-02T09:00:00+00:00"),  # not decided
        ]
        return claims, runs

    def test_exact_payload(self, seeded):
        claims, runs = seeded
        from app.analytics import build_ops_analytics

        result = build_ops_analytics(
            claims, runs, low_types=LOW_TYPES,
            low_amount_threshold=LOW_AMOUNT, sla_hours_by_severity=SLA_HOURS,
        )

        # Cycle time: decided cycles are A=3600s, B=172800s, C=108000s.
        # Sorted [3600, 108000, 172800]: p50 rank=1 -> 108000; p95 rank=1.9
        # -> 108000 + 0.9*64800 = 166320.
        assert result["cycleTime"] == {
            "p50Seconds": 108000.0, "p95Seconds": 166320.0, "decided": 3,
        }
        # STP: 2 auto_approved / 3 decided.
        assert result["stp"] == {
            "decided": 3, "autoApproved": 2, "escalated": 1,
            "rate": 2 / 3,
        }
        # Fraud: 1 flagged of 5 claims.
        assert result["fraud"] == {
            "totalClaims": 5, "flaggedClaims": 1, "rate": 0.2,
        }
        # Decisions: historical rows included; ties break alphabetically.
        assert result["decisions"] == [
            {"status": "auto_approved", "count": 2},
            {"status": "approved", "count": 1},
            {"status": "escalated", "count": 1},
            {"status": "rejected", "count": 1},
        ]
        # SLA: low decided=2 (A, C) 0 breaches; elevated decided=1 (B), 1 breach
        # (48h > 24h). Severity follows the STP derivation rules exactly.
        assert result["sla"] == {
            "bySeverity": [
                {"severity": "elevated", "slaHours": 24.0, "decided": 1,
                 "breaches": 1, "breachRate": 1.0},
                {"severity": "low", "slaHours": 48.0, "decided": 2,
                 "breaches": 0, "breachRate": 0.0},
            ]
        }
        # Workload (spec F10): the only open (reviewable-status) claim is
        # CLM-B (escalated) and no adjusters were supplied, so the roster is
        # empty and the open claim counts as unassigned.
        assert result["workload"] == {"adjusters": [], "unassigned": 1}

    def test_identical_inputs_yield_identical_outputs(self, seeded):
        claims, runs = seeded
        from app.analytics import build_ops_analytics

        first = build_ops_analytics(
            claims, runs, low_types=LOW_TYPES,
            low_amount_threshold=LOW_AMOUNT, sla_hours_by_severity=SLA_HOURS,
        )
        second = build_ops_analytics(
            claims, runs, low_types=LOW_TYPES,
            low_amount_threshold=LOW_AMOUNT, sla_hours_by_severity=SLA_HOURS,
        )
        assert first == second

    def test_empty_book_renders_zeroed_groups(self):
        from app.analytics import build_ops_analytics

        result = build_ops_analytics(
            [], [], low_types=LOW_TYPES, low_amount_threshold=LOW_AMOUNT,
            sla_hours_by_severity=SLA_HOURS,
        )
        assert result["cycleTime"] == {"p50Seconds": 0.0, "p95Seconds": 0.0, "decided": 0}
        assert result["stp"] == {"decided": 0, "autoApproved": 0, "escalated": 0, "rate": 0.0}
        assert result["fraud"] == {"totalClaims": 0, "flaggedClaims": 0, "rate": 0.0}
        # Spec F10: the workload group zeroes with the rest of the book.
        assert result["workload"] == {"adjusters": [], "unassigned": 0}
        assert result["decisions"] == []
        assert result["sla"]["bySeverity"] == [
            {"severity": "elevated", "slaHours": 24.0, "decided": 0,
             "breaches": 0, "breachRate": 0.0},
            {"severity": "low", "slaHours": 48.0, "decided": 0,
             "breaches": 0, "breachRate": 0.0},
        ]


class TestGrouping:
    def test_group_runs_by_claim_keeps_every_attempt(self):
        runs = [
            _run("C1", "2026-09-01T08:00:00+00:00", attempt=1),
            _run("C1", "2026-09-02T08:00:00+00:00", attempt=2),
            _run("C2", "2026-09-03T08:00:00+00:00"),
        ]
        grouped = group_runs_by_claim(runs)
        assert set(grouped) == {"C1", "C2"}
        assert len(grouped["C1"]) == 2

# ============ workload per adjuster (spec F10) ============


class TestBuildWorkload:
    """Pure workload counting: Mongo supplies rows, Python counts."""

    def test_counts_open_claims_per_adjuster_busiest_first(self):
        from app.analytics import build_workload

        claims = [
            # Open statuses (REVIEWABLE_STATUSES): escalated, pending, under_review.
            {"id": "C1", "status": "pending", "assignee_id": "u1"},
            {"id": "C2", "status": "escalated", "assignee_id": "u1"},
            {"id": "C3", "status": "under_review", "assignee_id": "u2"},
            # Decided statuses never count as workload.
            {"id": "C4", "status": "auto_approved", "assignee_id": "u2"},
            {"id": "C5", "status": "rejected", "assignee_id": "u1"},
        ]
        adjusters = [{"id": "u1", "email": "a@x.com"}, {"id": "u2", "email": "b@x.com"}]
        result = build_workload(claims, active_adjusters=adjusters)
        assert result == {
            "adjusters": [
                {"assigneeId": "u1", "email": "a@x.com", "openClaims": 2},
                {"assigneeId": "u2", "email": "b@x.com", "openClaims": 1},
            ],
            "unassigned": 0,
        }

    def test_unassigned_counts_open_claims_without_assignee(self):
        from app.analytics import build_workload

        claims = [
            {"id": "C1", "status": "pending", "assignee_id": None},  # legacy row
            {"id": "C2", "status": "escalated"},  # no field at all
            {"id": "C3", "status": "auto_approved", "assignee_id": None},
        ]
        result = build_workload(claims, active_adjusters=[])
        assert result == {"adjusters": [], "unassigned": 2}

    def test_zero_open_adjusters_still_appear_sorted(self):
        from app.analytics import build_workload

        claims = [{"id": "C1", "status": "pending", "assignee_id": "u2"}]
        adjusters = [{"id": "u1", "email": "a@x.com"}, {"id": "u2", "email": "b@x.com"}]
        result = build_workload(claims, active_adjusters=adjusters)
        assert [row["assigneeId"] for row in result["adjusters"]] == ["u2", "u1"]
        assert result["adjusters"][1]["openClaims"] == 0

    def test_ties_break_on_adjuster_id(self):
        from app.analytics import build_workload

        claims = [
            {"id": "C1", "status": "pending", "assignee_id": "u2"},
            {"id": "C2", "status": "pending", "assignee_id": "u1"},
        ]
        adjusters = [{"id": "u2", "email": "b@x.com"}, {"id": "u1", "email": "a@x.com"}]
        result = build_workload(claims, active_adjusters=adjusters)
        assert [row["assigneeId"] for row in result["adjusters"]] == ["u1", "u2"]

    async def test_route_payload_includes_workload(self, patched_mongo, make_authenticated_user):
        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.users.insert_one(
            {"id": "u1", "email": "a@x.com", "role": "adjuster", "active": True}
        )
        await patched_mongo.claims.insert_one(
            {"id": "CLM-W", "status": "pending", "assignee_id": "u1",
             "incident_type": "theft", "claimed_amount": 100.0, "fraud_check": {}}
        )
        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.get("/api/analytics/ops", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["workload"]["unassigned"] >= 0
        matching = [row for row in body["workload"]["adjusters"] if row["assigneeId"] == "u1"]
        assert matching and matching[0]["openClaims"] >= 1



# ============ API surface ============


class TestOpsAnalyticsRoute:
    async def test_route_serves_adjuster_seeded_payload(self, patched_mongo, make_authenticated_user):
        from datetime import datetime, timezone

        from fastapi.testclient import TestClient
        import server as server_mod

        await patched_mongo.claims.insert_one(
            {"id": "CLM-A", "status": "auto_approved", "incident_type": "theft",
             "claimed_amount": 1200.0, "fraud_check": {}}
        )
        submitted = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc).isoformat()
        decided = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc).isoformat()
        await patched_mongo.claim_runs.insert_one(
            {"claim_id": "CLM-A", "attempt": 1, "status": "auto_approved",
             "created_at": submitted, "finished_at": decided}
        )

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client)
            response = client.get("/api/analytics/ops", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["cycleTime"] == {"p50Seconds": 3600.0, "p95Seconds": 3600.0, "decided": 1}
        assert body["stp"]["rate"] == 1.0
        # The startup lifespan seeds 15 historical rows; the fraud denominator
        # counts every claim in the book, so derive it instead of hardcoding.
        total = await patched_mongo.claims.count_documents({})
        assert body["fraud"]["totalClaims"] == total
        assert body["fraud"]["flaggedClaims"] == 0

    async def test_route_requires_authentication(self, patched_mongo):
        from fastapi.testclient import TestClient
        import server as server_mod

        with TestClient(server_mod.app):
            response = TestClient(server_mod.app).get("/api/analytics/ops")
        assert response.status_code == 401

    async def test_route_rejects_customer_role(self, patched_mongo, make_authenticated_user):
        from fastapi.testclient import TestClient
        import server as server_mod

        with TestClient(server_mod.app) as client:
            headers, _, _ = make_authenticated_user(client, role="customer")
            response = client.get("/api/analytics/ops", headers=headers)
        assert response.status_code == 403
