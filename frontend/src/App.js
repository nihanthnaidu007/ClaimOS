import { useState, useCallback } from 'react';
import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Sidebar from "@/components/Sidebar";
import Dashboard from "@/components/Dashboard";
import NewClaim from "@/components/NewClaim";
import PolicyLookup from "@/components/PolicyLookup";
import ClaimHistory from "@/components/ClaimHistory";

function App() {
  const [recentClaims, setRecentClaims] = useState([]);
  
  const handleRecentClaims = useCallback((claims) => {
    setRecentClaims(claims);
  }, []);

  return (
    <BrowserRouter>
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
    </BrowserRouter>
  );
}

export default App;
