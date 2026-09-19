"""Decision-letter template tests (spec F13).

AC-13.1 - byte parity: rendering the seeded default template with the
fixture pipeline's merge context reproduces the decision letter the
fixture decision stage writes today, byte for byte.
AC-13.2 - every documented merge variable renders; unknown or malformed
slots render EMPTY with a warning (never raw {{...}} braces).
AC-13.3 - template CRUD is audited with before/after snapshots; the
default template cannot be deleted; preview renders without persisting
or mutating anything; the render feeds the existing PDF letter path.
"""

import asyncio
import base64
import re
import zlib
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import database
import server
from app.letter_templates import (
    DEFAULT_TEMPLATE_ID,
    build_merge_context,
    default_letter_template,
    ensure_default_template,
    render_letter,
    render_template_text,
)
from app.llm.fixture import _decision_output


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:  # runs lifespan (seeding)
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
    """An escalated claim with an approved decision trace (letter present)."""
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
        "agent_trace": {
            "intake": {
                "normalizedData": {
                    "incidentType": "theft",
                    "incidentDate": "2026-09-01",
                }
            },
            "policy": {"policyData": {"holder_name": "Sarah Chen"}},
            "decision": {
                "verdict": "approved",
                "payoutAmount": 700.0,
                "letterSubject": "Your Claim CLM-TEST-001 Has Been Approved",
                "letterBody": "Dear Sarah Chen, your claim has been approved.",
            },
        },
        "agent_logs": [],
        **overrides,
    }
    _run(database.claims_col.insert_one(claim.copy()))
    return claim["id"]


# ============ AC-13.1: byte parity with the fixture letter ============

# The exact parse anchors the fixture decision stage reads (holder, payout,
# recommendation, incident fields) with values matching the claim below.
PARITY_USER_TEXT = (
    "Holder Name: Sarah Chen\n"
    "Computed payout amount: $1,200.00 \n"
    "recommendation=auto_approve,\n"
    "incident date=2026-09-01, incident type=theft"
)


def test_default_template_matches_the_pipeline_letter():
    """AC-13.1: the seeded default renders the fixture letter byte-for-byte."""
    claim = {
        "id": "CLM-PARITY-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "incident_type": "theft",
        "incident_date": "2026-09-01",
        "agent_trace": {
            "intake": {
                "normalizedData": {
                    "incidentType": "theft",
                    "incidentDate": "2026-09-01",
                }
            },
            "policy": {"policyData": {"holder_name": "Sarah Chen"}},
            "decision": {"verdict": "approved", "payoutAmount": 1200.0},
        },
    }
    context = build_merge_context(claim)
    template = default_letter_template()
    rendered = render_letter(template["subject"], template["body"], context)
    fixture_letter = _decision_output(PARITY_USER_TEXT, claim["id"])
    assert rendered["warnings"] == []
    assert rendered["subject"] == fixture_letter.letterSubject
    assert rendered["body"] == fixture_letter.letterBody


# ============ AC-13.2: variable rendering and safety ============


def test_all_documented_merge_variables_render():
    context = {
        "claim_number": "CLM-X",
        "customer_name": "Ada",
        "policy_number": "POL-1",
        "decision": "approved",
        "amount": "$1,500.00",
        "today": "2026-09-18",
        "incident_type": "water damage",
        "incident_date": "2026-09-01",
    }
    text = (
        "{{claim_number}}|{{customer_name}}|{{policy_number}}|{{decision}}"
        "|{{amount}}|{{today}}|{{incident_type}}|{{incident_date}}"
    )
    rendered, warnings = render_template_text(text, context)
    assert rendered == "CLM-X|Ada|POL-1|approved|$1,500.00|2026-09-18|water damage|2026-09-01"
    assert warnings == []


def test_whitespace_inside_slots_is_tolerated():
    rendered, warnings = render_template_text(
        "Hi {{  customer_name  }}!", {"customer_name": "Ada"}
    )
    assert rendered == "Hi Ada!"
    assert warnings == []


