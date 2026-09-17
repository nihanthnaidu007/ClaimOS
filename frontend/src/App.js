// App shell: authenticated layout + routes.
//
// Every page consumes server state through TanStack Query (lib/queries.js);
// this component only wires the router, the auth gate, and the sidebar.
// Claim detail lives at /claims/:id — the full trace timeline, documents,
// and evidence pack for one claim.
import { useState, useCallback } from 'react';
import "@/App.css";
import { BrowserRouter, Routes, Route, Outlet, Link, useNavigate } from "react-router-dom";
import { LogOut, Hexagon, BarChart3 } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import Dashboard from "@/components/Dashboard";
import PolicyLookup from "@/components/PolicyLookup";
import ClaimHistory from "@/components/ClaimHistory";
import { AuthProvider, RequireRole, useAuth } from "@/lib/auth";
import WorkbenchQueue from "@/components/workbench/WorkbenchQueue";
import CaseView from "@/components/workbench/CaseView";
import StatusPortal from "@/components/StatusPortal";
import OpsAnalytics from "@/components/workbench/OpsAnalytics";
import NewClaimPage from "@/features/claims/NewClaimPage";
import ClaimDetail from "@/features/claims/ClaimDetail";

function WorkbenchShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-[#0a0c12]">
      <header className="flex items-center justify-between gap-4 border-b border-[#1a1f2e] px-6 py-3">
        <Link to="/workbench" className="flex items-center gap-2.5" data-testid="workbench-brand">
          <Hexagon className="w-6 h-6 text-[#3b82f6]" strokeWidth={2} />
          <span className="text-lg font-bold tracking-tight text-[#e2e8f0]" style={{ fontFamily: "Space Grotesk" }}>
            ClaimOS <span className="text-[#8b96ab] font-normal">· Adjuster workbench</span>
          </span>
        </Link>
        <div className="flex items-center gap-3">
          <Link
            to="/workbench/ops"
            data-testid="workbench-ops-link"
            className="inline-flex items-center gap-1.5 text-sm text-[#8b96ab] hover:text-[#e2e8f0] transition-colors"
          >
            <BarChart3 className="w-4 h-4" aria-hidden /> Ops Analytics
          </Link>
          <span className="text-sm text-[#8b96ab]" data-testid="workbench-user">
            {user?.email}
          </span>
          <button
            type="button"
            onClick={async () => {
              await logout();
              navigate("/workbench");
            }}
            data-testid="workbench-logout"
            className="inline-flex items-center gap-1.5 text-sm text-[#8b96ab] hover:text-[#e2e8f0] transition-colors"
          >
            <LogOut className="w-4 h-4" aria-hidden /> Sign out
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  );
}

function ClaimantConsole() {
  const [recentClaims, setRecentClaims] = useState([]);

  const handleRecentClaims = useCallback((claims) => {
    setRecentClaims(claims);
  }, []);

  return (
    <div className="app-layout">
      <Sidebar recentClaims={recentClaims} />
      <main className="main-panel">
        <Routes>
          <Route path="/" element={<Dashboard onRecentClaims={handleRecentClaims} />} />
          <Route path="/new-claim" element={<NewClaimPage />} />
          <Route path="/claims/:id" element={<ClaimDetail />} />
          <Route path="/policies" element={<PolicyLookup />} />
          <Route path="/history" element={<ClaimHistory />} />
        </Routes>
      </main>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public customer status page: standalone layout, no console chrome. */}
          <Route path="/status" element={<StatusPortal />} />
          {/* Adjuster workbench: standalone surface, adjuster-gated (spec AC-7). */}
          <Route
            path="/workbench"
            element={
              <RequireRole role="adjuster">
                <WorkbenchShell />
              </RequireRole>
            }
          >
            <Route index element={<WorkbenchQueue />} />
            <Route path="claims/:claimId" element={<CaseView />} />
            {/* Ops analytics: adjuster-only metric groups (spec AC-9). */}
            <Route path="ops" element={<OpsAnalytics />} />
          </Route>
          {/* Claimant console: any authenticated user. */}
          <Route
            path="*"
            element={
              <RequireRole>
                <ClaimantConsole />
              </RequireRole>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
