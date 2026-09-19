"""Decision-letter templates with merge variables (spec F13).

A letter template is a subject + body pair whose text may contain
``{{variable}}`` slots. Rendering substitutes every slot from a per-claim
merge context; a slot whose variable has no value renders as an empty
string and raises a warning -- raw ``{{...}}`` braces must never survive
into a customer-facing letter.

The default template is the pipeline's current decision letter captured
at the variable slots (fixture-approved variant), seeded at startup so a
fresh deployment and a twenty-PR-old deployment both have it. Rendered
with the fixture claim's context it is byte-equivalent to the letter the
decision stage writes today -- the AC-13.1 parity regression pins this.

Collections are read through the `database` module at call time (not
imported at module load) so test patching and multi-process deployments
both bind correctly -- the same pattern app.workbench_routes uses.
"""

import re
from datetime import datetime, timezone
from typing import TypedDict

import database


class RenderedLetter(TypedDict):
    """Render result: merged text plus the problems render hit, if any."""

    subject: str
    body: str
    warnings: list[str]


DEFAULT_TEMPLATE_ID = "ltpl_default_decision"

# The six spec-required merge variables. The context builder also supplies
# incident_type/incident_date: the current decision letter references the
# incident, so the default template cannot reproduce it byte-for-byte
# without them (AC-13.1). Everything here is plain display text.
MERGE_VARIABLES: dict[str, str] = {
    "claim_number": "Public claim identifier, e.g. CLM-20260918-001.",
    "customer_name": "Policyholder name resolved for the claim.",
    "policy_number": "Policy number on the claim.",
    "decision": "Raw pipeline verdict: approved | rejected | under_review.",
    "amount": "Payout formatted as currency, e.g. $1,500.00.",
    "today": "Render date (UTC, YYYY-MM-DD).",
    "incident_type": "Incident type as displayed (underscores become spaces).",
    "incident_date": "Incident date as submitted.",
}

# Well-formed slot: {{ name }} with a valid identifier inside. Spaces are
# tolerated around the name; newlines are not (a newline inside braces is
# prose, and the residual sweep below still catches it).
_MERGE_VAR_RE = re.compile(r"\{\{[ \t]*([a-zA-Z_][a-zA-Z0-9_]*)[ \t]*\}\}")
# Anything brace-paired that the variable pass could not interpret --
# {{}}, {{two words}} -- is swept so no brace artifact survives, and a
# trailing unclosed {{... is swept line-wise for the same reason.
_RESIDUAL_BRACE_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_UNCLOSED_TAIL_RE = re.compile(r"\{\{[^\n]*\Z")


def render_template_text(text: str, context: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``{{variable}}`` slots in one template string.

    Returns the rendered text and the list of problems encountered
    (unknown or malformed slots, deduplicated in first-seen order).
    Unknown variables render empty -- never as raw braces.
    """
    warnings: list[str] = []

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        value = context.get(name)
        if value is None:
            warnings.append(f"unknown_variable:{name}")
            return ""
        return str(value)

    rendered = _MERGE_VAR_RE.sub(_sub, text)
    leftovers = _RESIDUAL_BRACE_RE.findall(rendered)
    if leftovers:
        warnings.append(f"malformed_merge_slot:{','.join(leftovers)}")
        rendered = _RESIDUAL_BRACE_RE.sub("", rendered)
    tails = _UNCLOSED_TAIL_RE.findall(rendered)
    if tails:
        warnings.append(f"malformed_merge_slot:{','.join(tails)}")
        rendered = _UNCLOSED_TAIL_RE.sub("", rendered)
    return rendered, list(dict.fromkeys(warnings))


def render_letter(
    subject: str, body: str, context: dict[str, str]
) -> "RenderedLetter":
    """Render a full letter (subject + body) against one merge context."""
    rendered_subject, subject_warnings = render_template_text(subject, context)
    rendered_body, body_warnings = render_template_text(body, context)
    return {
        "subject": rendered_subject,
        "body": rendered_body,
        "warnings": list(dict.fromkeys(subject_warnings + body_warnings)),
    }


def build_merge_context(
    claim: dict,
    policy_doc: dict | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Merge-variable values for one claim, from stored data only.

    Resolution mirrors the decision stage: the holder name is the policy
    record's (what today's letter greets), the amount is the enforced
    payout formatted the way the letter prints it.
    """
    trace = claim.get("agent_trace") or {}
    decision = trace.get("decision") or {}
    policy_data = (trace.get("policy") or {}).get("policyData") or {}
    normalized = (trace.get("intake") or {}).get("normalizedData") or {}

    holder = (
        policy_data.get("holder_name")
        or (policy_doc or {}).get("holder_name")
        or claim.get("holder_name")
        or "Policyholder"
    )
    try:
        payout = float(decision.get("payoutAmount") or 0)
    except (TypeError, ValueError):
        payout = 0.0

    incident_type_raw = str(
        normalized.get("incidentType") or claim.get("incident_type") or ""
    )
    return {
        "claim_number": str(claim.get("id") or ""),
        "customer_name": str(holder),
        "policy_number": str(
            claim.get("policy_number") or policy_data.get("policy_number") or ""
        ),
        "decision": str(decision.get("verdict") or ""),
        "amount": f"${payout:,.2f}",
        "today": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
        "incident_type": incident_type_raw.replace("_", " "),
        "incident_date": str(
            normalized.get("incidentDate") or claim.get("incident_date") or ""
        ),
    }


# The current decision letter (fixture-approved variant) captured at its
# variable slots -- the AC-13.1 byte-parity baseline. If the fixture letter
# or this template drifts apart, the parity test fails.
DEFAULT_LETTER_SUBJECT = "Your Claim {{claim_number}} Has Been Approved"
DEFAULT_LETTER_BODY = (
    "Dear {{customer_name}},\n\n"
    "We have completed the review of your claim {{claim_number}} for the "
    "{{incident_type}} reported on {{incident_date}}. We are pleased to "
    "confirm the claim has been approved. The payout of {{amount}}, after "
    "the policy deductible was applied, will be issued within 5-7 business "
    "days.\n\nThank you for the documentation you provided.\n\n"
    "Sincerely,\nClaimOS Claims Processing Team"
)


def default_letter_template() -> dict:
    """The seeded default template document (fixed id, ``is_default``)."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": DEFAULT_TEMPLATE_ID,
        "name": "Default decision letter",
        "description": (
            "Seeded from the pipeline's decision letter -- the byte-parity "
            "baseline (spec F13)."
        ),
        "subject": DEFAULT_LETTER_SUBJECT,
        "body": DEFAULT_LETTER_BODY,
        "is_default": True,
        "created_at": now,
        "updated_at": now,
    }


async def ensure_default_template() -> None:
    """Idempotent startup seed: the default template must always exist.

    ``$setOnInsert`` only fills the document when the fixed id is missing,
    so adjuster edits to the default template survive restarts, and
    deployments that seeded before this feature still get it (this runs
    outside the seed:v1 marker on purpose).
    """
    col = database.letter_templates_col
    await col.create_index("id", unique=True)
    await col.update_one(
        {"id": DEFAULT_TEMPLATE_ID},
        {"$setOnInsert": default_letter_template()},
        upsert=True,
    )


async def get_letter_template(template_id: str) -> dict | None:
    return await database.letter_templates_col.find_one(
        {"id": template_id}, {"_id": 0}
    )


async def list_letter_templates() -> list[dict]:
    cursor = database.letter_templates_col.find({}, {"_id": 0}).sort("created_at", 1)
    return await cursor.to_list(500)
