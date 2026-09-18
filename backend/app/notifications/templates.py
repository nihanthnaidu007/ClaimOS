"""Pre-written customer email templates (F1).

Every email a customer receives is rendered from the fixed copy in this
module — pipeline events and LLM/agent output never reach an email body.
Each renderer returns both MIME parts (plain text and HTML) with the same
content; user-supplied values are HTML-escaped in the HTML part only.

These are pure functions: no I/O, no settings — trivially unit-testable.
"""

import html
from dataclasses import dataclass

# Max length of a quoted message snippet in the reply-notice email; longer
# messages are truncated to a preview (the full text stays in the portal).
_REPLY_SNIPPET_MAX = 280


@dataclass(frozen=True)
class RenderedEmail:
    """One rendered email: subject plus both MIME parts."""

    subject: str
    text: str
    html: str


_MILESTONE_SUBJECTS = {
    "submitted": "Claim {claim} received",
    "documents_received": "Documents received for claim {claim}",
    "decision_ready": "A decision is ready for claim {claim}",
    "payout_recorded": "Payout recorded for claim {claim}",
}

_MILESTONE_TEXT = {
    "submitted": (
        "Hello,\n\n"
        "We have received your claim {claim} and it is now in review.\n\n"
        "You can follow its progress on the ClaimOS status page at any time using "
        "your claim number and access code."
    ),
    "documents_received": (
        "Hello,\n\n"
        "The documents for claim {claim} have been received and analyzed. "
        "The next stage of the review has begun.\n\n"
        "You can follow its progress on the ClaimOS status page at any time using "
        "your claim number and access code."
    ),
    "decision_ready": (
        "Hello,\n\n"
        "A decision has been reached for claim {claim}. Open the ClaimOS status page "
        "to view your decision letter.\n\n"
        "Sign in with your claim number and access code."
    ),
    "payout_recorded": (
        "Hello,\n\n"
        "A payout has been recorded for claim {claim}. Depending on your bank, it can "
        "take a few business days to appear on your statement.\n\n"
        "You can see the details on the ClaimOS status page using your claim number "
        "and access code."
    ),
}

_MILESTONE_HTML = {
    "submitted": "<p>We have received your claim {claim} and it is now in review.</p>",
    "documents_received": (
        "<p>The documents for claim {claim} have been received and analyzed. "
        "The next stage of the review has begun.</p>"
    ),
    "decision_ready": (
        "<p>A decision has been reached for claim {claim}. Open the ClaimOS status page "
        "to view your decision letter.</p>"
    ),
    "payout_recorded": (
        "<p>A payout has been recorded for claim {claim}. Depending on your bank, it can "
        "take a few business days to appear on your statement.</p>"
    ),
}

_HTML_SHELL = """\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:24px;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;color:#1a202c;">
    <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:24px;">
      <p style="margin:0 0 16px;font-size:14px;font-weight:bold;color:#4a5568;">ClaimOS</p>
      {body}
      <p style="margin:24px 0 0;font-size:12px;color:#718096;">
        You are receiving this message because you have a claim with ClaimOS.
        Sign in on the status page with your claim number and access code.
      </p>
    </div>
  </body>
</html>
"""


def _wrap_html(body_paragraphs: str) -> str:
    return _HTML_SHELL.format(body=body_paragraphs)


def render_milestone_email(milestone: str, claim_number: str) -> RenderedEmail:
    """Milestone email (submitted / documents_received / decision_ready /
    payout_recorded). Claim numbers are system-generated (`CLM-...`, digits
    only), so .format interpolation is safe; the result is still escaped."""
    if milestone not in _MILESTONE_SUBJECTS:
        raise ValueError(f"unknown email template: {milestone}")
    safe_claim = html.escape(claim_number)
    return RenderedEmail(
        subject=_MILESTONE_SUBJECTS[milestone].format(claim=claim_number),
        text=_MILESTONE_TEXT[milestone].format(claim=claim_number),
        html=_wrap_html(_MILESTONE_HTML[milestone].format(claim=safe_claim)),
    )


def render_access_code_email(claim_number: str, holder_name: str, access_code: str) -> RenderedEmail:
    """Access-code delivery at FNOL time and on recovery (AC-1.2, AC-1.3)."""
    name = (holder_name or "").strip()
    first = name.split()[0] if name else ""
    greeting_text = f"Hi {first}," if first else "Hello,"
    greeting_html = f"<p>Hi {html.escape(first)},</p>" if first else "<p>Hello,</p>"
    safe_claim = html.escape(claim_number)
    safe_code = html.escape(access_code)
    return RenderedEmail(
        subject=f"Your ClaimOS access code for claim {claim_number}",
        text=(
            f"{greeting_text}\n\n"
            f"Your ClaimOS status-portal access code for claim {claim_number} is:\n\n"
            f"    {access_code}\n\n"
            "Keep this email. You will need the claim number and this code to open your "
            "claim's status page. If you lose the code, use the recovery form on the "
            "status page and a new code email will be sent to this address."
        ),
        html=_wrap_html(
            f"{greeting_html}"
            f"<p>Your ClaimOS status-portal access code for claim {safe_claim} is:</p>"
            f"<p style=\"font-size:20px;font-weight:bold;letter-spacing:1px;\">{safe_code}</p>"
            "<p>Keep this email. You will need the claim number and this code to open your "
            "claim's status page. If you lose the code, use the recovery form on the status "
            "page and a new code email will be sent to this address.</p>"
        ),
    )


def render_reply_notice_email(claim_number: str, sender_name: str, snippet: str) -> RenderedEmail:
    """New-message notice for the adjuster-customer thread (F5 rides the F1
    driver). Only a truncated preview is quoted; the thread lives in the app."""
    sender = (sender_name or "").strip() or "The claims team"
    short = " ".join((snippet or "").split())[:_REPLY_SNIPPET_MAX]
    safe_claim = html.escape(claim_number)
    return RenderedEmail(
        subject=f"New message about your claim {claim_number}",
        text=(
            f"Hello,\n\n"
            f"{sender} sent you a message about claim {claim_number}:\n\n"
            f"    \"{short}\"\n\n"
            "Open the page where the conversation started to read and reply — replies "
            "there keep the exchange on the record."
        ),
        html=_wrap_html(
            f"<p>{html.escape(sender)} sent you a message about claim {safe_claim}:</p>"
            f"<blockquote style=\"margin:0 0 16px;padding:8px 16px;border-left:3px solid #e2e8f0;\">"
            f"<p style=\"margin:0;\">&ldquo;{html.escape(short)}&rdquo;</p></blockquote>"
            "<p>Open the page where the conversation started to read and reply — replies "
            "there keep the exchange on the record.</p>"
        ),
    )


def render_email(
    template_key: str,
    *,
    claim_number: str = "",
    holder_name: str = "",
    access_code: str = "",
    sender_name: str = "",
    snippet: str = "",
) -> RenderedEmail:
    """Dispatch a template key (a milestone key, or access_code / reply_notice)
    to its renderer. The SMTP driver resolves the key off a Notification, so
    unknown keys raise — callers degrade, they never guess copy."""
    if template_key == "access_code":
        return render_access_code_email(claim_number, holder_name, access_code)
    if template_key == "reply_notice":
        return render_reply_notice_email(claim_number, sender_name, snippet)
    return render_milestone_email(template_key, claim_number)