def test_unknown_variable_renders_empty_with_warning():
    rendered, warnings = render_template_text("Hi {{ghost_name}}!", {})
    assert rendered == "Hi !"
    assert warnings == ["unknown_variable:ghost_name"]
    assert "{{" not in rendered and "}}" not in rendered


def test_malformed_slots_never_leave_braces():
    for text in ("A {{}} B", "A {{two words}} B", "Hi {{oops"):
        rendered, warnings = render_template_text(text, {"claim_number": "C"})
        assert "{{" not in rendered and "}}" not in rendered, text
        assert any(w.startswith("malformed_merge_slot:") for w in warnings), text


def test_merge_context_falls_back_gracefully():
    context = build_merge_context(
        {
            "id": "CLM-1",
            "policy_number": "P-1",
            "holder_name": "Fallback Name",
            "incident_type": "water_damage",
            "incident_date": "2026-01-02",
        }
    )
    assert context["customer_name"] == "Fallback Name"
    assert context["incident_type"] == "water damage"  # underscores become spaces
    assert context["amount"] == "$0.00"
    assert context["decision"] == ""
    assert context["today"]  # UTC render date present


# ============ AC-13.3: CRUD is audited, preview is read-only ============


def test_default_template_is_seeded_and_idempotent(client, adjuster_headers):
    headers = adjuster_headers(client)
    first = client.get(
        "/api/workbench/letter-templates", headers=headers
    ).json()["templates"]
    assert [t["id"] for t in first] == [DEFAULT_TEMPLATE_ID]
    assert first[0]["isDefault"] is True
    _run(ensure_default_template())  # second seed attempt must not duplicate
    second = client.get(
        "/api/workbench/letter-templates", headers=headers
    ).json()["templates"]
    assert [t["id"] for t in second] == [DEFAULT_TEMPLATE_ID]


