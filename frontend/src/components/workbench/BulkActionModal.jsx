// Bulk action modal (spec F12): the N-claims confirmation gate before the
// queue posts its audited bulk action. Requires a reason (plus a target
// adjuster for reassign), then renders the per-claim outcome — including
// partial failures, which the queue never silently drops.
import { useState } from 'react';
import api from '@/lib/api';

const ACTION_LABELS = {
  flag: { title: 'Flag claims for review', verb: 'Flagged' },
  reassign: { title: 'Reassign claims', verb: 'Reassigned' },
};

export default function BulkActionModal({
  open,
  action,
  claimCount,
  claimIds,
  onApplied,
  onClose,
}) {
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  if (!open) return null;

  const labels = ACTION_LABELS[action] ?? ACTION_LABELS.flag;
  const isReassign = action === 'reassign';
  const ready = Boolean(reason.trim()) && (!isReassign || Boolean(target.trim()));

  async function handleApply() {
    if (!ready || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload = { action, claimIds, reason: reason.trim() };
      if (isReassign) payload.target = target.trim();
      const { data } = await api.post('/workbench/claims/bulk', payload);
      setResult(data);
      onApplied?.(data);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Bulk action failed. Try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      data-testid="bulk-modal"
      role="dialog"
      aria-modal="true"
      aria-label={labels.title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-md rounded-xl border border-[#1a1f2e] bg-[#0d1119] p-6">
        <h2 className="text-lg font-semibold text-[#e2e8f0]" style={{ fontFamily: 'Space Grotesk' }}>
          {labels.title}
        </h2>
        <p data-testid="bulk-claim-count-n" className="mt-2 text-sm text-[#8b96ab]">
          <strong className="text-[#e2e8f0]">{claimCount}</strong>{' '}
          {claimCount === 1 ? 'claim will be' : 'claims will be'} affected. Every claim gets
          its own audit entry.
        </p>

        {!result && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleApply();
            }}
          >
            <label className="mt-4 block text-xs uppercase tracking-wider font-mono text-[#8b96ab]">
              Reason
              <input
                data-testid="bulk-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why are you applying this action?"
                className="mt-1 w-full rounded-md border border-[#1a1f2e] bg-[#161b26] px-3 py-2 text-sm text-[#e2e8f0] normal-case focus:border-[#3b82f6] focus:outline-none"
              />
            </label>

            {isReassign && (
              <label className="mt-3 block text-xs uppercase tracking-wider font-mono text-[#8b96ab]">
                Target adjuster
                <input
                  data-testid="bulk-target"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder="name@claimos.dev"
                  className="mt-1 w-full rounded-md border border-[#1a1f2e] bg-[#161b26] px-3 py-2 text-sm text-[#e2e8f0] normal-case focus:border-[#3b82f6] focus:outline-none"
                />
              </label>
            )}

            {error && (
              <div
                data-testid="bulk-error"
                role="alert"
                className="mt-3 rounded-md border border-[#ef4444]/40 bg-[#ef4444]/10 px-3 py-2 text-sm text-[#ef4444]"
              >
                {error}
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                data-testid="bulk-cancel"
                onClick={onClose}
                className="rounded-md border border-[#1a1f2e] px-3 py-2 text-sm text-[#8b96ab] hover:text-[#e2e8f0]"
              >
                Cancel
              </button>
              <button
                type="submit"
                data-testid="bulk-confirm"
                disabled={!ready || submitting}
                className="rounded-md bg-[#3b82f6] px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                {submitting ? 'Applying…' : 'Apply'}
              </button>
            </div>
          </form>
        )}

        {result && (
          <div data-testid="bulk-result" className="mt-4 text-sm">
            <p className="text-[#e2e8f0]">
              {labels.verb} {result.updated} {result.updated === 1 ? 'claim' : 'claims'}.
            </p>
            {result.failed > 0 && (
              <div
                data-testid="bulk-result-failures"
                className="mt-3 rounded-md border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-3 py-2 text-[#f59e0b]"
              >
                <p>{result.failed} failed:</p>
                <ul className="mt-1 list-disc pl-4 font-mono text-xs">
                  {(result.results || [])
                    .filter((item) => item.status === 'failed')
                    .map((item) => (
                      <li key={item.claimId}>
                        {item.claimId} — {item.detail}
                      </li>
                    ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                data-testid="bulk-done"
                onClick={onClose}
                className="rounded-md border border-[#1a1f2e] px-3 py-2 text-sm text-[#8b96ab] hover:text-[#e2e8f0]"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
