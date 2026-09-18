import { Clock, ListChecks, RefreshCw } from 'lucide-react';

// "What happens next" card (F2 / AC-2.3): the customer-facing next-step copy
// and honest ETA chip, both built by the backend projection (PORTAL_STAGE_COPY
// + SLA state). This component never authors stage text of its own — it only
// renders what the masked lookup response carries — so a copy edit on the
// backend lands here without a frontend change, and no agent output can
// reach the customer through this card.
//
// The four async states from the UX quality bar:
// - loading: skeleton rows shaped like the final list (never a bare spinner)
// - error: names the object, explains in plain words, offers Retry
// - empty: instructive copy with the next action
// - content: numbered steps + optional ETA chip (chip omitted when no ETA)

function SkeletonRow() {
  return (
    <li className="flex items-start gap-3 py-1.5">
      <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full bg-[#1a1f2e]" />
      <span className="h-3.5 w-full rounded-sm bg-[#1a1f2e]/70" />
    </li>
  );
}

export default function NextStepsCard({ steps, eta, loading = false, error = null, onRetry }) {
  if (loading) {
    return (
      <section
        data-testid="next-steps-loading"
        aria-busy="true"
        aria-label="Loading next steps"
        className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6 mb-6"
      >
        <span className="block h-3.5 w-40 rounded-sm bg-[#1a1f2e]/70 mb-4" />
        <ul className="space-y-1">
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
        </ul>
        <span className="block h-3.5 w-64 rounded-sm bg-[#1a1f2e]/50 mt-4" />
      </section>
    );
  }

  if (error) {
    return (
      <section
        data-testid="next-steps-error"
        role="alert"
        className="border border-[#f59e0b]/30 bg-[#f59e0b]/5 rounded-sm px-4 py-3 mb-6 text-sm text-[#fbbf24]"
      >
        <p>Couldn&apos;t load the next steps for your claim.</p>
        <p className="text-[#d97706] mt-1">{error}</p>
        {onRetry && (
          <button
            type="button"
            data-testid="next-steps-retry"
            onClick={onRetry}
            className="mt-3 inline-flex items-center gap-2 border border-[#f59e0b]/40 hover:bg-[#f59e0b]/10 text-[#fbbf24] rounded-sm font-medium text-xs px-3 py-1.5 transition-colors duration-200"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Try again
          </button>
        )}
      </section>
    );
  }

  if (!steps || steps.length === 0) {
    return (
      <section
        data-testid="next-steps-empty"
        className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6 mb-6"
      >
        <p className="text-sm text-[#e2e8f0]">
          There are no next steps to show for your claim right now.
        </p>
        <p className="text-sm text-[#8892a4] mt-1">
          Check your claim number and access code again, or check back shortly —
          this page updates automatically as your claim moves forward.
        </p>
      </section>
    );
  }

  return (
    <section
      data-testid="next-steps-card"
      aria-live="polite"
      className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6 mb-6"
    >
      <div className="flex items-center gap-2 mb-4">
        <ListChecks className="w-4 h-4 text-[#3b82f6]" />
        <h2 className="text-sm uppercase tracking-wider text-[#8892a4] font-mono">
          What happens next
        </h2>
      </div>

      <ol data-testid="next-steps-list" className="space-y-3">
        {steps.map((step, i) => (
          <li
            key={`${i}-${step.slice(0, 24)}`}
            data-testid={`next-step-${i}`}
            className="flex items-start gap-3"
          >
            <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-[#1a1f2e] text-[10px] font-mono text-[#8892a4]">
              {i + 1}
            </span>
            <span className="text-sm text-[#e2e8f0] leading-relaxed">{step}</span>
          </li>
        ))}
      </ol>

      {eta && (
        <p
          data-testid="status-expected-resolution"
          className="inline-flex items-center gap-2 mt-4 border border-[#3b82f6]/30 bg-[#3b82f6]/5 rounded-full px-3 py-1.5 text-xs text-[#93c5fd]"
        >
          <Clock className="w-3.5 h-3.5" />
          {eta}
        </p>
      )}
    </section>
  );
}
