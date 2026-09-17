import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, ShieldCheck, ShieldX, Clock, DollarSign, AlertTriangle, FileText, ArrowRight } from 'lucide-react';
import axios from 'axios';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

const formatDollars = (n) => {
  if (n == null) return '$0.00';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

export default function Dashboard({ onRecentClaims }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await axios.get(`${API}/dashboard/stats`);
        setStats(res.data);
        if (onRecentClaims) onRecentClaims(res.data.recentClaims || []);
      } catch (e) {
        console.error('Failed to fetch stats:', e);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, [onRecentClaims]);

  if (loading) {
    return (
      <div className="page-enter" data-testid="dashboard-loading">
        <div className="flex items-center gap-3 mb-8">
          <Activity className="w-5 h-5 text-[#3b82f6]" />
          <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>Operations Dashboard</h1>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {[1,2,3,4].map(i => (
            <div key={i} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5 animate-pulse h-28" />
          ))}
        </div>
      </div>
    );
  }

  const statCards = [
    { label: 'TOTAL CLAIMS', value: stats?.totalClaims || 0, icon: FileText, color: 'text-[#3b82f6]', bgColor: 'bg-[#3b82f6]/10' },
    { label: 'APPROVED', value: stats?.approved || 0, icon: ShieldCheck, color: 'text-[#10b981]', bgColor: 'bg-[#10b981]/10' },
    { label: 'REJECTED', value: stats?.rejected || 0, icon: ShieldX, color: 'text-[#ef4444]', bgColor: 'bg-[#ef4444]/10' },
    { label: 'UNDER REVIEW', value: (stats?.underReview || 0) + (stats?.pending || 0), icon: Clock, color: 'text-[#f59e0b]', bgColor: 'bg-[#f59e0b]/10' },
  ];

  const verdictPill = (status) => {
    const colors = {
      approved: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/30',
      rejected: 'bg-[#ef4444]/10 text-[#ef4444] border-[#ef4444]/30',
      under_review: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
      escalate: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/30',
      pending: 'bg-[#8892a4]/10 text-[#8892a4] border-[#8892a4]/30',
    };
    return colors[status] || colors.pending;
  };

  return (
    <div className="page-enter" data-testid="dashboard">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Activity className="w-5 h-5 text-[#3b82f6]" />
          <h1 className="text-2xl font-bold tracking-tight uppercase" style={{ fontFamily: 'Space Grotesk' }}>
            Operations Dashboard
          </h1>
        </div>
        <button
          data-testid="new-claim-cta"
          onClick={() => navigate('/new-claim')}
          className="flex items-center gap-2 bg-[#3b82f6] hover:bg-[#3b82f6]/90 text-white text-sm font-medium px-4 py-2 rounded-none uppercase tracking-wide transition-colors duration-200"
        >
          New Claim <ArrowRight className="w-4 h-4" />
        </button>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6 stagger-children">
        {statCards.map((card) => (
          <div key={card.label} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5 hover:border-[#232b3d] transition-colors duration-200">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">{card.label}</span>
              <div className={`p-1.5 rounded-sm ${card.bgColor}`}>
                <card.icon className={`w-4 h-4 ${card.color}`} strokeWidth={1.5} />
              </div>
            </div>
            <div className="text-3xl font-bold tracking-tight" style={{ fontFamily: 'Space Grotesk' }}>
              {card.value}
            </div>
          </div>
        ))}
      </div>

      {/* Secondary stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6 stagger-children">
        <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5">
          <div className="flex items-center gap-2 mb-2">
            <DollarSign className="w-4 h-4 text-[#10b981]" />
            <span className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Total Payout</span>
          </div>
          <div className="text-2xl font-bold text-[#10b981]" style={{ fontFamily: 'JetBrains Mono' }}>
            {formatDollars(stats?.totalPayout)}
          </div>
        </div>
        <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-[#f59e0b]" />
            <span className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Avg Risk Score</span>
          </div>
          <div className="text-2xl font-bold text-[#f59e0b]" style={{ fontFamily: 'JetBrains Mono' }}>
            {stats?.avgRiskScore || 0}/100
          </div>
        </div>
        <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-5">
          <div className="flex items-center gap-2 mb-2">
            <ShieldCheck className="w-4 h-4 text-[#3b82f6]" />
            <span className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono">Active Policies</span>
          </div>
          <div className="text-2xl font-bold" style={{ fontFamily: 'JetBrains Mono' }}>
            {stats?.activePolicies || 0}
          </div>
        </div>
      </div>

      {/* Recent Claims */}
      <div className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm">
        <div className="border-b border-[#1a1f2e] px-5 py-3 bg-[#0a0c12]/50 flex items-center justify-between">
          <span className="text-xs uppercase tracking-[0.15em] text-[#8892a4] font-mono">Recent Claims</span>
          <button
            data-testid="view-all-claims"
            onClick={() => navigate('/history')}
            className="text-xs text-[#3b82f6] hover:text-[#60a5fa] font-mono transition-colors duration-200"
          >
            View All
          </button>
        </div>
        <div className="divide-y divide-[#1a1f2e]">
          {(stats?.recentClaims || []).length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-[#4a5568]">
              No claims processed yet. Submit your first claim to see agent activity.
            </div>
          ) : (
            stats.recentClaims.map((claim) => (
              <div key={claim.id} className="px-5 py-3 flex items-center gap-4 hover:bg-[#141820] transition-colors duration-200">
                <span className="font-mono text-xs text-[#7dd3fc] w-40 flex-shrink-0">{claim.id}</span>
                <span className="text-sm text-[#8892a4] flex-1 truncate">{claim.holder_name || claim.policy_number}</span>
                <span className="text-xs text-[#8892a4] font-mono">{claim.incident_type}</span>
                <span className="text-sm font-mono text-[#e2e8f0]">{formatDollars(claim.claimed_amount)}</span>
                <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-medium border rounded-none ${verdictPill(claim.status)}`}>
                  {(claim.status || 'pending').toUpperCase()}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
