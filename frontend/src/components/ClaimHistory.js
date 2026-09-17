import { useState, useEffect } from 'react';
import { History, ChevronDown, ChevronUp, ArrowUpDown, Download } from 'lucide-react';
import axios from 'axios';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

const formatDollars = (n) => '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const verdictColors = {
  approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  rejected: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  under_review: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  escalate: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  pending: 'bg-[#8892a4]/10 text-[#8892a4] border-[#8892a4]/30',
};

const riskColor = (score) => {
  if (score >= 70) return 'text-[#ef4444] bg-[#ef4444]/10';
  if (score >= 30) return 'text-[#f59e0b] bg-[#f59e0b]/10';
  return 'text-[#10b981] bg-[#10b981]/10';
};

const AGENT_NAMES = [
  { key: 'intake', label: 'Intake Agent' },
  { key: 'policy', label: 'Policy Verification Agent' },
  { key: 'documents', label: 'Document Analysis Agent' },
  { key: 'eligibility', label: 'Eligibility & Risk Agent' },
  { key: 'decision', label: 'Decision & Communication Agent' },
];

export default function ClaimHistory() {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [sortKey, setSortKey] = useState('created_at');
  const [sortDir, setSortDir] = useState('desc');
  const [expandedTrace, setExpandedTrace] = useState(null);

  useEffect(() => {
    const fetchClaims = async () => {
      try {
        const res = await axios.get(`${API}/claims`);
        setClaims(res.data);
      } catch (e) {
        console.error('Failed to fetch claims:', e);
      } finally {
        setLoading(false);
      }
    };
    fetchClaims();
  }, []);

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const sorted = [...claims].sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return sortDir === 'asc' ? -1 : 1;
    if (av > bv) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  const downloadPdf = async (claimId) => {
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

  const SortHeader = ({ label, field }) => (
    <button
      onClick={() => handleSort(field)}
      className="flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#4a5568] hover:text-[#8892a4] transition-colors duration-200"
    >
      {label}
      <ArrowUpDown className="w-3 h-3" />
    </button>
  );

  return (
    <div className="page-enter" data-testid="claim-history-page">
      <div className="flex items-center gap-3 mb-6">
        <History className="w-5 h-5 text-[#3b82f6]" />
        <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
          Claim History
        </h1>
        <span className="text-xs font-mono text-[#4a5568] ml-2">{claims.length} records</span>
      </div>

      <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm overflow-hidden">
        {/* Table Header */}
        <div className="grid grid-cols-12 gap-2 px-4 py-3 border-b border-[#1a1f2e] bg-[#0a0c12]/50 items-center">
          <div className="col-span-2"><SortHeader label="Claim ID" field="id" /></div>
          <div className="col-span-2"><SortHeader label="Policy #" field="policy_number" /></div>
          <div className="col-span-1 hidden lg:block"><SortHeader label="Type" field="incident_type" /></div>
          <div className="col-span-2"><SortHeader label="Amount" field="claimed_amount" /></div>
          <div className="col-span-1"><SortHeader label="Risk" field="risk_score" /></div>
          <div className="col-span-2"><SortHeader label="Verdict" field="status" /></div>
          <div className="col-span-2"><SortHeader label="Date" field="created_at" /></div>
        </div>

        {/* Rows */}
        {loading ? (
          <div className="p-8 text-center text-sm text-[#4a5568]">Loading claims...</div>
        ) : sorted.length === 0 ? (
          <div className="p-8 text-center text-sm text-[#4a5568]">No claims found.</div>
        ) : (
          sorted.map((claim) => (
            <div key={claim.id} data-testid={`claim-row-${claim.id}`}>
              <button
                onClick={() => setExpandedId(expandedId === claim.id ? null : claim.id)}
                className="w-full grid grid-cols-12 gap-2 px-4 py-3 items-center hover:bg-[#141820] transition-colors duration-200 border-b border-[#1a1f2e] text-left"
              >
                <div className="col-span-2 font-mono text-xs text-[#7dd3fc] truncate">{claim.id}</div>
                <div className="col-span-2 font-mono text-xs text-[#8892a4] truncate">{claim.policy_number}</div>
                <div className="col-span-1 hidden lg:block text-xs text-[#8892a4]">{claim.incident_type}</div>
                <div className="col-span-2 font-mono text-sm text-[#e2e8f0]">{formatDollars(claim.claimed_amount)}</div>
                <div className="col-span-1">
                  <span className={`inline-flex items-center justify-center w-10 py-0.5 text-[10px] font-mono font-bold rounded-none ${riskColor(claim.risk_score)}`}>
                    {claim.risk_score}
                  </span>
                </div>
                <div className="col-span-2">
                  <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${verdictColors[claim.status] || verdictColors.pending}`}>
                    {(claim.status || 'pending').toUpperCase().replace('_', ' ')}
                  </span>
                </div>
                <div className="col-span-2 flex items-center justify-between">
                  <span className="text-xs font-mono text-[#4a5568]">
                    {claim.created_at ? new Date(claim.created_at).toLocaleDateString() : '-'}
                  </span>
                  {expandedId === claim.id ? <ChevronUp className="w-4 h-4 text-[#4a5568]" /> : <ChevronDown className="w-4 h-4 text-[#4a5568]" />}
                </div>
              </button>

              {/* Expanded row */}
              {expandedId === claim.id && (
                <div className="bg-[#0a0c12] border-b border-[#1a1f2e] p-5">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-xs uppercase tracking-wider text-[#8892a4] font-mono">Agent Trace</span>
                    {!claim.is_historical && (
                      <button
                        data-testid={`download-pdf-${claim.id}`}
                        onClick={() => downloadPdf(claim.id)}
                        className="flex items-center gap-1.5 text-xs text-[#3b82f6] hover:text-[#60a5fa] font-mono transition-colors duration-200"
                      >
                        <Download className="w-3.5 h-3.5" /> PDF
                      </button>
                    )}
                  </div>

                  {claim.agent_trace ? (
                    <div className="space-y-2">
                      {AGENT_NAMES.map(({ key, label }) => {
                        const trace = claim.agent_trace[key];
                        if (!trace || Object.keys(trace).length === 0) return null;
                        const isOpen = expandedTrace === `${claim.id}-${key}`;
                        return (
                          <div key={key} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm">
                            <button
                              onClick={() => setExpandedTrace(isOpen ? null : `${claim.id}-${key}`)}
                              className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-[#141820] transition-colors duration-200"
                            >
                              <span className="text-xs font-medium text-[#e2e8f0]">{label}</span>
                              {isOpen ? <ChevronUp className="w-3.5 h-3.5 text-[#4a5568]" /> : <ChevronDown className="w-3.5 h-3.5 text-[#4a5568]" />}
                            </button>
                            {isOpen && (
                              <div className="px-4 pb-3 border-t border-[#1a1f2e]">
                                <pre className="text-[11px] font-mono text-[#8892a4] whitespace-pre-wrap leading-relaxed mt-2 max-h-60 overflow-y-auto">
                                  {trace.summary || trace.reasoning || JSON.stringify(trace, null, 2)}
                                </pre>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="text-xs text-[#4a5568] font-mono">
                      {claim.decision_reason || 'No agent trace available for historical claims.'}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
