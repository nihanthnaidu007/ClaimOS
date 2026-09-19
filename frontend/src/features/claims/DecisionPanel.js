// Decision panel: verdict, payout, risk, eligibility checks, and the letter —
// rendered from the persisted claim record (agent_trace), never from
// transient stream state, so a reload or replay lands on the same view.
import { useState } from 'react';
import { CircleDot, CheckCircle2, XCircle, AlertTriangle, Download, Mail, Loader2 } from 'lucide-react';
import RiskGauge from '@/components/RiskGauge';
import api from '@/lib/api';
import { formatDollars } from './constants';

const VERDICT_CONFIG = {
  approved: { label: 'CLAIM APPROVED', color: 'border-[#10b981]', bg: 'bg-[#10b981]/5', textColor: 'text-[#10b981]', icon: CheckCircle2 },
  rejected: { label: 'CLAIM REJECTED', color: 'border-[#ef4444]', bg: 'bg-[#ef4444]/5', textColor: 'text-[#ef4444]', icon: XCircle },
  under_review: { label: 'UNDER REVIEW', color: 'border-[#f59e0b]', bg: 'bg-[#f59e0b]/5', textColor: 'text-[#f59e0b]', icon: AlertTriangle },
  escalate: { label: 'ESCALATED FOR REVIEW', color: 'border-[#f59e0b]', bg: 'bg-[#f59e0b]/5', textColor: 'text-[#f59e0b]', icon: AlertTriangle },
};

// Map a persisted claim status onto the verdict banner.
function verdictConfigFor(status, verdict) {
  if (VERDICT_CONFIG[verdict]) return VERDICT_CONFIG[verdict];
  if (status === 'auto_approved') return VERDICT_CONFIG.approved;
  if (status === 'escalated') return VERDICT_CONFIG.escalate;
  if (status === 'failed') return { ...VERDICT_CONFIG.rejected, label: 'PROCESSING FAILED' };
  return VERDICT_CONFIG.under_review;
}

