"""F11 internal claim notes: sanitization, mentions, CRUD, audit, fan-out,
and the customer-invisibility regression (AC-11.2)."""

import asyncio
import json
import uuid

import pytest

import database
from app.notes import (
    mention_handles,
    mention_notification_doc,
    parse_mentions,
    sanitize_note_body,
)
from app.schemas import MAX_MENTIONS_PER_NOTE, NOTE_MAX_LENGTH
from app.status_portal import public_status_payload

pytestmark = pytest.mark.usefixtures("patched_mongo")


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from server import app

    return TestClient(app)


def make_claim(claim_id=None):
    claim_id = claim_id or f"CLM-20240101-{uuid.uuid4().hex[:6]}"
    asyncio.run(database.claims_col.insert_one({"id": claim_id, "status": "in_review"}))
    return claim_id


def synthetic_adjusters(*handles):
    out = []
    for handle in handles:
        doc = {
            "id": f"usr-{handle}-{uuid.uuid4().hex[:6]}",
            "email": f"{handle}@claimos.test",
            "role": "adjuster",
        }
        out.append(doc)
    return out


# ---- Sanitization (pure) ----


def test_sanitizer_strips_control_chars_and_ansi():
    dirty = "\x1b[31malert\x1b[0m\x07 watch out"
    assert sanitize_note_body(dirty, NOTE_MAX_LENGTH) == "alert watch out"


def test_sanitizer_keeps_newlines_and_tabs():
    assert sanitize_note_body("line one\nline two\tindented", NOTE_MAX_LENGTH) == (
        "line one\nline two\tindented"
    )


def test_sanitizer_normalizes_crlf():
    assert sanitize_note_body("a\r\nb\rc", NOTE_MAX_LENGTH) == "a\nb\nc"


def test_sanitizer_caps_length():
    body = "x" * (NOTE_MAX_LENGTH + 500)
    assert len(sanitize_note_body(body, NOTE_MAX_LENGTH)) == NOTE_MAX_LENGTH


def test_sanitizer_strips_outer_whitespace():
    assert sanitize_note_body("   padded   ", NOTE_MAX_LENGTH) == "padded"


# ---- Mention parsing (pure) ----


def test_mention_handles_dedupe_in_order():
    assert mention_handles("@alex @alex @bob") == ["alex", "bob"]


def test_email_addresses_are_not_mentions():
    assert mention_handles("contact alex.rivera@example.com now") == []


def test_punctuation_stops_a_handle():
    assert mention_handles("ping @alex, @sam.") == ["alex", "sam"]


def test_parse_mentions_resolves_local_part_case_insensitively():
    adjusters = synthetic_adjusters("alex.rivera", "sam.lee")
    matched = parse_mentions("cc @ALEX.RIVERA please", adjusters)
    assert [a["email"] for a in matched] == ["alex.rivera@claimos.test"]


def test_parse_mentions_skips_unknown_handles():
    adjusters = synthetic_adjusters("alex.rivera")
    assert parse_mentions("@nobody @alex.rivera", adjusters) == [adjusters[0]]


def test_parse_mentions_collapses_duplicate_targets():
    adjusters = synthetic_adjusters("alex.rivera")
    matched = parse_mentions("@alex.rivera @ALEX.RIVERA @alex.rivera", adjusters)
    assert len(matched) == 1


def test_parse_mentions_caps_fanout():
    handles = [f"adj{i}" for i in range(MAX_MENTIONS_PER_NOTE + 3)]
    adjusters = synthetic_adjusters(*handles)
    body = " ".join(f"@{h}" for h in handles)
    assert len(parse_mentions(body, adjusters)) == MAX_MENTIONS_PER_NOTE


def test_mention_bell_carries_no_note_text():
    adjusters = synthetic_adjusters("alex.rivera")
    doc = mention_notification_doc(
        claim_id="CLM-20240101-000001",
        note_id="note_1",
        recipient=adjusters[0],
    )
    assert doc["recipient_email"] == "alex.rivera@claimos.test"
    assert doc["read"] is False
    assert "note_1" not in doc["title"] + doc["body"]


# ---- Routes ----


@pytest.fixture
def adjuster(api_client, make_authenticated_user):
    headers, csrf, email = make_authenticated_user(api_client, role="adjuster")
    return {"headers": headers, "csrf": csrf, "email": email, "client": api_client}


def _post_note(adjuster, claim_id, body):
    return adjuster["client"].post(
        f"/api/workbench/claims/{claim_id}/notes",
        json={"body": body},
        headers={**adjuster["headers"], "X-CSRF-Token": adjuster["csrf"]},
    )


def _get_notes(adjuster, claim_id):
    return adjuster["client"].get(
        f"/api/workbench/claims/{claim_id}/notes", headers=adjuster["headers"]
    )


def test_create_and_list_roundtrip(adjuster):
    claim_id = make_claim()
    response = _post_note(adjuster, claim_id, "Checked the police report — liability is clear.")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["claimId"] == claim_id
    assert created["authorEmail"] == adjuster["email"]
    assert created["body"] == "Checked the police report — liability is clear."
    assert created["mentions"] == []
    assert created["auditEntryId"].startswith("aud_")

    listed = _get_notes(adjuster, claim_id)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["claimId"] == claim_id
    assert [n["id"] for n in payload["notes"]] == [created["id"]]


