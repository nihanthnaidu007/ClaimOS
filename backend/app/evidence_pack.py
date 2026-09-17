"""Evidence-pack PDF builder: the full adjudication record for one claim.

Bundles the claim summary, every agent's persisted trace (findings, output,
tool calls, errors), the final decision letter, and the durable event log
appendix into a single downloadable PDF. Deterministic — no LLM is involved;
the pack is a faithful rendering of what the pipeline already stored.
"""

import base64
import io
from datetime import datetime, timezone

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Core fpdf fonts are latin-1 only; LLM output can contain any unicode, so
# every string is normalized before it reaches the renderer.
_LATIN1 = "latin-1"


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"
    text = str(value).replace("\r", "")
    return text.encode(_LATIN1, "replace").decode(_LATIN1)


class EvidencePackPDF(FPDF):
    """Same visual language as the decision letter (dark header, Helvetica)."""

    def __init__(self, claim: dict, events: list[dict], runs: list[dict]):
        super().__init__()
        self.claim = claim
        self.events = events
        self.runs = runs
        self.set_auto_page_break(auto=True, margin=20)

    # -- chrome ------------------------------------------------------------
    def header(self):
        self.set_fill_color(10, 12, 18)
        self.rect(0, 0, 210, 22, "F")
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(226, 232, 240)
        self.set_xy(10, 6)
        self.cell(0, 10, "ClaimOS", 0, 0, "L")
        self.set_font("Helvetica", "", 9)
        self.set_xy(10, 6)
        self.cell(0, 10, "EVIDENCE PACK", 0, 0, "R")
        self.set_fill_color(20, 24, 32)
        self.rect(0, 22, 210, 10, "F")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(136, 146, 164)
        self.set_xy(10, 24)
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.cell(
            0, 6,
            f"Claim: {self.claim.get('id', 'N/A')}  |  Generated: {generated}  |  CONFIDENTIAL",
            0, 0, "L",
        )
        self.ln(18)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(74, 85, 104)
        self.cell(
            0, 10,
            f"ClaimOS Evidence Pack  |  Confidential  |  Page {self.page_no()}",
            0, 0, "C",
        )

    # -- sections ------------------------------------------------------------
    def section_title(self, title: str):
        if self.get_y() > 250:
            self.add_page()
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 30, 30)
        self.cell(0, 8, f"  {title}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        self.ln(1)

    def field_line(self, label: str, value: str):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(50, 50, 50)
        # fpdf2 leaves multi_cell's cursor at the right margin by default —
        # without an explicit reset the next full-width cell has no space.
        self.multi_cell(0, 5, _clean(f"{label}: {value}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def kv_dict(self, data: dict, indent: str = "  "):
        for key, value in data.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, dict):
                self.field_line(f"{indent}{key}", "")
                self.kv_dict(value, indent=f"{indent}  ")
            elif isinstance(value, list):
                self.field_line(f"{indent}{key}", ", ".join(_clean(v) for v in value[:8]))
            else:
                self.field_line(f"{indent}{key}", value)

    def render(self) -> bytes:
        self.add_page()
        self._cover()
        self._agent_traces()
        self._decision()
        self._run_history()
        self._event_log()
        buf = io.BytesIO()
        self.output(buf)
        return buf.getvalue()

    def _cover(self):
        claim = self.claim
        trace = claim.get("agent_trace", {}) or {}
        intake = (trace.get("intake", {}) or {}).get("normalizedData", {}) or {}
        status = _clean(claim.get("status", "unknown")).upper()
        self.section_title("CLAIM SUMMARY")
        self.field_line("Claim ID", claim.get("id", ""))
        self.field_line("Status", status)
        self.field_line("Policy number", claim.get("policy_number", ""))
        self.field_line("Policyholder", claim.get("holder_name", "") or intake.get("holderName", ""))
        self.field_line("Incident type", claim.get("incident_type", "") or intake.get("incidentType", ""))
        self.field_line("Incident date", claim.get("incident_date", "") or intake.get("incidentDate", ""))
        self.field_line("Claimed amount", f"${claim.get('claimed_amount', 0):,.2f}")
        self.field_line("Risk score", f"{claim.get('risk_score', 0)}/100")
        if claim.get("failure_reason"):
            self.field_line("Failure reason", claim["failure_reason"])
        if claim.get("escalation_reason"):
            self.field_line("Escalation reason", claim["escalation_reason"])

    _AGENT_ORDER = ("intake", "policy", "documents", "eligibility", "decision")

    def _agent_traces(self):
        trace = self.claim.get("agent_trace", {}) or {}
        logs = {log.get("agent"): log for log in self.claim.get("agent_logs", []) or []}
        self.section_title("AGENT TRACES")
        if not trace and not logs:
            self.field_line("Agents", "No agent trace recorded for this claim.")
            return
        for name in self._AGENT_ORDER:
            agent_trace = trace.get(name) or {}
            log = logs.get(name, {})
            label = log.get("label") or name.replace("_", " ").title()
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(30, 30, 30)
            self.cell(0, 7, f"{label}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            parts = []
            if log.get("status"):
                parts.append(f"status: {log['status']}")
            if log.get("confidence") is not None:
                parts.append(f"confidence: {float(log['confidence']):.0%}")
            if log.get("duration"):
                parts.append(f"duration: {log['duration']}ms")
            if parts:
                self.set_font("Helvetica", "I", 8)
                self.set_text_color(100, 100, 100)
                self.cell(0, 5, _clean("  |  ".join(parts)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if log.get("error"):
                self.field_line("Error", log["error"])
            if agent_trace:
                self.kv_dict(agent_trace)
            self.ln(1)

    def _decision(self):
        trace = self.claim.get("agent_trace", {}) or {}
        decision = trace.get("decision", {}) or {}
        letter = decision.get("letterBody", "")
        next_steps = decision.get("nextSteps", [])
        if letter:
            self.section_title("DECISION LETTER")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(50, 50, 50)
            self.multi_cell(0, 5, _clean(letter), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if next_steps:
            self.section_title("NEXT STEPS")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(50, 50, 50)
            for i, step in enumerate(next_steps, 1):
                self.multi_cell(0, 5, _clean(f"{i}. {step}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def _run_history(self):
        self.section_title("RUN HISTORY")
        if not self.runs:
            self.field_line("Runs", "No pipeline runs recorded.")
            return
        for run in self.runs:
            line = f"attempt {run.get('attempt', '?')}: {run.get('status', '?')}"
            if run.get("failure_reason"):
                line += f" — failure: {run['failure_reason']}"
            if run.get("escalation_reason"):
                line += f" — escalated: {run['escalation_reason']}"
            self.field_line("Run", line)

    _MAX_EVENT_ROWS = 120

    def _event_log(self):
        self.section_title("EVENT LOG (APPENDIX)")
        if not self.events:
            self.field_line("Events", "No durable events recorded.")
            return
        for doc in self.events[: self._MAX_EVENT_ROWS]:
            data = doc.get("data", {}) or {}
            summary = "; ".join(f"{k}={_clean(v)}" for k, v in list(data.items())[:3])
            row = f"#{doc.get('seq', '?')} {doc.get('event', '?')} — {summary}"
            self.set_font("Courier", "", 7)
            self.set_text_color(60, 60, 60)
            self.multi_cell(0, 4, _clean(row[:160]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def generate_evidence_pack(claim: dict, events: list[dict], runs: list[dict]) -> str:
    """Render the evidence pack and return it as a base64 string."""
    pdf = EvidencePackPDF(claim, events, runs)
    return base64.b64encode(pdf.render()).decode("utf-8")
