// Reopen modal: send a decided claim back for another round of review (F14).
//
// The reason is the audit anchor — the backend rejects a blank one with 422.
// Reopen is a REVIEW state: no pipeline re-run happens automatically; the
// next decision is the adjuster's explicit override from this workbench.
import { useState } from 'react';
import { AlertTriangle, History, Loader2 } from 'lucide-react';
import api from '@/lib/api';

export default function ReopenModal({ claimId, open, onClose, onReopened }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reasonValid = reason.trim().length > 0;

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    if (!reasonValid) {
      setError('A reason is required to reopen.');
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post(`/claims/${encodeURIComponent(claimId)}/reopen`, {
        reason: reason.trim(),
      });
      onReopened?.(data);
      onClose?.();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : err?.response?.status === 422
            ? 'The server rejected this reopen — a reason is required.'
            : 'Could not reopen this claim. Try again.'
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="reopen-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Reopen claim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose?.();
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md bg-[#0d1119] border border-[#1a1f2e] rounded-xl p-6 space-y-4"
      >
        <div className="flex items-center gap-2">
          <History className="w-5 h-5 text-[#f59e0b]" aria-hidden />
          <h2 className="text-lg font-semibold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            Reopen claim · {claimId}
          </h2>
        </div>
        <p className="text-sm text-[#8b96ab]">
          The claim goes back under review — it reappears in the queue and needs a new decision.
          Nothing is re-run automatically, and the current decision stands until you record a new one.
        </p>

        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
          Reason (required)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid="reopen-reason"
            rows={4}
            required
            placeholder="Explain why this claim is being reopened — the reason is permanently recorded in the audit log."
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
          />
        </label>
        {!reasonValid && (
          <p className="text-xs text-[#f59e0b] flex items-center gap-1.5" data-testid="reopen-reason-hint">
            <AlertTriangle className="w-3 h-3" aria-hidden />
            A reason is required — the reopen cannot be recorded without one.
          </p>
        )}

        {error && (
          <p role="alert" className="text-sm text-[#ef4444]" data-testid="reopen-error">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={() => !busy && onClose?.()}
            data-testid="reopen-cancel"
            className="px-4 py-2 text-sm rounded-md border border-[#1a1f2e] text-[#8b96ab] hover:text-[#e2e8f0] transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!reasonValid || busy}
            data-testid="reopen-submit"
            className="inline-flex items-center gap-2 bg-[#f59e0b] hover:bg-[#d97706] disabled:opacity-40 disabled:cursor-not-allowed text-[#0d1119] text-sm font-medium rounded-md px-4 py-2 transition-colors"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}
            {busy ? 'Reopening…' : 'Reopen claim'}
          </button>
        </div>
      </form>
    </div>
  );
}
