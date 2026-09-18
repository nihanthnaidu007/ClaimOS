// Claim history: sortable table of every claim. Rows open the full claim
// detail (trace timeline, documents, evidence pack) — the legacy inline
// trace expansion was replaced by that dedicated surface.
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { History, ArrowUpDown } from 'lucide-react';
import { useClaims } from '@/lib/queries';
import { formatDollars } from '@/features/claims/constants';

const STATUS_PILL = {
  approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  auto_approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  rejected: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  under_review: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  escalate: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  escalated: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
  failed: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  pending: 'bg-[#8892a4]/10 text-[#8892a4] border-[#8892a4]/30',
};

const riskColor = (score) => {
  if (score == null) return 'text-[#4a5568] bg-[#141820]';
  if (score >= 70) return 'text-[#ef4444] bg-[#ef4444]/10';
  if (score >= 30) return 'text-[#f59e0b] bg-[#f59e0b]/10';
  return 'text-[#10b981] bg-[#10b981]/10';
};

function SortHeader({ label, field, sortKey, sortDir, onSort }) {
  return (
    <button
      onClick={() => onSort(field)}
      className="flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#4a5568] hover:text-[#8892a4] transition-colors duration-200"
    >
      {label}
      <ArrowUpDown className={`w-3 h-3 ${sortKey === field ? 'text-[#3b82f6]' : ''}`} />
      {sortKey === field && <span className="sr-only">{sortDir === 'asc' ? 'ascending' : 'descending'}</span>}
    </button>
  );
}

export default function ClaimHistory() {
  const navigate = useNavigate();
  const claimsQuery = useClaims();
  const [sortKey, setSortKey] = useState('created_at');
  const [sortDir, setSortDir] = useState('desc');

  const claims = useMemo(() => claimsQuery.data || [], [claimsQuery.data]);

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...claims].sort((a, b) => {
      let av = a[sortKey];
      let bv = b[sortKey];
      if (typeof av === 'string') av = av.toLowerCase();
      if (typeof bv === 'string') bv = bv.toLowerCase();
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }, [claims, sortKey, sortDir]);

  return (
    <div className="page-enter" data-testid="claim-history-page">
      <div className="flex items-center gap-3 mb-6">
        <History className="w-5 h-5 text-[#3b82f6]" />
        <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
          Claim History
        </h1>
        <span className="text-xs font-mono text-[#4a5568] ml-2" data-testid="history-count">
          {claims.length} records
        </span>
      </div>

      <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm overflow-hidden">
        <div className="grid grid-cols-12 gap-2 px-4 py-3 border-b border-[#1a1f2e] bg-[#0a0c12]/50 items-center">
          <div className="col-span-2"><SortHeader label="Claim ID" field="id" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-2"><SortHeader label="Policy #" field="policy_number" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-1 hidden lg:block"><SortHeader label="Type" field="incident_type" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-2"><SortHeader label="Amount" field="claimed_amount" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-1"><SortHeader label="Risk" field="risk_score" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-2"><SortHeader label="Verdict" field="status" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
          <div className="col-span-2"><SortHeader label="Date" field="created_at" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></div>
        </div>

        {claimsQuery.isLoading ? (
          <div className="p-8 text-center text-sm text-[#4a5568]" data-testid="history-loading">Loading claims...</div>
        ) : claimsQuery.isError ? (
          <div className="p-8 text-center" data-testid="history-error">
            <p className="text-sm text-[#8892a4] mb-3">Claims failed to load.</p>
            <button onClick={() => claimsQuery.refetch()} className="text-xs font-mono text-[#3b82f6] hover:text-[#60a5fa]">
              Retry
            </button>
          </div>
        ) : sorted.length === 0 ? (
          <div className="p-8 text-center text-sm text-[#4a5568]" data-testid="history-empty">No claims found.</div>
        ) : (
          sorted.map((claim) => (

            <button
              key={claim.id}
              onClick={() => navigate(`/claims/${claim.id}`)}
              data-testid={`claim-row-${claim.id}`}
              className="w-full grid grid-cols-12 gap-2 px-4 py-3 items-center hover:bg-[#141820] transition-colors duration-200 border-b border-[#1a1f2e] text-left"
            >
              <div className="col-span-2 font-mono text-xs text-[#7dd3fc] truncate">{claim.id}</div>
              <div className="col-span-2 font-mono text-xs text-[#8892a4] truncate">{claim.policy_number}</div>
              <div className="col-span-1 hidden lg:block text-xs text-[#8892a4]">{claim.incident_type}</div>
              <div className="col-span-2 font-mono text-sm text-[#e2e8f0]">{formatDollars(claim.claimed_amount)}</div>
              <div className="col-span-1">
                <span className={`inline-flex items-center justify-center w-10 py-0.5 text-[10px] font-mono font-bold rounded-none ${riskColor(claim.risk_score)}`}>
                  {claim.risk_score ?? '-'}
                </span>
              </div>
              <div className="col-span-2">
                <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${STATUS_PILL[claim.status] || STATUS_PILL.pending}`}>
                  {(claim.status || 'pending').toUpperCase().replace(/_/g, ' ')}
                </span>
              </div>
              <div className="col-span-2 text-xs font-mono text-[#4a5568]">
                {claim.created_at ? new Date(claim.created_at).toLocaleDateString() : '-'}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
