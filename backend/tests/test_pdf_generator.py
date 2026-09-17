"""Evidence-pack PDF rendering — real fpdf layout, no live API or Mongo.

The Decision Citations section renders model-produced text through fpdf2.
fpdf regressions are layout- and encoding-shaped (multi_cell cursor drift,
Latin-1-only core fonts), so these tests run the real generator and assert
on the decoded PDF bytes.
"""

import base64

from pdf_generator import _latin1_safe, generate_claim_pdf


def _state_with_citations():
    """Pipeline-shaped state: the fields the PDF body reads, with the citation
    and letter text as the model actually writes it (curly apostrophe,
    em-dash — both outside the Latin-1 range of fpdf's core fonts)."""
    return {
        "claimId": "CLM-PDF-1",
        "input": {"policyNumber": "AUTO-2026-009999", "holderName": "Sarah Chen"},
        "intake": {
            "normalizedData": {
                "incidentType": "accident",
                "claimedAmount": 3200.0,
            }
        },
        "policy": {"policyData": {"holder_name": "Sarah Chen"}, "deductibleApplied": 500.0},
        "eligibility": {"riskScore": 5, "riskFactors": ["late-night incident — unverified"]},
        "decision": {
            "verdict": "approved",
            "payoutAmount": 2700.0,
            "summary": "Approved: active coverage, within limits.",
            "letterBody": "Dear Ms. Chen, we've approved your claim — payment follows shortly.",
            "nextSteps": [
                "We've issued payment to your account — expect it within 5 business days.",
                "Keep your repair receipts in case of supplementary claims.",
            ],
            "citations": [
                {
                    "fact": "POLICY: status=active, adjustedPayout=2700.0",
                    "sourceRef": "policy.status; policy.adjustedPayout",
                    "customerFriendlyExplanation": (
                        "Your policy was active on the date of your accident — "
                        "your coverage entitles you to $2,700.00 after your deductible."
                    ),
                },
                {
                    "fact": "DOCUMENTS: consistency=consistent, redFlags=[]",
                    "sourceRef": "documents.consistency; documents.redFlags",
                    "customerFriendlyExplanation": (
                        "The documents you submitted matched the details of your claim."
                    ),
                },
            ],
        },
    }


def test_latin1_safe_transliterates_typographic_punctuation():
    assert _latin1_safe("we've — approved") == "we've - approved"
    assert _latin1_safe("\u201cquoted\u201d \u2026 done\u00a0now") == '"quoted" ... done now'
    # Already-safe text passes through byte-identical.
    assert _latin1_safe("plain text 123") == "plain text 123"


def test_pdf_renders_decision_citations():
    """The glass-box citations section must render model text containing
    typographic punctuation without fpdf layout or encoding failures, and the
    PDF must be well-formed."""
    raw = base64.b64decode(generate_claim_pdf(_state_with_citations()))
    assert raw[:5] == b"%PDF-"
    assert len(raw) > 1000


def test_pdf_without_citations_still_renders():
    """Citations are optional (pre-upgrade claims have none)."""
    state = _state_with_citations()
    state["decision"]["citations"] = []

    raw = base64.b64decode(generate_claim_pdf(state))
    assert raw[:5] == b"%PDF-"