// Base64 → Blob download, shared by every PDF export on a claim.
export async function downloadBase64Pdf(base64, filename) {
  const bytes = Uint8Array.from(atob(base64), (ch) => ch.charCodeAt(0));
  const blob = new Blob([bytes], { type: 'application/pdf' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function usePdfDownload(path, filename) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const download = async () => {
    setDownloading(true);
    setError('');
    try {
      const { data } = await api.get(path);
      await downloadBase64Pdf(data.pdf, filename);
    } catch {
      setError('PDF export failed — try again.');
    } finally {
      setDownloading(false);
    }
  };
  return { download, downloading, error };
}

export default function DecisionPanel({ claim }) {
  const trace = claim?.agent_trace || {};
  const decision = trace.decision || {};
  const eligibility = trace.eligibility || {};
  const policy = trace.policy || {};
  const intake = trace.intake || {};

  const verdict = decision.verdict || 'pending';
  const config = verdictConfigFor(claim?.status, verdict);
  const VerdictIcon = config.icon;

  const payout = decision.payoutAmount || 0;
  const riskScore = eligibility.riskScore || claim?.risk_score || 0;
  const deductible = policy.deductibleApplied || 0;

  const pdf = usePdfDownload(`/claims/${claim.id}/pdf`, `ClaimOS-${claim.id}.pdf`);
  const normalized = intake.normalizedData || {};

  return (
    <div className={`decision-panel mt-6 border-2 rounded-sm ${config.color} ${config.bg}`} data-testid="decision-panel">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[#1a1f2e]">
        <div className="flex items-center gap-3">
          <VerdictIcon className={`w-6 h-6 ${config.textColor}`} />
          <div>
            <h2 className={`text-xl font-bold uppercase tracking-tight ${config.textColor}`} style={{ fontFamily: 'Space Grotesk' }}>
              {config.label}
            </h2>
            <div className="text-xs font-mono text-[#8892a4] mt-0.5">
              {claim.id} · {claim.holder_name || 'Unknown holder'} · {(claim.incident_type || normalized.incidentType || '').replace(/_/g, ' ')} · {claim.incident_date || normalized.incidentDate || ''}
            </div>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="px-6 py-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
          {verdict === 'approved' && (
            <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-1">Payout Amount</div>
              <div className="text-2xl font-bold text-[#10b981]" style={{ fontFamily: 'JetBrains Mono' }}>
                {formatDollars(payout)}
              </div>
            </div>
          )}
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4 text-center">
            <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-1">Deductible</div>
            <div className="text-2xl font-bold text-[#8892a4]" style={{ fontFamily: 'JetBrains Mono' }}>
              {formatDollars(deductible)}
            </div>
          </div>
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4 flex justify-center">
            <RiskGauge score={riskScore} size={100} />
          </div>
        </div>

        {/* Risk factors + eligibility checks */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4">
            <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-3">Risk Factors</div>
            {(eligibility.riskFactors || []).length === 0 ? (
              <div className="text-xs text-[#8892a4] font-mono">No significant risk factors</div>
            ) : (
              <ul className="space-y-1.5">
                {eligibility.riskFactors.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs font-mono text-[#8892a4]">
                    <CircleDot className="w-3 h-3 text-[#f59e0b] mt-0.5 flex-shrink-0" />
                    {f}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4">
            <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-3">Eligibility Checks</div>
            <ul className="space-y-1.5">
              <EligibilityRow ok={policy.found} label="Policy Active" />
              <EligibilityRow ok={(policy.coverageCheck || '').includes('COVERED')} label="Incident Covered" />
              <EligibilityRow ok={policy.withinLimits} label="Within Coverage Limit" />
              <EligibilityRow
                ok={(trace.documents?.consistencyScore || 0) >= 50}
                label={`Documents Consistent (${trace.documents?.consistencyScore || 0}/100)`}
              />
            </ul>
          </div>
        </div>

        {/* Decision Letter */}
        {decision.letterBody && (
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm mb-6">
            <div className="px-4 py-3 border-b border-[#1a1f2e] bg-[#0a0c12]/50">
              <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">Decision Letter</div>
              {decision.letterSubject && <div className="text-xs text-[#8892a4] font-mono mt-0.5">{decision.letterSubject}</div>}
            </div>
            <div className="p-4">
              <pre className="text-xs font-mono text-[#8892a4] whitespace-pre-wrap leading-relaxed">{decision.letterBody}</pre>
            </div>
          </div>
        )}

        {/* Decision Citations (glass-box): driver -> source locator -> plain-language explanation */}
        {(decision.citations || []).length > 0 && (
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm mb-6" data-testid="decision-citations">
            <div className="px-4 py-3 border-b border-[#1a1f2e] bg-[#0a0c12]/50">
              <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">Decision Citations</div>
            </div>
            <ul className="p-4 space-y-2">
              {decision.citations.map((citation, i) => (
                <li key={i} className="text-xs font-mono leading-relaxed">
                  <div className="text-[#e2e8f0]">{i + 1}. {citation.fact}</div>
                  <div className="text-[#4a5568] mt-0.5">source: {citation.sourceRef}</div>
                  {citation.customerFriendlyExplanation && (
                    <div className="text-[#8892a4] mt-0.5">{citation.customerFriendlyExplanation}</div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Next steps */}
        {(decision.nextSteps || []).length > 0 && (
          <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-4 mb-6">
            <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-2">Next Steps</div>
            <ol className="space-y-1">
              {decision.nextSteps.map((step, i) => (
                <li key={i} className="text-xs font-mono text-[#8892a4] flex items-start gap-2">
                  <span className="text-[#3b82f6] font-bold">{i + 1}.</span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            data-testid="download-pdf-btn"
            onClick={pdf.download}
            disabled={pdf.downloading}
            className="flex items-center gap-2 bg-[#141820] border border-[#232b3d] hover:bg-[#1a1f2e] text-[#e2e8f0] rounded-none text-sm px-4 py-2 font-mono transition-colors duration-200 disabled:opacity-40"
          >
            {pdf.downloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} PDF
          </button>
          <div className="flex items-center gap-2 text-xs font-mono text-[#4a5568]">
            <Mail className="w-3.5 h-3.5" />
            Email: {decision.emailSent ? 'Sent' : 'Not sent (mocked)'}
          </div>
          {pdf.error && <span className="text-xs font-mono text-[#ef4444]">{pdf.error}</span>}
        </div>
      </div>
    </div>
  );
}

function EligibilityRow({ ok, label }) {
  return (
    <li className="flex items-center gap-2 text-xs font-mono">
      {ok
        ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981]" />
        : <XCircle className="w-3.5 h-3.5 text-[#ef4444]" />}
      <span className="text-[#8892a4]">{label}</span>
    </li>
  );
}
