import base64
import io
from fpdf import FPDF
from datetime import datetime, timezone


class ClaimPDF(FPDF):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.set_auto_page_break(auto=True, margin=25)
    
    def header(self):
        # Dark header bar
        self.set_fill_color(10, 12, 18)
        self.rect(0, 0, 210, 22, 'F')
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(226, 232, 240)
        self.set_xy(10, 6)
        self.cell(0, 10, 'ClaimOS', 0, 0, 'L')
        self.set_font('Helvetica', '', 9)
        self.set_xy(10, 6)
        self.cell(0, 10, 'OFFICIAL CLAIM DECISION', 0, 0, 'R')
        # Sub header
        self.set_fill_color(20, 24, 32)
        self.rect(0, 22, 210, 10, 'F')
        self.set_font('Helvetica', '', 8)
        self.set_text_color(136, 146, 164)
        self.set_xy(10, 24)
        claim_id = self.state.get('claimId', 'N/A')
        policy_num = self.state.get('input', {}).get('policyNumber', 'N/A')
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        self.cell(0, 6, f'Claim: {claim_id}  |  Policy: {policy_num}  |  Generated: {date_str}  |  CONFIDENTIAL', 0, 0, 'L')
        self.ln(18)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', '', 7)
        self.set_text_color(74, 85, 104)
        self.cell(0, 10, f'ClaimOS Agentic Claims Processing  |  Confidential  |  Page {self.page_no()}  |  Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}', 0, 0, 'C')


def generate_claim_pdf(state):
    """Generate a PDF decision letter and return as base64 string."""
    pdf = ClaimPDF(state)
    pdf.add_page()
    
    decision = state.get('decision', {})
    eligibility = state.get('eligibility', {})
    policy = state.get('policy', {})
    intake = state.get('intake', {})
    
    verdict = decision.get('verdict', 'pending').upper()
    payout = decision.get('payoutAmount', 0)
    risk_score = eligibility.get('riskScore', 0)
    
    # Verdict box
    if verdict == 'APPROVED':
        r, g, b = 16, 185, 129
    elif verdict == 'REJECTED':
        r, g, b = 239, 68, 68
    else:
        r, g, b = 245, 158, 11
    
    pdf.set_fill_color(r, g, b)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(0, 14, f'  {verdict}', 1, 1, 'L', True)
    pdf.ln(4)
    
    # Key metrics row
    pdf.set_text_color(30, 30, 30)
    pdf.set_font('Helvetica', '', 9)
    
    holder_name = policy.get('policyData', {}).get('holder_name', 'N/A')
    incident_type = intake.get('normalizedData', {}).get('incidentType', 'N/A')
    claimed_amount = intake.get('normalizedData', {}).get('claimedAmount', 0)
    
    pdf.cell(63, 8, f'Policyholder: {holder_name}', 0, 0)
    pdf.cell(63, 8, f'Incident: {incident_type}', 0, 0)
    pdf.cell(63, 8, f'Claimed: ${claimed_amount:,.2f}', 0, 1)
    
    if verdict == 'APPROVED':
        pdf.set_font('Helvetica', 'B', 10)
        deductible = policy.get('deductibleApplied', 0)
        pdf.cell(63, 8, f'Payout: ${payout:,.2f}', 0, 0)
        pdf.cell(63, 8, f'Deductible: ${deductible:,.2f}', 0, 0)
        pdf.cell(63, 8, f'Risk Score: {risk_score}/100', 0, 1)
    else:
        pdf.cell(63, 8, f'Risk Score: {risk_score}/100', 0, 1)
    
    pdf.ln(4)
    
    # Risk factors
    risk_factors = eligibility.get('riskFactors', [])
    if risk_factors:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 8, 'RISK FACTORS', 0, 1)
        pdf.set_font('Helvetica', '', 9)
        for factor in risk_factors[:5]:
            pdf.cell(0, 6, f'  - {factor[:80]}', 0, 1)
        pdf.ln(3)
    
    # Decision Letter
    letter_body = decision.get('letterBody', '')
    if letter_body:
        pdf.set_font('Helvetica', 'B', 11)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 8, 'DECISION LETTER', 0, 1)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font('Helvetica', '', 9)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(0, 5, letter_body)
        pdf.ln(4)
    
    # Decision Citations (glass-box): driver -> source locator -> explanation
    citations = decision.get('citations', [])
    if citations:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 8, 'DECISION CITATIONS', 0, 1)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font('Helvetica', '', 8)
        for index, citation in enumerate(citations[:10], start=1):
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, 5, f"{index}. {citation.get('fact', '')}")
            pdf.set_text_color(110, 110, 110)
            pdf.multi_cell(0, 4, f"   source: {citation.get('sourceRef', '')}")
            explanation = citation.get('customerFriendlyExplanation', '')
            if explanation:
                pdf.multi_cell(0, 4, f"   {explanation}")
            pdf.ln(1)
        pdf.ln(3)

    # Next steps
    next_steps = decision.get('nextSteps', [])
    if next_steps:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 8, 'NEXT STEPS', 0, 1)
        pdf.set_font('Helvetica', '', 9)
        for i, step in enumerate(next_steps, 1):
            pdf.multi_cell(0, 5, f'{i}. {step}')
    
    # Output as base64
    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')
