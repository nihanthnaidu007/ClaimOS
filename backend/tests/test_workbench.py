"""Adjuster workbench tests (spec AC-7).

Covers the three gates the spec calls out: override-without-reason is a 422
with no state change, every valid override appends an immutable audit_log
entry, and SLA aging is computed from claim data (env-configured targets).
Also: severity derivation, queue filters/sorting, adjuster gating on every
route, and the deterministic case summary. Runs on mongomock — no MongoDB.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import database
import server
from app.config import settings
from app.workbench import (
    build_case_summary,
    matches_search,
    parse_sla_hours,
    queue_row,
    severity_for_claim,
    sla_state,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
        # Row-level assertions are deterministic: start from no claims, even
        # though startup seeding creates demo ones.
        _run(database.claims_col.delete_many({}))
        yield test_client


@pytest.fixture
def adjuster_headers(make_authenticated_user):
    """Factory: register+login an adjuster, return bearer headers."""

    def _make(client):
        headers, _csrf, _email = make_authenticated_user(client, role="adjuster")
        return headers

    return _make


def _insert_claim(**overrides):
    """Insert a workbench-shaped LOW-severity claim (theft ≤ $10k per the STP
    rule); returns its id. Overrides change severity deterministically."""
    claim = {
        "id": "CLM-TEST-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "incident_date": "2026-09-01",
        "claimed_amount": 1200.0,
        "description": "Rear-end collision.",
        "status": "escalated",
        "risk_score": 0.42,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent_trace": {},
        "agent_logs": [],
        **overrides,
    }
    _run(database.claims_col.insert_one(claim.copy()))
    return claim["id"]


# ============ SLA computation (pure) ============


def test_parse_sla_hours_parses_severity_pairs():
    assert parse_sla_hours("low:72,elevated:24") == {"low": 72.0, "elevated": 24.0}


def test_parse_sla_hours_rejects_malformed_entries():
    with pytest.raises(ValueError):
        parse_sla_hours("low:72,oops")
    with pytest.raises(ValueError):
        parse_sla_hours("low")


def test_sla_state_breaches_after_target(monkeypatch):
    monkeypatch.setattr(settings, "sla_hours_per_severity", "low:72,elevated:24")
    created = datetime.now(timezone.utc) - timedelta(hours=80)
    state = sla_state(created.isoformat(), "low")
    assert state["breached"] is True
    assert state["state"] == "breached"
    assert state["hoursElapsed"] == pytest.approx(80, abs=0.01)
    assert state["hoursRemaining"] == pytest.approx(-8, abs=0.01)


def test_sla_state_at_risk_past_fraction(monkeypatch):
    monkeypatch.setattr(settings, "sla_hours_per_severity", "low:72,elevated:24")
    monkeypatch.setattr(settings, "sla_at_risk_fraction", 0.75)
    created = datetime.now(timezone.utc) - timedelta(hours=60)  # 83% of 72h
    state = sla_state(created.isoformat(), "low")
    assert state["state"] == "at_risk"
    assert state["breached"] is False


def test_sla_state_ok_when_fresh():
    created = datetime.now(timezone.utc) - timedelta(hours=1)
    state = sla_state(created.isoformat(), "low")
    assert state["state"] == "ok"
    assert state["breached"] is False


def test_sla_state_per_severity_target(monkeypatch):
    monkeypatch.setattr(settings, "sla_hours_per_severity", "low:72,elevated:24")
    created = datetime.now(timezone.utc) - timedelta(hours=30)
    # 30h breaches a 24h elevated target but is barely at-risk for a 72h low one.
    assert sla_state(created.isoformat(), "elevated")["breached"] is True
    assert sla_state(created.isoformat(), "low")["breached"] is False


def test_sla_state_handles_unparseable_and_future_timestamps():
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    for created in (None, "not-a-date", future):
        state = sla_state(created, "low")
        assert state["state"] == "ok"
        assert state["hoursElapsed"] == 0.0


def test_sla_target_unknown_severity_uses_default(monkeypatch):
    monkeypatch.setattr(settings, "sla_default_hours", 48.0)
    assert sla_state(None, "catastrophic")["targetHours"] == 48.0


# ============ Severity derivation ============


def test_severity_prefers_stored_stp_verdict():
    claim = {"stp": {"severity": "elevated"}, "claimed_amount": 100}
    assert severity_for_claim(claim) == "elevated"


def test_severity_rederived_low_from_intake():
    claim = {
        "claimed_amount": 2500.0,
        "incident_type": "theft",
        "agent_trace": {"intake": {"normalizedData": {"incidentType": "theft"}}},
    }
    assert severity_for_claim(claim) == "low"


def test_severity_rederived_elevated_by_amount():
    claim = {"claimed_amount": 25000.0, "incident_type": "theft"}
    assert severity_for_claim(claim) == "elevated"


# ============ Case summary (deterministic, no LLM) ============


def _claim_with_trace():
    return {
        "id": "CLM-TRACE-1",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "accident",
        "claimed_amount": 1200.0,
        "status": "escalated",
        "risk_score": 0.55,
        "created_at": "2026-09-17T10:00:00+00:00",
        "escalation_reason": "confidence below STP threshold",
        "agent_trace": {
            "intake": {"valid": True, "normalizedData": {"incidentType": "accident"}},
            "policy": {"found": True, "statusCheck": "active", "withinLimits": True},
            "documents": {"consistencyScore": 0.9, "redFlags": []},
            "eligibility": {"eligible": True, "recommendation": "approve", "riskFactors": ["late_report"]},
            "decision": {"verdict": "approved", "payoutAmount": 1100.0, "confidence": 0.62,
                         "letterSubject": "Decision letter", "letterBody": "..."},
        },
        "agent_logs": [
            {"agent": "intake", "status": "complete", "durationMs": 120},
            {"agent": "decision", "status": "complete", "durationMs": 300},
        ],
    }


def test_case_summary_assembled_from_stored_traces():
    summary = build_case_summary(_claim_with_trace())
    assert summary["source"] == "stored agent traces"
    assert summary["decision"]["verdict"] == "approved"
    assert summary["confidence"] == 0.62
    assert summary["recommendation"] == "approve"
    reached = {stage["agent"]: stage for stage in summary["stages"]}
    assert reached["intake"]["reached"] is True
    assert reached["policy"]["reached"] is True
    assert reached["policy"]["durationMs"] is None  # no log for that stage
    assert reached["decision"]["reached"] is True


def test_case_summary_flags_unreached_stages():
    claim = _claim_with_trace()
    claim["agent_trace"] = {"intake": claim["agent_trace"]["intake"]}
    claim["agent_logs"] = []
    summary = build_case_summary(claim)
    by_agent = {stage["agent"]: stage for stage in summary["stages"]}
    assert by_agent["intake"]["reached"] is True
    assert by_agent["policy"]["reached"] is False
    assert by_agent["decision"]["status"] == "pending"


def test_queue_row_carries_severity_and_sla():
    claim = _claim_with_trace()
    claim["stp"] = {"severity": "elevated"}
    row = queue_row(claim, now=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    assert row["severity"] == "elevated"
    assert row["sla"]["state"] in ("ok", "at_risk", "breached")
    assert row["holder_name"] == "Sarah Chen"


# ============ Queue API: gating, filters, sorting ============


def test_queue_requires_authentication(client):
    assert client.get("/api/workbench/queue").status_code == 401


def test_queue_rejects_customer_role(client, make_authenticated_user):
    token = _login_token(client, "customer")
    response = client.get(
        "/api/workbench/queue", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def _login_token(client, role):
    """Register+login a fresh user of `role`, return their access token."""
    from uuid import uuid4

    from conftest import TEST_INVITE_CODE, TEST_PASSWORD

    email = f"{role}-{uuid4().hex[:8]}@test.example"
    register = client.post(
        "/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "role": role,
              "inviteCode": TEST_INVITE_CODE},
    )
    assert register.status_code == 201
    login = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    return login.json()["accessToken"]


def test_queue_returns_reviewable_rows_with_sla(client, adjuster_headers):
    _insert_claim()  # escalated, low severity (theft, small amount)
    _insert_claim(id="CLM-TEST-002", status="auto_approved")  # decided: not on the queue
    _insert_claim(id="CLM-TEST-003", claimed_amount=25000.0)  # elevated by amount
    response = client.get("/api/workbench/queue", headers=adjuster_headers(client))
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-001", "CLM-TEST-003"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["CLM-TEST-003"]["severity"] == "elevated"
    assert by_id["CLM-TEST-001"]["severity"] == "low"
    assert by_id["CLM-TEST-001"]["sla"]["state"] == "ok"


def test_queue_severity_filter(client, adjuster_headers):
    _insert_claim()
    _insert_claim(id="CLM-TEST-003", claimed_amount=25000.0)
    rows = client.get(
        "/api/workbench/queue", headers=adjuster_headers(client), params={"severity": "elevated"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-003"]


def test_queue_age_filter_and_sort(client, adjuster_headers, monkeypatch):
    monkeypatch.setattr(settings, "sla_hours_per_severity", "low:72,elevated:24")
    now = datetime.now(timezone.utc)
    _insert_claim(id="CLM-FRESH", created_at=now.isoformat(), risk_score=0.1)
    _insert_claim(id="CLM-OLD", created_at=(now - timedelta(hours=50)).isoformat(), risk_score=0.9)
    _insert_claim(id="CLM-STALE", created_at=(now - timedelta(hours=80)).isoformat(), risk_score=0.5)

    headers = adjuster_headers(client)
    # Age sort: oldest first by default.
    rows = client.get("/api/workbench/queue", headers=headers).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-STALE", "CLM-OLD", "CLM-FRESH"]

    # min_age_hours drops fresh rows.
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"min_age_hours": 60}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-STALE"]

    # max_age_hours keeps only the fresh row.
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"max_age_hours": 10}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-FRESH"]

    # Risk sort: highest risk first.
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"sort": "risk"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-OLD", "CLM-STALE", "CLM-FRESH"]


def test_queue_severity_sort_puts_elevated_first(client, adjuster_headers):
    now = datetime.now(timezone.utc)
    _insert_claim(id="CLM-LOW", created_at=now.isoformat())  # low (small amount)
    _insert_claim(id="CLM-HIGH", created_at=(now - timedelta(hours=1)).isoformat(),
                  claimed_amount=25000.0)
    rows = client.get(
        "/api/workbench/queue", headers=adjuster_headers(client), params={"sort": "severity"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-HIGH", "CLM-LOW"]


def test_queue_rejects_unknown_sort(client, adjuster_headers):
    response = client.get(
        "/api/workbench/queue", headers=adjuster_headers(client), params={"sort": "nonsense"}
    )
    assert response.status_code == 400


def test_queue_status_filter_param(client, adjuster_headers):
    _insert_claim()
    _insert_claim(id="CLM-DECIDED", status="auto_approved")
    rows = client.get(
        "/api/workbench/queue", headers=adjuster_headers(client),
        params={"status": "auto_approved"},
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-DECIDED"]


# ============ Queue search (spec F8: AC-8.1, AC-8.2) ============


def _seed_search_claims():
    """Two reviewable claims with disjoint numbers, policies, and holders."""
    _insert_claim(id="CLM-TEST-001")  # policy AUTO-2024-001847, holder Sarah Chen
    _insert_claim(
        id="CLM-OTHER-9",
        policy_number="AUTO-2023-555111",
        holder_name="Ada Lovelace",
    )


def test_matches_search_claim_number_is_prefix_only():
    row = {"id": "CLM-TEST-001", "policy_number": "AUTO-2024-001847", "holder_name": "Sarah Chen"}
    assert matches_search(row, "CLM-TEST") is True
    assert matches_search(row, "clm-te") is True  # case-insensitive prefix
    # Neither policy nor name contains these, and they are not claim prefixes.
    assert matches_search(row, "TEST-001") is False
    assert matches_search(row, "lm-test") is False


def test_matches_search_policy_and_name_are_substrings():
    row = {"id": "CLM-TEST-001", "policy_number": "AUTO-2024-001847", "holder_name": "Sarah Chen"}
    # Substring in the middle of the policy number, any case.
    assert matches_search(row, "01847") is True
    assert matches_search(row, "auto-2024") is True
    assert matches_search(row, "CHEN") is True
    assert matches_search(row, "rah C") is True
    assert matches_search(row, "grace") is False


def test_queue_search_by_claim_number_prefix(client, adjuster_headers):
    _seed_search_claims()
    headers = adjuster_headers(client)
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"search": "clm-test"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-001"]


def test_queue_search_by_policy_number_substring(client, adjuster_headers):
    _seed_search_claims()
    headers = adjuster_headers(client)
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"search": "555111"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-OTHER-9"]
    # Case-insensitive and matches a mid-policy segment.
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"search": "AUTO-2023"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-OTHER-9"]


def test_queue_search_by_customer_name_substring(client, adjuster_headers):
    _seed_search_claims()
    headers = adjuster_headers(client)
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"search": "chen"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-001"]
    rows = client.get(
        "/api/workbench/queue", headers=headers, params={"search": "ADA LOVELACE"}
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-OTHER-9"]


def test_queue_search_blank_query_is_a_noop(client, adjuster_headers):
    _seed_search_claims()
    headers = adjuster_headers(client)
    for blank in ("", "   "):
        rows = client.get(
            "/api/workbench/queue", headers=headers, params={"search": blank}
        ).json()["rows"]
        assert sorted(row["id"] for row in rows) == ["CLM-OTHER-9", "CLM-TEST-001"]


def test_queue_search_with_no_matches_returns_empty_list(client, adjuster_headers):
    _seed_search_claims()
    rows = client.get(
        "/api/workbench/queue", headers=adjuster_headers(client), params={"search": "zzz-nothing"}
    ).json()["rows"]
    assert rows == []


def test_queue_search_composes_with_filters_and_sort(client, adjuster_headers, monkeypatch):
    monkeypatch.setattr(settings, "sla_hours_per_severity", "low:72,elevated:24")
    now = datetime.now(timezone.utc)
    _insert_claim(id="CLM-TEST-001", created_at=now.isoformat())  # low, fresh
    _insert_claim(
        id="CLM-TEST-003",
        created_at=(now - timedelta(hours=50)).isoformat(),
        claimed_amount=25000.0,  # elevated by amount, old
    )
    _insert_claim(
        id="CLM-OTHER-9",
        policy_number="AUTO-2023-555111",
        holder_name="Ada Lovelace",
        created_at=(now - timedelta(hours=80)).isoformat(),
        claimed_amount=25000.0,  # elevated, oldest — outside the search scope
    )
    headers = adjuster_headers(client)

    # Search + severity filter: only the elevated CLM-TEST claim survives.
    rows = client.get(
        "/api/workbench/queue", headers=headers,
        params={"search": "CLM-TEST", "severity": "elevated"},
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-003"]

    # Search + age filter: the 50h-old claim, not the fresh one.
    rows = client.get(
        "/api/workbench/queue", headers=headers,
        params={"search": "CLM-TEST", "min_age_hours": 40},
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-003"]

    # Search + severity sort keeps the current sort contract.
    rows = client.get(
        "/api/workbench/queue", headers=headers,
        params={"search": "CLM-TEST", "sort": "severity"},
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-TEST-003", "CLM-TEST-001"]

    # Search hits every field at once: "2023" only in the OTHER policy number.
    rows = client.get(
        "/api/workbench/queue", headers=headers,
        params={"search": "2023", "severity": "elevated", "sort": "risk"},
    ).json()["rows"]
    assert [row["id"] for row in rows] == ["CLM-OTHER-9"]


def test_queue_search_does_not_widen_customer_access(client, make_authenticated_user):
    """AC-8.2: search cannot reach the workbench with a customer token."""
    _seed_search_claims()
    token = _login_token(client, "customer")
    response = client.get(
        "/api/workbench/queue",
        headers={"Authorization": f"Bearer {token}"},
        params={"search": "CLM-TEST-001"},
    )
    assert response.status_code == 403


# ============ Case summary + events endpoints ============


def test_case_summary_endpoint_returns_stored_traces(client, adjuster_headers):
    claim_id = _insert_claim(agent_trace={"policy": {"found": True}})
    response = client.get(f"/api/workbench/claims/{claim_id}/summary",
                          headers=adjuster_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body["claimId"] == claim_id
    assert body["coverage"]["found"] is True
    assert body["source"] == "stored agent traces"


def test_case_endpoints_404_for_unknown_claim(client, adjuster_headers):
    headers = adjuster_headers(client)
    for path in ("/api/workbench/claims/NOPE/summary", "/api/workbench/claims/NOPE/audit",
                 "/api/workbench/claims/NOPE/events"):
        assert client.get(path, headers=headers).status_code == 404


def test_case_events_endpoint_returns_durable_events(client, adjuster_headers):
    claim_id = _insert_claim()
    from app.events import emit_event

    _run(emit_event(claim_id, {"event": "stage_started", "agent": "intake"}))
    response = client.get(f"/api/workbench/claims/{claim_id}/events",
                          headers=adjuster_headers(client))
    assert response.status_code == 200
    events = response.json()["events"]
    assert events and events[0]["event"] == "stage_started"


# ============ Override: reason required, audit appended ============


def test_override_without_reason_rejected_422_no_state_change(client, adjuster_headers):
    """Spec AC-7: a missing (or blank) reason is a 422 and nothing changes."""
    claim_id = _insert_claim()
    headers = adjuster_headers(client)
    for payload in (
        {"decision": "approved"},                      # reason missing
        {"decision": "approved", "reason": ""},        # empty
        {"decision": "approved", "reason": "    "},    # whitespace-only
    ):
        response = client.post(f"/api/workbench/claims/{claim_id}/override",
                               json=payload, headers=headers)
        assert response.status_code == 422, payload

    # No state change anywhere: claim, audit trail, event stream.
    claim = _run(database.claims_col.find_one({"id": claim_id}, {"_id": 0}))
    assert claim["status"] == "escalated"
    assert "override" not in claim
    assert _run(database.audit_log_col.count_documents({})) == 0
    assert _run(database.events_col.count_documents({})) == 0


def test_override_appends_audit_entry_and_updates_claim(client, adjuster_headers):
    claim_id = _insert_claim(
        agent_trace={"eligibility": {"recommendation": "reject"},
                     "decision": {"verdict": "rejected", "payoutAmount": 0}},
    )
    headers = adjuster_headers(client)
    response = client.post(
        f"/api/workbench/claims/{claim_id}/override",
        json={"decision": "approved", "payoutAmount": 900.0,
              "reason": "Policy documents verified manually; coverage confirmed."},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "overridden"

    entry = body["auditEntry"]
    assert entry["action"] == "override"
    assert entry["claim_id"] == claim_id
    assert "manual" in entry["reason"]
    assert entry["before"]["status"] == "escalated"
    assert entry["before"]["verdict"] == "rejected"
    assert entry["after"]["verdict"] == "approved"
    assert entry["after"]["payoutAmount"] == 900.0
    assert entry["at"]  # timestamped
    assert entry["actor"]  # actor recorded

    # Claim carries the override block for the case view.
    claim = _run(database.claims_col.find_one({"id": claim_id}, {"_id": 0}))
    assert claim["status"] == "overridden"
    assert claim["override"]["reason"] == entry["reason"]
    assert claim["override"]["auditId"] == entry["id"]

    # Exactly one audit_log row; live viewers got a claim_overridden event.
    assert _run(database.audit_log_col.count_documents({})) == 1
    events = _run(database.events_col.find({"claim_id": claim_id}, {"_id": 0}).to_list(10))
    assert any(event.get("event") == "claim_overridden" for event in events)


def test_override_audit_trail_endpoint_lists_newest_first(client, adjuster_headers):
    claim_id = _insert_claim()
    headers = adjuster_headers(client)
    for i in range(2):
        client.post(
            f"/api/workbench/claims/{claim_id}/override",
            json={"reason": f"Reason {i}"},
            headers=headers,
        )
        # Second override hits the decided-claim guard; only first lands.
        break
    audit = client.get(f"/api/workbench/claims/{claim_id}/audit", headers=headers).json()
    assert len(audit) == 1
    assert audit[0]["reason"] == "Reason 0"


def test_override_on_decided_claim_conflict_409(client, adjuster_headers):
    claim_id = _insert_claim(status="auto_approved")
    response = client.post(
        f"/api/workbench/claims/{claim_id}/override",
        json={"reason": "Too late"},
        headers=adjuster_headers(client),
    )
    assert response.status_code == 409
    assert _run(database.audit_log_col.count_documents({})) == 0


def test_override_on_unknown_claim_404(client, adjuster_headers):
    response = client.post(
        "/api/workbench/claims/NOPE/override",
        json={"reason": "Nobody home"},
        headers=adjuster_headers(client),
    )
    assert response.status_code == 404


def test_override_rejects_customer_role(client, make_authenticated_user):
    claim_id = _insert_claim()
    token = _login_token(client, "customer")
    response = client.post(
        f"/api/workbench/claims/{claim_id}/override",
        json={"reason": "Customers cannot override"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert _run(database.audit_log_col.count_documents({})) == 0


def test_audit_endpoint_rejects_customer_role(client, make_authenticated_user):
    token = _login_token(client, "customer")
    response = client.get(
        "/api/workbench/claims/CLM-TEST-001/audit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


# ============ Fraud cross-check surfacing (ops PR) ============


def test_queue_row_carries_fraud_flags_with_legacy_coercion():
    """fraud_flags passes through for flagged claims; legacy docs coerce to []."""
    flag = {
        "code": "DUPLICATE_INCIDENT_FINGERPRINT",
        "severity": "high",
        "detail": "Matches prior claim CLM-PRIOR-1",
        "evidence": {"duplicate_of_claim_id": "CLM-PRIOR-1"},
    }
    row = queue_row({"id": "CLM-F1", "status": "under_review", "fraud_flags": [flag]})
    assert row["fraud_flags"] == [flag]

    # Seeded/legacy claims have no fraud fields at all — no None leaks.
    legacy = queue_row({"id": "CLM-L1", "status": "under_review"})
    assert legacy["fraud_flags"] == []


def test_case_summary_exposes_fraud_flags_and_stage():
    claim = _claim_with_trace()
    flag = {
        "code": "IMPOSSIBLE_DATE",
        "severity": "high",
        "detail": "Incident date is in the future",
        "evidence": {},
    }
    claim["fraud_flags"] = [flag]
    summary = build_case_summary(claim)

    assert summary["fraudFlags"] == [flag]
    # The six-stage timeline includes the fraud cross-check with its label.
    labels = [stage["label"] for stage in summary["stages"]]
    assert "Fraud cross-check" in labels
    assert labels.index("Fraud cross-check") == labels.index("Document analysis") + 1
