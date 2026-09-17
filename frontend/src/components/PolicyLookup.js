import { useState, useEffect, useCallback } from 'react';
import { Search, Shield, Calendar, DollarSign, AlertCircle, ChevronDown, ChevronUp } from 'lucide-react';
import axios from 'axios';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

const formatDollars = (n) => '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const statusColors = {
  active: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  expired: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
  suspended: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
};

const typeColors = {
  auto: 'bg-[#3b82f6]/10 text-[#3b82f6] border-[#3b82f6]/30',
  home: 'bg-[#8b5cf6]/10 text-[#8b5cf6] border-[#8b5cf6]/30',
  health: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
  device: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
};

export default function PolicyLookup() {
  const [query, setQuery] = useState('');
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [claimCounts, setClaimCounts] = useState({});

  const fetchPolicies = useCallback(async (searchQuery) => {
    try {
      setLoading(true);
      const url = searchQuery
        ? `${API}/policies/search?q=${encodeURIComponent(searchQuery)}`
        : `${API}/policies`;
      const res = await axios.get(url);
      setPolicies(res.data);
    } catch (e) {
      console.error('Failed to fetch policies:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPolicies('');
  }, [fetchPolicies]);

  useEffect(() => {
    const timer = setTimeout(() => fetchPolicies(query), 300);
    return () => clearTimeout(timer);
  }, [query, fetchPolicies]);

  // Fetch claim counts for policies
  useEffect(() => {
    const fetchCounts = async () => {
      try {
        const res = await axios.get(`${API}/claims`);
        const counts = {};
        res.data.forEach(claim => {
          counts[claim.policy_number] = (counts[claim.policy_number] || 0) + 1;
        });
        setClaimCounts(counts);
      } catch (e) { /* ignore */ }
    };
    fetchCounts();
  }, []);

  return (
    <div className="page-enter" data-testid="policy-lookup-page">
      <div className="flex items-center gap-3 mb-6">
        <Search className="w-5 h-5 text-[#3b82f6]" />
        <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
          Policy Lookup
        </h1>
      </div>

      {/* Search */}
      <div className="relative mb-6">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#4a5568]" />
        <input
          data-testid="policy-search-input"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by policy number, holder name, or type..."
          className="w-full bg-[#0a0c12] border border-[#232b3d] text-[#e2e8f0] rounded-none pl-10 pr-4 py-3 text-sm focus:ring-1 focus:ring-[#3b82f6] focus:border-[#3b82f6] placeholder:text-[#4a5568] outline-none font-mono"
        />
      </div>

      {/* Results */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1,2,3,4].map(i => (
            <div key={i} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5 animate-pulse h-40" />
          ))}
        </div>
      ) : policies.length === 0 ? (
        <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-8 text-center">
          <AlertCircle className="w-8 h-8 text-[#4a5568] mx-auto mb-3" />
          <p className="text-sm text-[#8892a4]">No policies found matching your search.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 stagger-children">
          {policies.map((policy) => (
            <div
              key={policy.id}
              data-testid={`policy-card-${policy.policy_number}`}
              className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm hover:border-[#232b3d] transition-colors duration-200"
            >
              <div className="p-5">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="text-base font-semibold text-[#e2e8f0]">{policy.holder_name}</div>
                    <div className="font-mono text-xs text-[#7dd3fc] mt-0.5">{policy.policy_number}</div>
                  </div>
                  <div className="flex gap-2">
                    <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${statusColors[policy.status] || ''}`}>
                      {policy.status?.toUpperCase()}
                    </span>
                    <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${typeColors[policy.policy_type] || ''}`}>
                      {policy.policy_type?.toUpperCase()}
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3 mb-3">
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">Coverage</div>
                    <div className="text-sm font-mono text-[#e2e8f0]">{formatDollars(policy.coverage_limit)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">Deductible</div>
                    <div className="text-sm font-mono text-[#e2e8f0]">{formatDollars(policy.deductible)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">Claims</div>
                    <div className="text-sm font-mono text-[#e2e8f0]">{claimCounts[policy.policy_number] || 0}</div>
                  </div>
                </div>

                <button
                  data-testid={`expand-policy-${policy.policy_number}`}
                  onClick={() => setExpandedId(expandedId === policy.id ? null : policy.id)}
                  className="flex items-center gap-1 text-xs text-[#3b82f6] hover:text-[#60a5fa] font-mono transition-colors duration-200"
                >
                  {expandedId === policy.id ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {expandedId === policy.id ? 'HIDE DETAILS' : 'VIEW DETAILS'}
                </button>
              </div>

              {expandedId === policy.id && (
                <div className="border-t border-[#1a1f2e] p-5 bg-[#0a0c12]/50">
                  <div className="grid grid-cols-2 gap-3 mb-3">
                    <div className="flex items-center gap-2">
                      <Calendar className="w-3.5 h-3.5 text-[#4a5568]" />
                      <span className="text-xs text-[#8892a4]">Start: <span className="font-mono text-[#e2e8f0]">{policy.start_date}</span></span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Calendar className="w-3.5 h-3.5 text-[#4a5568]" />
                      <span className="text-xs text-[#8892a4]">End: <span className="font-mono text-[#e2e8f0]">{policy.end_date}</span></span>
                    </div>
                    <div className="flex items-center gap-2">
                      <DollarSign className="w-3.5 h-3.5 text-[#4a5568]" />
                      <span className="text-xs text-[#8892a4]">Premium: <span className="font-mono text-[#e2e8f0]">{formatDollars(policy.monthly_premium)}/mo</span></span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Shield className="w-3.5 h-3.5 text-[#4a5568]" />
                      <span className="text-xs text-[#8892a4]">Phone: <span className="font-mono text-[#e2e8f0]">{policy.holder_phone}</span></span>
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono mb-2">Covered Events</div>
                    <div className="flex flex-wrap gap-1.5">
                      {(policy.covered_events || []).map((evt) => (
                        <span key={evt} className="inline-flex items-center px-2 py-0.5 text-[10px] font-mono text-[#8892a4] bg-[#141820] border border-[#1a1f2e] rounded-none">
                          {evt.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
