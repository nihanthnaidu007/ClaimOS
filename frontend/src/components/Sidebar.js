import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, FilePlus, Search, History, Hexagon, Menu, X, ChevronRight, ShieldCheck } from 'lucide-react';
import { useAuth } from '@/lib/auth';

const NAV_ITEMS = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/new-claim', label: 'New Claim', icon: FilePlus },
  { path: '/policies', label: 'Policy Lookup', icon: Search },
  { path: '/history', label: 'Claim History', icon: History },
];

const AGENT_STATUS = [
  { label: 'INTAKE', color: 'bg-[#10b981]' },
  { label: 'POLICY', color: 'bg-[#10b981]' },
  { label: 'DOCUMENT', color: 'bg-[#10b981]' },
  { label: 'ELIGIBILITY', color: 'bg-[#10b981]' },
  { label: 'DECISION', color: 'bg-[#10b981]' },
];

export default function Sidebar({ recentClaims = [] }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user } = useAuth();
  const isAdjuster = user?.role === 'adjuster';

  const verdictColor = (status) => {
    if (status === 'approved') return 'bg-[#10b981] text-[#10b981]';
    if (status === 'rejected') return 'bg-[#ef4444] text-[#ef4444]';
    return 'bg-[#f59e0b] text-[#f59e0b]';
  };

  const sidebarContent = (
    <div className="flex flex-col h-full bg-[#0a0c12] border-r border-[#1a1f2e]" data-testid="sidebar">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-[#1a1f2e]">
        <div className="flex items-center gap-2.5">
          <Hexagon className="w-6 h-6 text-[#3b82f6]" strokeWidth={2} />
          <span className="text-lg font-bold tracking-tight text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            ClaimOS
          </span>
        </div>
        <div className="mt-1.5 text-[10px] uppercase tracking-[0.2em] text-[#4a5568] font-mono">
          Agentic Claims Intelligence
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname === item.path;
          return (
            <button
              key={item.path}
              data-testid={`nav-${item.label.toLowerCase().replace(/\s/g, '-')}`}
              onClick={() => { navigate(item.path); setMobileOpen(false); }}
              className={`w-full flex items-center gap-3 px-3 py-2.5 mb-0.5 text-sm font-medium border-l-2 transition-colors duration-200 ${
                active
                  ? 'bg-[#0f1218] text-[#e2e8f0] border-[#3b82f6]'
                  : 'text-[#8892a4] border-transparent hover:text-[#e2e8f0] hover:bg-[#0f1218] hover:border-[#3b82f6]/50'
              }`}
            >
              <item.icon className="w-4 h-4" strokeWidth={1.5} />
              {item.label}
              {active && <ChevronRight className="w-3 h-3 ml-auto text-[#3b82f6]" />}
            </button>
          );
        })}
        {isAdjuster && (
          <button
            data-testid="nav-workbench"
            onClick={() => { navigate('/workbench'); setMobileOpen(false); }}
            className="w-full flex items-center gap-3 px-3 py-2.5 mb-0.5 text-sm font-medium border-l-2 text-[#b79df5] border-transparent hover:text-[#e2e8f0] hover:bg-[#0f1218] hover:border-[#7c3aed]/50 transition-colors duration-200"
          >
            <ShieldCheck className="w-4 h-4" strokeWidth={1.5} />
            Workbench
          </button>
        )}
      </nav>

      {/* Agent Status */}
      <div className="px-4 py-3 border-t border-[#1a1f2e]">
        <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono mb-2.5">
          Agent Status
        </div>
        <div className="space-y-1.5">
          {AGENT_STATUS.map((agent) => (
            <div key={agent.label} className="flex items-center gap-2 text-xs text-[#8892a4]">
              <div className={`w-1.5 h-1.5 rounded-full ${agent.color}`} />
              <span className="font-mono text-[10px]">{agent.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Claims */}
      {recentClaims.length > 0 && (
        <div className="px-4 py-3 border-t border-[#1a1f2e]">
          <div className="text-[10px] uppercase tracking-[0.15em] text-[#4a5568] font-mono mb-2.5">
            Recent Claims
          </div>
          <div className="space-y-1.5">
            {recentClaims.slice(0, 5).map((claim) => (
              <button
                key={claim.id}
                data-testid={`recent-claim-${claim.id}`}
                onClick={() => navigate('/history')}
                className="w-full flex items-center gap-2 text-xs text-[#8892a4] hover:text-[#e2e8f0] transition-colors duration-200"
              >
                <div className={`w-1.5 h-1.5 rounded-full ${verdictColor(claim.status).split(' ')[0]}`} />
                <span className="font-mono text-[10px] truncate">{claim.id}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="px-4 py-3 border-t border-[#1a1f2e]">
        <div className="text-[10px] text-[#4a5568] font-mono">
          ClaimOS v2.0 · 5 Agents Active
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Mobile toggle */}
      <button
        data-testid="mobile-menu-toggle"
        onClick={() => setMobileOpen(!mobileOpen)}
        className="fixed top-3 left-3 z-50 md:hidden bg-[#0f1218] border border-[#1a1f2e] p-2 rounded-sm"
      >
        {mobileOpen ? <X className="w-5 h-5 text-[#e2e8f0]" /> : <Menu className="w-5 h-5 text-[#e2e8f0]" />}
      </button>

      {/* Desktop sidebar */}
      <div className="hidden md:block w-[260px] flex-shrink-0 h-screen overflow-hidden">
        {sidebarContent}
      </div>

      {/* Mobile sidebar overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <div className="absolute left-0 top-0 w-[260px] h-full z-50">
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
}
