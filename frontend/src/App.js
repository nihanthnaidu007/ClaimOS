import { useState, useCallback } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Outlet, Link, useNavigate } from "react-router-dom";
import { LogOut, Hexagon } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import Dashboard from "@/components/Dashboard";
import NewClaim from "@/components/NewClaim";
import PolicyLookup from "@/components/PolicyLookup";
import ClaimHistory from "@/components/ClaimHistory";
import { AuthProvider, RequireRole, useAuth } from "@/lib/auth";
import WorkbenchQueue from "@/components/workbench/WorkbenchQueue";
import CaseView from "@/components/workbench/CaseView";

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
          <Route path="/new-claim" element={<NewClaim />} />
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
          </Route>
          {/* Claimant console: unchanged behavior. */}
          <Route path="*" element={<ClaimantConsole />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
