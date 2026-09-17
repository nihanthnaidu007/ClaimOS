import { useState } from 'react';
import { ShieldCheck, Search } from 'lucide-react';
import axios from 'axios';

import StatusTimeline from '@/components/StatusTimeline';

const API = `${import.meta.env.VITE_API_BASE_URL}/api`;

// Public customer portal: claim number + access code -> masked status. No
// login; the access code IS the credential. Every failure — unknown claim,
// wrong code, rate limit — renders the same customer-facing message so
// nothing about a claim's existence is revealed.

export default function StatusPortal() {
  const [claimNumber, setClaimNumber] = useState('');
  const [accessCode, setAccessCode] = useState('');
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const lookup = async (e) => {
    e && e.preventDefault();
    setError('');
    setNotFound(false);
    setLoading(true);
    try {
      const res = await axios.post(`${API}/status/lookup`, {
        claimNumber: claimNumber.trim(),
        accessCode: accessCode.trim(),
      });
      setStatus(res.data);
    } catch (err) {
      setStatus(null);
      if (err.response && err.response.status === 404) {
        setNotFound(true);
      } else if (err.response && err.response.status === 429) {
        setError('Too many attempts. Please wait a minute and try again.');
      } else {
        setError('Lookup failed. Please check your details and try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const downloadLetter = async () => {
    setDownloading(true);
    try {
      const res = await axios.post(`${API}/status/decision-letter`, {
        claimNumber: claimNumber.trim(),
        accessCode: accessCode.trim(),
      });
      const bytes = atob(res.data.pdf);
      const arr = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
      const blob = new Blob([arr], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${res.data.claimId}-decision-letter.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (err.response && err.response.status === 429) {
        setError('Too many attempts. Please wait a minute and try again.');
      } else {
        setError('The decision letter could not be downloaded.');
      }
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div data-testid="status-portal" className="min-h-screen bg-[#0a0c12] text-[#e2e8f0]">
      <div className="max-w-2xl mx-auto px-6 py-12">
        <header className="mb-8">
          <div className="flex items-center gap-2 mb-2">
            <ShieldCheck className="w-5 h-5 text-[#3b82f6]" />
            <h1 data-testid="status-portal-title" className="text-xl font-semibold tracking-wide">
              ClaimOS — Claim Status
            </h1>
          </div>
          <p className="text-sm text-[#8892a4]">
            Check your claim&apos;s progress with your claim number and the access code
            you were given when you submitted.
          </p>
        </header>

        <form onSubmit={lookup} className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6 mb-6">
          <label htmlFor="claim-number" className="block text-xs uppercase tracking-wider text-[#8892a4] font-mono mb-1.5">
            Claim number
          </label>
          <input
            id="claim-number"
            data-testid="status-claim-input"
            value={claimNumber}
            onChange={(e) => setClaimNumber(e.target.value)}
            placeholder="CLM-20260917-001"
            className="w-full bg-[#0a0c12] border border-[#1a1f2e] px-3 py-2.5 font-mono text-sm text-[#e2e8f0] placeholder-[#2a3040] focus:outline-none focus:border-[#3b82f6] mb-4"
          />
          <label htmlFor="access-code" className="block text-xs uppercase tracking-wider text-[#8892a4] font-mono mb-1.5">
            Access code
          </label>
          <input
            id="access-code"
            data-testid="status-code-input"
            value={accessCode}
            onChange={(e) => setAccessCode(e.target.value)}
            placeholder="Paste the access code you received"
            className="w-full bg-[#0a0c12] border border-[#1a1f2e] px-3 py-2.5 font-mono text-sm text-[#e2e8f0] placeholder-[#2a3040] focus:outline-none focus:border-[#3b82f6] mb-4"
          />
          <button
            type="submit"
            data-testid="status-lookup-submit"
            disabled={loading || !claimNumber.trim() || !accessCode.trim()}
            className="inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#3b82f6]/90 disabled:opacity-40 text-white rounded-sm font-medium text-sm px-5 py-2.5 transition-colors duration-200"
          >
            <Search className="w-4 h-4" />
            {loading ? 'Checking…' : 'Check status'}
          </button>
        </form>

        {notFound && (
          <div
            data-testid="status-lookup-error"
            className="border border-[#ef4444]/30 bg-[#ef4444]/5 px-4 py-3 mb-6 text-sm text-[#f87171]"
          >
            No claim found for that claim number and access code. Double-check both
            values — the code is case-sensitive.
          </div>
        )}
        {error && (
          <div
            data-testid="status-lookup-error"
            className="border border-[#f59e0b]/30 bg-[#f59e0b]/5 px-4 py-3 mb-6 text-sm text-[#fbbf24]"
          >
            {error}
          </div>
        )}

        {status && <StatusTimeline status={status} onDownloadLetter={downloadLetter} downloading={downloading} />}
      </div>
    </div>
  );
}