def test_notes_require_authentication(api_client):
    claim_id = make_claim()
    assert api_client.get(f"/api/workbench/claims/{claim_id}/notes").status_code == 401
    assert api_client.post(f"/api/workbench/claims/{claim_id}/notes", json={"body": "x"}).status_code in (401, 403)


def test_notes_reject_customers(api_client, make_authenticated_user):
    claim_id = make_claim()
    headers, csrf, _ = make_authenticated_user(api_client, role="customer")
    assert api_client.get(
        f"/api/workbench/claims/{claim_id}/notes", headers=headers
    ).status_code == 403
    assert api_client.post(
        f"/api/workbench/claims/{claim_id}/notes",
        json={"body": "customer note"},
        headers={**headers, "X-CSRF-Token": csrf},
    ).status_code == 403


def test_unknown_claim_returns_404(adjuster):
    assert _get_notes(adjuster, "CLM-00000000-404").status_code == 404
    assert _post_note(adjuster, "CLM-00000000-404", "hi").status_code == 404


def test_blank_after_sanitization_is_422(adjuster):
    claim_id = make_claim()
    assert _post_note(adjuster, claim_id, "   \x07\x1b[0m  ").status_code == 422


def test_oversized_body_is_422(adjuster):
    claim_id = make_claim()
    assert _post_note(adjuster, claim_id, "x" * (NOTE_MAX_LENGTH + 1)).status_code == 422


def test_mention_fans_out_one_bell_to_named_adjuster(adjuster, api_client, make_authenticated_user):
    headers_b, csrf_b, email_b = make_authenticated_user(api_client, role="adjuster")
    claim_id = make_claim()
    handle_b = email_b.split("@", 1)[0]

    response = _post_note(adjuster, claim_id, f"@{handle_b} please double-check the estimate")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["mentions"] == [handle_b]

    bell = asyncio.run(database.notifications_col.find_one({"note_id": created["id"]}))
    assert bell is not None
    assert bell["recipient_email"] == email_b
    assert "double-check" not in (bell["title"] + bell["body"])

    # The named adjuster sees the bell; the author does not.
    theirs = api_client.get("/api/notifications", headers=headers_b).json()
    assert theirs["unreadCount"] == 1
    assert theirs["notifications"][0]["claimId"] == claim_id
    mine = api_client.get("/api/notifications", headers=adjuster["headers"]).json()
    assert mine["unreadCount"] == 0


def test_second_mention_on_same_claim_does_not_collide(adjuster, api_client, make_authenticated_user):
    headers_b, csrf_b, email_b = make_authenticated_user(api_client, role="adjuster")
    claim_id = make_claim()
    handle_b = email_b.split("@", 1)[0]
    assert _post_note(adjuster, claim_id, f"@{handle_b} first look").status_code == 201
    assert _post_note(adjuster, claim_id, f"@{handle_b} second look").status_code == 201
    bells = asyncio.run(
        database.notifications_col.find({"recipient_email": email_b}).to_list(100)
    )
    assert len(bells) == 2


def test_note_create_is_audited(adjuster):
    claim_id = make_claim()
    created = _post_note(adjuster, claim_id, "Audit me").json()
    audit = adjuster["client"].get(
        f"/api/workbench/claims/{claim_id}/audit", headers=adjuster["headers"]
    )
    assert audit.status_code == 200
    entry = next(e for e in audit.json() if e.get("id") == created["auditEntryId"])
    assert entry["action"] == "note"
    assert entry["actor_email"] == adjuster["email"]
    assert entry["after"]["noteId"] == created["id"]


def test_notes_list_is_oldest_first(adjuster):
    claim_id = make_claim()
    first = _post_note(adjuster, claim_id, "first").json()
    second = _post_note(adjuster, claim_id, "second").json()
    listed = _get_notes(adjuster, claim_id).json()
    assert [n["id"] for n in listed["notes"]] == [first["id"], second["id"]]


# ---- Customer invisibility (AC-11.2 / F6 coordination) ----


def test_public_projection_never_carries_note_fields():
    claim = {
        "id": "CLM-20240101-000009",
        "holder_name": "Alex Rivera",
        "status": "in_review",
        "internal_notes": ["SECRET-NOTE-TEXT adjuster-only"],
        "note_drafts": [{"body": "SECRET-NOTE-TEXT"}],
        "claim_notes": [{"body": "SECRET-NOTE-TEXT"}],
        "agent_trace": {"notes": {"body": "SECRET-NOTE-TEXT"}, "decision": {"verdict": "approved"}},
    }
    events = [
        {"type": "note_added", "payload": {"body": "SECRET-NOTE-TEXT"}, "seq": 1},
        {"type": "intake_completed", "payload": {}, "seq": 2},
    ]
    payload = public_status_payload(claim, events)
    assert "SECRET-NOTE-TEXT" not in json.dumps(payload)
    assert not [k for k in payload if "note" in k.lower()]
