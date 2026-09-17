import { useState, useEffect, useRef, useCallback } from 'react';
import { Play, CircleDot, CheckCircle2, XCircle, AlertTriangle, Clock, Wrench, ChevronDown, ChevronUp, Download, Mail, FilePlus, Search, Loader2 } from 'lucide-react';
import axios from 'axios';
import RiskGauge from './RiskGauge';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;
const BACKEND_URL = import.meta.env.VITE_API_BASE_URL;

const formatDollars = (n) => {
  if (n == null || isNaN(n)) return '$0.00';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const INCIDENT_TYPES = [
  'accident', 'theft', 'vandalism', 'weather_damage', 'fire', 'flood',
  'earthquake', 'wind_damage', 'hit_and_run', 'total_loss',
  'accidental_damage', 'malfunction', 'hospitalization', 'surgery',
  'emergency', 'specialist_visit', 'dental', 'vision'
];

const AGENT_META = {
  INTAKE_AGENT: { index: 1, label: 'Intake & Validation', desc: 'Validating claim fields and normalizing data', icon: '01' },
  POLICY_AGENT: { index: 2, label: 'Policy Verification', desc: 'Querying policy database for coverage verification', icon: '02' },
  DOCUMENT_AGENT: { index: 3, label: 'Document Analysis', desc: 'Analyzing claim documents for evidence and consistency', icon: '03' },
  ELIGIBILITY_AGENT: { index: 4, label: 'Eligibility & Risk', desc: 'Calculating risk score and eligibility verdict', icon: '04' },
  DECISION_AGENT: { index: 5, label: 'Decision & Communication', desc: 'Issuing final verdict and drafting communication', icon: '05' },
};

const AGENT_ORDER = ['INTAKE_AGENT', 'POLICY_AGENT', 'DOCUMENT_AGENT', 'ELIGIBILITY_AGENT', 'DECISION_AGENT'];

// ============ FORM COMPONENT ============
function ClaimForm({ onSubmit, submitting }) {
  const [form, setForm] = useState({
    policyNumber: '', incidentDate: '', incidentType: '', claimedAmount: '',
    description: '', contactEmail: '', documentText: ''
  });
  const [policyInfo, setPolicyInfo] = useState(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState('');

  const handleChange = (field, value) => setForm(prev => ({ ...prev, [field]: value }));

  const lookupPolicy = async () => {
    if (!form.policyNumber.trim()) return;
    setLookupLoading(true);
    setLookupError('');
    setPolicyInfo(null);
    try {
      const res = await axios.get(`${API}/policies/lookup?policy_number=${encodeURIComponent(form.policyNumber)}`);
      setPolicyInfo(res.data);
      if (res.data.holder_email) handleChange('contactEmail', res.data.holder_email);
    } catch {
      setLookupError('Policy not found');
    } finally {
      setLookupLoading(false);
    }
  };

  const canSubmit = form.policyNumber && form.incidentDate && form.incidentType && form.claimedAmount && form.description.length >= 20;

  return (
    <div className="page-enter" data-testid="claim-form">
      <div className="flex items-center gap-3 mb-6">
        <FilePlus className="w-5 h-5 text-[#3b82f6]" />
        <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
          New Claim Submission
        </h1>
      </div>

      <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm">
        <div className="border-b border-[#1a1f2e] px-5 py-3 bg-[#0a0c12]/50">
          <span className="text-xs uppercase tracking-[0.15em] text-[#8892a4] font-mono">Claim Details</span>
        </div>

        <div className="p-5 space-y-5">
          {/* Policy Lookup */}
          <div>
            <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Policy Number</label>
            <div className="flex gap-2">
              <input
                data-testid="input-policy-number"
                type="text"
                value={form.policyNumber}
                onChange={(e) => handleChange('policyNumber', e.target.value)}
                placeholder="AUTO-2024-001847"
                className="flex-1 bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none"
              />
              <button
                data-testid="lookup-policy-btn"
                onClick={lookupPolicy}
                disabled={lookupLoading || !form.policyNumber.trim()}
                className="bg-[#141820] border border-[#232b3d] hover:bg-[#1a1f2e] text-[#e2e8f0] rounded-none text-sm px-4 py-2 font-mono uppercase tracking-wide disabled:opacity-40 transition-colors duration-200 flex items-center gap-2"
              >
                {lookupLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                LOOKUP
              </button>
            </div>
            {lookupError && <div className="text-xs text-[#ef4444] mt-1.5 font-mono">{lookupError}</div>}
            {policyInfo && (
              <div data-testid="policy-lookup-result" className="mt-3 bg-[#0a0c12] border border-[#1a1f2e] rounded-sm p-3 flex items-center gap-4">
                <div className="flex-1">
                  <div className="text-sm font-medium text-[#e2e8f0]">{policyInfo.holder_name}</div>
                  <div className="text-xs text-[#8892a4] font-mono">{policyInfo.policy_type?.toUpperCase()} · {formatDollars(policyInfo.coverage_limit)} limit</div>
                </div>
                <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${
                  policyInfo.status === 'active' ? 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30' :
                  policyInfo.status === 'expired' ? 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30' :
                  'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30'
                }`}>
                  {policyInfo.status?.toUpperCase()}
                </span>
              </div>
            )}
          </div>

          {/* 2-column grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Incident Date</label>
              <input
                data-testid="input-incident-date"
                type="date"
                value={form.incidentDate}
                onChange={(e) => handleChange('incidentDate', e.target.value)}
                className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] outline-none"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Incident Type</label>
              <select
                data-testid="input-incident-type"
                value={form.incidentType}
                onChange={(e) => handleChange('incidentType', e.target.value)}
                className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] outline-none appearance-none"
              >
                <option value="">Select type...</option>
                {INCIDENT_TYPES.map(t => (
                  <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Claimed Amount ($)</label>
              <input
                data-testid="input-claimed-amount"
                type="number"
                value={form.claimedAmount}
                onChange={(e) => handleChange('claimedAmount', e.target.value)}
                placeholder="3200.00"
                className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Contact Email</label>
              <input
                data-testid="input-contact-email"
                type="email"
                value={form.contactEmail}
                onChange={(e) => handleChange('contactEmail', e.target.value)}
                placeholder="email@example.com"
                className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none"
              />
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Incident Description</label>
            <textarea
              data-testid="input-description"
              value={form.description}
              onChange={(e) => handleChange('description', e.target.value)}
              placeholder="Describe the incident in detail (minimum 20 characters)..."
              rows={4}
              className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none resize-none"
            />
            <div className="text-[10px] text-[#4a5568] font-mono mt-1">{form.description.length} chars {form.description.length < 20 ? '(min 20)' : ''}</div>
          </div>

          {/* Document text */}
          <div>
            <label className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] font-mono block mb-1.5">Evidence / Document Text</label>
            <textarea
              data-testid="input-document-text"
              value={form.documentText}
              onChange={(e) => handleChange('documentText', e.target.value)}
              placeholder="Paste any supporting documentation, police reports, medical records, etc."
              rows={3}
              className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none px-3 py-2.5 text-sm font-mono focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none resize-none"
            />
          </div>

          {/* Submit */}
          <button
            data-testid="submit-claim-btn"
            onClick={() => onSubmit(form)}
            disabled={!canSubmit || submitting}
            className="w-full bg-[#3b82f6] hover:bg-[#3b82f6]/90 text-white rounded-none font-medium text-sm px-4 py-3 uppercase tracking-wide disabled:opacity-40 transition-colors duration-200 flex items-center justify-center gap-2"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Initiate Agentic Processing
          </button>
          <div className="text-[10px] text-[#4a5568] font-mono text-center">
            5 AI agents will process this claim in sequence · Typical processing: 15-30 seconds
          </div>
        </div>
      </div>
    </div>
  );
}

// ============ AGENT CARD ============
function AgentCard({ agentName, status, output, toolsCalled, duration, isLast }) {
  const meta = AGENT_META[agentName] || { index: '?', label: agentName, desc: '' };
  const [showTrace, setShowTrace] = useState(false);
  const [visibleTools, setVisibleTools] = useState([]);
  const toolTimerRef = useRef(null);

  // Animate tool calls appearing
  useEffect(() => {
    if (toolsCalled && toolsCalled.length > 0 && status === 'done') {
      setVisibleTools([]);
      toolsCalled.forEach((tool, i) => {
        toolTimerRef.current = setTimeout(() => {
          setVisibleTools(prev => [...prev, tool]);
        }, i * 200);
      });
    } else if (status === 'running' && toolsCalled) {
      setVisibleTools(toolsCalled);
    }
    return () => clearTimeout(toolTimerRef.current);
  }, [toolsCalled, status]);

  const statusIcon = () => {
    switch (status) {
      case 'running': return <Loader2 className="w-4 h-4 text-[#3b82f6] animate-spin" />;
      case 'done': return <CheckCircle2 className="w-4 h-4 text-[#10b981]" />;
      case 'error': return <XCircle className="w-4 h-4 text-[#ef4444]" />;
      case 'halted': return <AlertTriangle className="w-4 h-4 text-[#f59e0b]" />;
      default: return <Clock className="w-4 h-4 text-[#4a5568]" />;
    }
  };

  const borderClass = () => {
    switch (status) {
      case 'running': return 'agent-running border-[#3b82f6]';
      case 'done': return 'border-[#10b981]/50';
      case 'error': return 'border-[#ef4444]/50';
      case 'halted': return 'border-[#f59e0b]/50';
      default: return 'border-[#1a1f2e] opacity-40';
    }
  };

  const statusLabel = () => {
    switch (status) {
      case 'running': return 'RUNNING';
      case 'done': return `DONE · ${(duration / 1000).toFixed(1)}s`;
      case 'error': return 'ERROR';
      case 'halted': return 'HALTED';
      default: return 'WAITING';
    }
  };

  return (
    <div className="relative" data-testid={`agent-card-${agentName}`}>
      {/* Connector line */}
      {!isLast && (
        <div className="absolute left-[19px] top-[48px] bottom-[-16px] w-[2px] bg-[#1a1f2e] z-0" />
      )}

      <div className={`relative bg-[#0f1218] border rounded-sm overflow-hidden transition-all duration-300 ${borderClass()}`}>
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3">
          <div className={`relative z-10 flex items-center justify-center w-8 h-8 rounded-full border text-xs font-mono font-bold ${
            status === 'done' ? 'bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981]' :
            status === 'running' ? 'bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6]' :
            status === 'error' ? 'bg-[#ef4444]/10 border-[#ef4444]/30 text-[#ef4444]' :
            'bg-[#0a0c12] border-[#232b3d] text-[#4a5568]'
          }`}>
            {meta.icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-[#e2e8f0] uppercase tracking-wide" style={{ fontFamily: 'Space Grotesk' }}>
                {meta.label}
              </span>
            </div>
            <div className="text-xs text-[#4a5568] truncate">{meta.desc}</div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {statusIcon()}
            <span className={`text-[10px] font-mono uppercase tracking-wider ${
              status === 'done' ? 'text-[#10b981]' :
              status === 'running' ? 'text-[#3b82f6]' :
              status === 'error' ? 'text-[#ef4444]' :
              'text-[#4a5568]'
            }`}>
              {statusLabel()}
            </span>
          </div>
        </div>

        {/* Progress bar */}
        <div className="progress-bar-container">
          {status === 'running' && <div className="progress-bar-indeterminate" />}
          {status === 'done' && <div className="progress-bar-complete" />}
        </div>

        {/* Running state: show thinking cursor */}
        {status === 'running' && (
          <div className="px-4 py-2 border-t border-[#1a1f2e]/50">
            <div className="flex items-center gap-2 text-xs text-[#3b82f6] font-mono">
              Analyzing with Claude
              <span className="thinking-cursor" />
            </div>
            {visibleTools.map((tool, i) => (
              <div key={i} className="tool-call-line flex items-center gap-2 text-xs font-mono text-[#8892a4] mt-1">
                <Wrench className="w-3 h-3 text-[#7dd3fc]" />
                <span className="text-[#7dd3fc]">{tool.tool}</span>
                <span className="text-[#4a5568]">({tool.input})</span>
                {tool.duration_ms && <span className="text-[#10b981]">✓ {tool.duration_ms}ms</span>}
              </div>
            ))}
          </div>
        )}

        {/* Done state: summary + expandable trace */}
        {status === 'done' && output && (
          <div className="border-t border-[#1a1f2e]/50">
            {/* Summary line */}
            <div className="px-4 py-2 flex items-center gap-4 text-xs font-mono text-[#8892a4]">
              {output.valid !== undefined && <span>STATUS: <span className={output.valid ? 'text-[#10b981]' : 'text-[#ef4444]'}>{output.valid ? 'VALID' : 'INVALID'}</span></span>}
              {output.found !== undefined && <span>FOUND: <span className={output.found ? 'text-[#10b981]' : 'text-[#ef4444]'}>{output.found ? 'YES' : 'NO'}</span></span>}
              {output.consistencyScore !== undefined && <span>CONSISTENCY: <span className="text-[#7dd3fc]">{output.consistencyScore}/100</span></span>}
              {output.riskScore !== undefined && <span>RISK: <span className={output.riskScore >= 70 ? 'text-[#ef4444]' : output.riskScore >= 30 ? 'text-[#f59e0b]' : 'text-[#10b981]'}>{output.riskScore}/100</span></span>}
              {output.verdict && <span>VERDICT: <span className={output.verdict === 'approved' ? 'text-[#10b981]' : output.verdict === 'rejected' ? 'text-[#ef4444]' : 'text-[#f59e0b]'}>{output.verdict?.toUpperCase()}</span></span>}
              {visibleTools.length > 0 && <span>TOOLS: {visibleTools.length}</span>}
            </div>

            {/* Tool calls */}
            {visibleTools.length > 0 && (
              <div className="px-4 pb-2">
                {visibleTools.map((tool, i) => (
                  <div key={i} className="tool-call-line flex items-center gap-2 text-xs font-mono text-[#8892a4] mt-0.5">
                    <Wrench className="w-3 h-3 text-[#7dd3fc]" />
                    <span className="text-[#7dd3fc]">{tool.tool}</span>
                    <span className="text-[#4a5568]">("{tool.input}")</span>
                    <span className="text-[#10b981]">✓ {tool.duration_ms}ms</span>
                    <span className="text-[#4a5568]">→ {tool.output}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Reasoning trace toggle */}
            {output.reasoning && (
              <div>
                <button
                  data-testid={`toggle-trace-${agentName}`}
                  onClick={() => setShowTrace(!showTrace)}
                  className="w-full flex items-center gap-2 px-4 py-2 text-xs font-mono text-[#3b82f6] hover:text-[#60a5fa] hover:bg-[#141820] transition-colors duration-200"
                >
                  {showTrace ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {showTrace ? 'HIDE' : 'VIEW'} REASONING TRACE
                </button>
                {showTrace && (
                  <div className="px-4 pb-3 border-t border-[#1a1f2e]/50">
                    <pre className="text-[11px] font-mono text-[#8892a4] whitespace-pre-wrap leading-relaxed mt-2 bg-[#0a0c12] p-3 rounded-sm border border-[#1a1f2e] max-h-48 overflow-y-auto">
                      {output.reasoning}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Error state */}
        {status === 'error' && (
          <div className="px-4 py-2 border-t border-[#ef4444]/20 bg-[#ef4444]/5">
            <div className="text-xs font-mono text-[#ef4444]">Pipeline continuing in degraded mode</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============ PIPELINE BOARD ============
function PipelineBoard({ claimId, agents, pipelineStatus, haltReason }) {
  return (
    <div className="page-enter" data-testid="pipeline-board">
      {/* Top bar */}
      <div className="flex items-center gap-3 mb-6">
        <div className="recording-dot" />
        <span className="text-xs uppercase tracking-[0.15em] text-[#8892a4] font-mono">Agent Pipeline Active</span>
        <span className="text-xs font-mono text-[#7dd3fc]">{claimId}</span>
      </div>

      {/* Agent cards */}
      <div className="space-y-4">
        {AGENT_ORDER.map((name, idx) => (
          <AgentCard
            key={name}
            agentName={name}
            status={agents[name]?.status || 'waiting'}
            output={agents[name]?.output}
            toolsCalled={agents[name]?.toolsCalled || []}
            duration={agents[name]?.duration || 0}
            isLast={idx === AGENT_ORDER.length - 1}
          />
        ))}
      </div>

      {/* Halted banner */}
      {pipelineStatus === 'halted' && (
        <div className="mt-4 bg-[#f59e0b]/5 border-2 border-dashed border-[#f59e0b] rounded-sm p-5 text-center" data-testid="pipeline-halted">
          <AlertTriangle className="w-6 h-6 text-[#f59e0b] mx-auto mb-2" />
          <div className="text-sm font-semibold text-[#f59e0b] uppercase mb-1">Pipeline Halted</div>
          <div className="text-xs font-mono text-[#8892a4]">{haltReason}</div>
        </div>
      )}
    </div>
  );
}

// ============ DECISION PANEL ============
function DecisionPanel({ state, claimId }) {
  const decision = state?.decision || {};
  const eligibility = state?.eligibility || {};
  const policy = state?.policy || {};
  const intake = state?.intake || {};
  
  const verdict = decision.verdict || 'pending';
  const payout = decision.payoutAmount || 0;
  const riskScore = eligibility.riskScore || 0;
  const deductible = policy.deductibleApplied || 0;

  const verdictConfig = {
    approved: { label: 'CLAIM APPROVED', color: 'border-[#10b981]', bg: 'bg-[#10b981]/5', textColor: 'text-[#10b981]', icon: CheckCircle2 },
    rejected: { label: 'CLAIM REJECTED', color: 'border-[#ef4444]', bg: 'bg-[#ef4444]/5', textColor: 'text-[#ef4444]', icon: XCircle },
    under_review: { label: 'UNDER REVIEW', color: 'border-[#f59e0b]', bg: 'bg-[#f59e0b]/5', textColor: 'text-[#f59e0b]', icon: AlertTriangle },
    escalate: { label: 'ESCALATED FOR REVIEW', color: 'border-[#f59e0b]', bg: 'bg-[#f59e0b]/5', textColor: 'text-[#f59e0b]', icon: AlertTriangle },
  };

  const config = verdictConfig[verdict] || verdictConfig.under_review;
  const VerdictIcon = config.icon;

  const downloadPdf = async () => {
    try {
      const res = await axios.get(`${API}/claims/${claimId}/pdf`);
      const bytes = atob(res.data.pdf);
      const arr = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
      const blob = new Blob([arr], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ClaimOS-${claimId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('PDF download failed:', e);
    }
  };

  const holderName = policy.policyData?.holder_name || '';
  const incidentType = intake.normalizedData?.incidentType || '';
  const incidentDate = intake.normalizedData?.incidentDate || '';

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
              {claimId} · {holderName} · {incidentType?.replace(/_/g, ' ')} · {incidentDate}
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
              <li className="flex items-center gap-2 text-xs font-mono">
                {policy.found ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981]" /> : <XCircle className="w-3.5 h-3.5 text-[#ef4444]" />}
                <span className="text-[#8892a4]">Policy Active</span>
              </li>
              <li className="flex items-center gap-2 text-xs font-mono">
                {policy.coverageCheck?.includes('COVERED') ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981]" /> : <XCircle className="w-3.5 h-3.5 text-[#ef4444]" />}
                <span className="text-[#8892a4]">Incident Covered</span>
              </li>
              <li className="flex items-center gap-2 text-xs font-mono">
                {policy.withinLimits ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981]" /> : <XCircle className="w-3.5 h-3.5 text-[#ef4444]" />}
                <span className="text-[#8892a4]">Within Coverage Limit</span>
              </li>
              <li className="flex items-center gap-2 text-xs font-mono">
                {(state?.documents?.consistencyScore || 0) >= 50 ? <CheckCircle2 className="w-3.5 h-3.5 text-[#10b981]" /> : <XCircle className="w-3.5 h-3.5 text-[#ef4444]" />}
                <span className="text-[#8892a4]">Documents Consistent ({state?.documents?.consistencyScore || 0}/100)</span>
              </li>
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

        {/* Next steps */}
        {decision.nextSteps && decision.nextSteps.length > 0 && (
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
            onClick={downloadPdf}
            className="flex items-center gap-2 bg-[#141820] border border-[#232b3d] hover:bg-[#1a1f2e] text-[#e2e8f0] rounded-none text-sm px-4 py-2 font-mono transition-colors duration-200"
          >
            <Download className="w-4 h-4" /> PDF
          </button>
          <div className="flex items-center gap-2 text-xs font-mono text-[#4a5568]">
            <Mail className="w-3.5 h-3.5" />
            Email: {decision.emailSent ? 'Sent' : 'Not sent (mocked)'}
          </div>
        </div>
      </div>
    </div>
  );
}

// ============ MAIN NEW CLAIM COMPONENT ============
export default function NewClaim() {
  const [phase, setPhase] = useState('form'); // form | processing | complete
  const [claimId, setClaimId] = useState(null);
  const [accessCode, setAccessCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [agents, setAgents] = useState({});
  const [pipelineStatus, setPipelineStatus] = useState('idle');
  const [haltReason, setHaltReason] = useState('');
  const [finalState, setFinalState] = useState(null);
  const eventSourceRef = useRef(null);

  const handleSubmit = useCallback(async (formData) => {
    setSubmitting(true);
    try {
      const res = await axios.post(`${API}/claims`, {
        policyNumber: formData.policyNumber,
        holderName: '',
        incidentDate: formData.incidentDate,
        incidentType: formData.incidentType,
        claimedAmount: parseFloat(formData.claimedAmount) || 0,
        description: formData.description,
        contactEmail: formData.contactEmail || '',
        documentText: formData.documentText || ''
      });
      
      const newClaimId = res.data.claimId;
      setClaimId(newClaimId);
      setAccessCode(res.data.accessCode || '');
      setPhase('processing');
      setPipelineStatus('running');
      setAgents({});

      // Connect SSE
      const es = new EventSource(`${BACKEND_URL}/api/claims/stream/${newClaimId}`);
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          switch (data.event) {
            case 'agent_start':
              setAgents(prev => ({
                ...prev,
                [data.agent]: { status: 'running', output: null, toolsCalled: [], duration: 0 }
              }));
              break;
              
            case 'agent_complete':
              setAgents(prev => ({
                ...prev,
                [data.agent]: {
                  status: 'done',
                  output: data.output,
                  toolsCalled: data.toolsCalled || [],
                  duration: data.duration || 0
                }
              }));
              break;
              
            case 'agent_error':
              setAgents(prev => ({
                ...prev,
                [data.agent]: {
                  status: 'error',
                  output: { error: data.error },
                  toolsCalled: [],
                  duration: data.duration || 0
                }
              }));
              break;
              
            case 'pipeline_halted':
              setPipelineStatus('halted');
              setHaltReason(data.reason || 'Unknown reason');
              // Mark remaining agents as halted
              setAgents(prev => {
                const updated = { ...prev };
                AGENT_ORDER.forEach(name => {
                  if (!updated[name] || updated[name].status === 'waiting') {
                    updated[name] = { ...updated[name], status: 'halted' };
                  }
                });
                return updated;
              });
              es.close();
              break;
              
            case 'pipeline_complete':
              setPipelineStatus('complete');
              setFinalState(data.finalState);
              setPhase('complete');
              es.close();
              break;

            case 'pipeline_error':
              setPipelineStatus('error');
              setHaltReason(data.error || 'Pipeline error');
              es.close();
              break;

            default:
              break;
          }
        } catch (e) {
          console.error('SSE parse error:', e);
        }
      };

      es.onerror = () => {
        // SSE connection error - might be normal close
        if (pipelineStatus !== 'complete' && pipelineStatus !== 'halted') {
          console.warn('SSE connection error');
        }
      };

    } catch (e) {
      console.error('Submit failed:', e);
    } finally {
      setSubmitting(false);
    }
  }, [pipelineStatus]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  const resetForm = () => {
    setPhase('form');
    setClaimId(null);
    setAccessCode('');
    setAgents({});
    setPipelineStatus('idle');
    setHaltReason('');
    setFinalState(null);
  };

  return (
    <div data-testid="new-claim-page">
      {phase === 'form' && (
        <ClaimForm onSubmit={handleSubmit} submitting={submitting} />
      )}

      {(phase === 'processing' || phase === 'complete') && (
        <>
          {accessCode && (
            <div
              data-testid="access-code-reveal"
              className="mt-6 border border-amber-500/40 bg-amber-500/5 p-4"
            >
              <p className="text-xs uppercase tracking-wide text-amber-400 mb-1">
                Customer status access code — show once, store safely
              </p>
              <p className="text-[13px] text-stone-300 mb-2">
                Share this with the claimant: together with claim{' '}
                <span className="font-mono text-stone-100">{claimId}</span>, it unlocks
                the public status page at <span className="font-mono text-stone-100">/status</span>.
              </p>
              <code
                data-testid="access-code-value"
                className="inline-block bg-stone-900 border border-stone-700 px-3 py-1.5 font-mono text-sm text-amber-300 select-all"
              >
                {accessCode}
              </code>
            </div>
          )}
          <PipelineBoard
            claimId={claimId}
            agents={agents}
            pipelineStatus={pipelineStatus}
            haltReason={haltReason}
          />
          
          {phase === 'complete' && finalState && (
            <DecisionPanel state={finalState} claimId={claimId} />
          )}

          {(phase === 'complete' || pipelineStatus === 'halted') && (
            <div className="mt-6 text-center">
              <button
                data-testid="new-claim-after-complete"
                onClick={resetForm}
                className="inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#3b82f6]/90 text-white rounded-none font-medium text-sm px-6 py-2.5 uppercase tracking-wide transition-colors duration-200"
              >
                <FilePlus className="w-4 h-4" /> Submit New Claim
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