def test_template_crud_is_audited(client, adjuster_headers):
    headers = adjuster_headers(client)
    created = client.post(
        "/api/workbench/letter-templates",
        json={
            "name": "Theft letter",
            "description": "",
            "subject": "S {{claim_number}}",
            "body": "B {{customer_name}}",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    assert template_id.startswith("ltpl_") and template_id != DEFAULT_TEMPLATE_ID

    updated = client.put(
        f"/api/workbench/letter-templates/{template_id}",
        json={
            "name": "Theft letter v2",
            "description": "",
            "subject": "S2 {{claim_number}}",
            "body": "B2",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Theft letter v2"

    listing = client.get(
        "/api/workbench/letter-templates", headers=headers
    ).json()["templates"]
    ids = [t["id"] for t in listing]
    assert DEFAULT_TEMPLATE_ID in ids and template_id in ids

    deleted = client.delete(
        f"/api/workbench/letter-templates/{template_id}", headers=headers
    )
    assert deleted.status_code == 200, deleted.text

    entries = _run(
        database.audit_log_col.find({}, {"_id": 0}).sort("at", 1).to_list(100)
    )
    assert [e["action"] for e in entries] == [
        "letter_template_created",
        "letter_template_updated",
        "letter_template_deleted",
    ]
    update_entry = entries[1]
    assert update_entry["before"]["name"] == "Theft letter"
    assert update_entry["after"]["name"] == "Theft letter v2"
    assert update_entry["actor_email"]


def test_default_template_cannot_be_deleted(client, adjuster_headers):
    headers = adjuster_headers(client)
    response = client.delete(
        f"/api/workbench/letter-templates/{DEFAULT_TEMPLATE_ID}", headers=headers
    )
    assert response.status_code == 409
    still_there = client.get(
        "/api/workbench/letter-templates", headers=headers
    ).json()["templates"]
    assert DEFAULT_TEMPLATE_ID in [t["id"] for t in still_there]
    deletions = _run(
        database.audit_log_col.count_documents({"action": "letter_template_deleted"})
    )
    assert deletions == 0


def test_template_validation_rejects_blank_fields(client, adjuster_headers):
    headers = adjuster_headers(client)
    response = client.post(
        "/api/workbench/letter-templates",
        json={"name": "   ", "subject": "S", "body": "B"},
        headers=headers,
    )
    assert response.status_code == 422


def test_template_routes_require_an_adjuster(client, make_authenticated_user):
    anon = client.get("/api/workbench/letter-templates")
    assert anon.status_code in (401, 403)
    headers, _csrf, _email = make_authenticated_user(client, role="customer")
    response = client.get("/api/workbench/letter-templates", headers=headers)
    assert response.status_code == 403


def test_preview_renders_without_mutating_or_auditing(client, adjuster_headers):
    claim_id = _insert_claim()
    headers = adjuster_headers(client)
    before = _run(database.claims_col.find_one({"id": claim_id}, {"_id": 0}))
    response = client.post(
        f"/api/workbench/claims/{claim_id}/letter/preview",
        json={},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["templateId"] == DEFAULT_TEMPLATE_ID
    assert data["claimId"] == claim_id
    assert data["subject"] == "Your Claim CLM-TEST-001 Has Been Approved"
    assert data["body"].startswith("Dear Sarah Chen,")
    assert data["warnings"] == []
    after = _run(database.claims_col.find_one({"id": claim_id}, {"_id": 0}))
    assert after == before  # strictly read-only: no claim mutation
    assert _run(database.audit_log_col.count_documents({})) == 0  # no audit write
    assert _run(database.letter_templates_col.count_documents({})) == 1  # nothing persisted


def test_preview_uses_requested_template_and_flags_unknown_variables(
    client, adjuster_headers
):
    claim_id = _insert_claim()
    headers = adjuster_headers(client)
    create = client.post(
        "/api/workbench/letter-templates",
        json={
            "name": "Probe",
            "description": "",
            "subject": "PROBE {{claim_number}}",
            "body": "Hi {{customer_name}} / {{nope}}",
        },
        headers=headers,
    )
    template_id = create.json()["id"]
    response = client.post(
        f"/api/workbench/claims/{claim_id}/letter/preview",
        json={"templateId": template_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["templateId"] == template_id
    assert data["subject"] == "PROBE CLM-TEST-001"
    assert data["body"] == "Hi Sarah Chen / "  # unknown rendered empty, not raw braces
    assert data["warnings"] == ["unknown_variable:nope"]


# ============ Render feeds the PDF path ============


def _pdf_drawn_text(raw: bytes) -> bytes:
    """Decompress the PDF's content streams so drawn text is greppable.

    fpdf2 compresses content streams (FlateDecode); null bytes are stripped
    so both latin-1 core-font text and UTF-16 subset text match.
    """
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.DOTALL):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue  # not a deflate stream
    return b"\n".join(chunks).replace(b"\x00", b"")


def test_pdf_export_uses_selected_template(client, adjuster_headers):
    claim_id = _insert_claim()
    headers = adjuster_headers(client)
    create = client.post(
        "/api/workbench/letter-templates",
        json={
            "name": "PDF probe",
            "description": "",
            "subject": "PROBE SUBJECT {{claim_number}}",
            "body": "PROBE BODY for {{customer_name}}",
        },
        headers=headers,
    )
    template_id = create.json()["id"]

    with_template = client.get(
        f"/api/claims/{claim_id}/pdf",
        params={"template_id": template_id},
        headers=headers,
    )
    assert with_template.status_code == 200
    pdf_bytes = base64.b64decode(with_template.json()["pdf"])
    assert pdf_bytes.startswith(b"%PDF")
    # The PDF letter section draws the letter body (not the subject), so the
    # probe body proves the render fed the PDF path - and the claim's stored
    # letterBody must be gone, replaced by the template render.
    drawn = _pdf_drawn_text(pdf_bytes)
    assert b"PROBE BODY for Sarah Chen" in drawn
    assert b"Dear Sarah Chen, your claim has been approved." not in drawn

    missing = client.get(
        f"/api/claims/{claim_id}/pdf",
        params={"template_id": "ltpl_ghost"},
        headers=headers,
    )
    assert missing.status_code == 404
