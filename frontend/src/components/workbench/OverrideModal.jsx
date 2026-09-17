// Override modal: an adjuster decision (approve/reject) on a claim.
//
// The reason is the point of the override — it is what makes a human decision
// auditable. The backend rejects reason-less overrides with 422; this form
// mirrors that client-side (submit disabled until the reason has content) and
// surfaces any backend rejection verbatim.
import { useState } from 'react';
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react';
import api from '@/lib/api';

const DECISIONS = [
  { value: 'approved', label: 'Approve claim', hint: 'Record an approve decision over the pipeline verdict.' },
  { value: 'rejected', label: 'Reject claim', hint: 'Record a reject decision over the pipeline verdict.' },
];

export default function OverrideModal({ claimId, open, onClose, onOverridden }) {
  const [decision, setDecision] = useState('approved');
  const [payoutAmount, setPayoutAmount] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reasonValid = reason.trim().length > 0;
  const payoutValid =
    payoutAmount === '' || (!Number.isNaN(Number(payoutAmount)) && Number(payoutAmount) >= 0);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    if (!reasonValid) {
      setError('A reason is required to override.');
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post(`/workbench/claims/${encodeURIComponent(claimId)}/override`, {
        decision,
        reason: reason.trim(),
        ...(payoutAmount !== '' ? { payoutAmount: Number(payoutAmount) } : {}),
      });
      onOverridden?.(data);
      onClose?.();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : err?.response?.status === 422
            ? 'The server rejected this override — a reason is required.'
            : 'Could not record the override. Try again.'
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="override-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Record decision"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose?.();
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md bg-[#0d1119] border border-[#1a1f2e] rounded-xl p-6 space-y-4"
      >
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-[#7c3aed]" aria-hidden />
          <h2 className="text-lg font-semibold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
            Record decision · {claimId}
          </h2>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-xs uppercase tracking-wider text-[#8b96ab] font-mono mb-1">Decision</legend>
          {DECISIONS.map((option) => (
            <label
              key={option.value}
              className={`flex items-start gap-2 border rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                decision === option.value ? 'border-[#7c3aed] bg-[#7c3aed]/10' : 'border-[#1a1f2e] hover:border-[#8b96ab]'
              }`}
            >
              <input
                type="radio"
                name="override-decision"
                value={option.value}
                checked={decision === option.value}
                onChange={() => setDecision(option.value)}
                className="mt-1 accent-[#7c3aed]"
              />
              <span>
                <span className="block text-sm text-[#e2e8f0]">{option.label}</span>
                <span className="block text-xs text-[#8b96ab]">{option.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
          Payout amount (optional, USD)
          <input
            type="number"
            min="0"
            step="0.01"
            value={payoutAmount}
            onChange={(e) => setPayoutAmount(e.target.value)}
            data-testid="override-payout"
            aria-invalid={!payoutValid}
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] font-mono normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
          />
        </label>
        {!payoutValid && (
          <p className="text-xs text-[#ef4444]" data-testid="override-payout-error">
            Payout must be zero or more.
          </p>
        )}

        <label className="block text-xs uppercase tracking-wider text-[#8b96ab] font-mono">
          Reason (required)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid="override-reason"
            rows={4}
            required
            placeholder="Explain why the pipeline decision is being overridden — this reason is permanently recorded in the audit log."
            className="mt-1 w-full bg-[#0a0c12] border border-[#1a1f2e] rounded-md px-3 py-2 text-sm text-[#e2e8f0] normal-case tracking-normal focus:outline-none focus:border-[#3b82f6]"
          />
        </label>
        {!reasonValid && (
          <p className="text-xs text-[#f59e0b] flex items-center gap-1.5" data-testid="override-reason-hint">
            <AlertTriangle className="w-3 h-3" aria-hidden />
            A reason is required — the override cannot be recorded without one.
          </p>
        )}

        {error && (
          <p role="alert" className="text-sm text-[#ef4444]" data-testid="override-error">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={() => !busy && onClose?.()}
            data-testid="override-cancel"
            className="px-4 py-2 text-sm rounded-md border border-[#1a1f2e] text-[#8b96ab] hover:text-[#e2e8f0] transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!reasonValid || !payoutValid || busy}
            data-testid="override-submit"
            className="inline-flex items-center gap-2 bg-[#7c3aed] hover:bg-[#6d28d9] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}
            {busy ? 'Recording…' : 'Record decision'}
          </button>
        </div>
      </form>
    </div>
  );
}
