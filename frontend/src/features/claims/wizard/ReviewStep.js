// Step 4 — Review: full submission summary with per-section edit links and
// the submit action. Re-runs every validator; nothing submits with errors.
import { AlertTriangle, CheckCircle2, Loader2, Paperclip, Pencil } from 'lucide-react';
import { formatDollars } from '../constants';

export default function ReviewStep({ draft, errors, files, onSubmit, submitting, submitError, onEdit }) {
  const blocking = Object.entries(errors);
  const canSubmit = blocking.length === 0 && !submitting;

  return (
    <div className="space-y-5" data-testid="wizard-step-3">
      {/* Summary sections */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SummaryCard title="Incident" onEdit={() => onEdit(0)}>
          <Row label="Type" value={(draft.incidentType || '—').replace(/_/g, ' ')} />
          <Row label="Date" value={draft.incidentDate || '—'} />
          <Row label="Estimated" value={draft.estimatedCost ? formatDollars(Number(draft.estimatedCost)) : '—'} />
          <Row label="Description" value={draft.description} className="line-clamp-3" />
        </SummaryCard>

        <SummaryCard title="Policy Holder" onEdit={() => onEdit(1)}>
          <Row label="Policy" value={draft.policyNumber} mono />
          <Row label="Name" value={draft.holderName} />
          <Row label="Email" value={draft.holderEmail} />
          <Row label="Role" value={(draft.incidentRole || '—').replace(/_/g, ' ')} />
        </SummaryCard>
      </div>

      <SummaryCard title={`Documents (${files.length})`} onEdit={() => onEdit(0)}>
        {files.length === 0 ? (
          <div className="text-xs font-mono text-[#4a5568]">No documents attached — optional.</div>
        ) : (
          <ul className="space-y-1">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="text-xs font-mono text-[#7dd3fc] flex items-center gap-2">
                <Paperclip className="w-3 h-3" /> {f.name}
              </li>
            ))}
          </ul>
        )}
      </SummaryCard>

      {/* Blocking issues */}
      {blocking.length > 0 && (
        <div className="bg-[#ef4444]/5 border border-[#ef4444]/40 rounded-sm p-4" data-testid="review-blocking">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-[#ef4444]" />
            <span className="text-xs font-mono uppercase tracking-wider text-[#ef4444]">Resolve before submitting</span>
          </div>
          <ul className="space-y-1">
            {blocking.map(([field, msg]) => (
              <li key={field} className="text-xs font-mono text-[#8892a4]">· {msg}</li>
            ))}
          </ul>
        </div>
      )}

      {submitError && (
        <div className="bg-[#ef4444]/5 border border-[#ef4444]/40 rounded-sm p-4 text-xs font-mono text-[#ef4444]" data-testid="submit-error">
          {submitError}
        </div>
      )}

      {/* Consent + submit */}
      <div className="flex items-start gap-3 border-t border-[#1a1f2e] pt-4">
        <CheckCircle2 className="w-4 h-4 text-[#10b981] mt-0.5 flex-shrink-0" />
        <p className="text-xs font-mono text-[#8892a4]">
          By submitting you attest the information is accurate. The five-agent pipeline will adjudicate this claim
          and every step is recorded in a tamper-evident event log you can inspect afterwards.
        </p>
      </div>

      <button
        data-testid="submit-claim-btn"
        onClick={onSubmit}
        disabled={!canSubmit}
        className="w-full bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold rounded-none py-3.5 text-sm uppercase tracking-wider transition-colors duration-200 flex items-center justify-center gap-2"
        style={{ fontFamily: 'Space Grotesk' }}
      >
        {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
        {submitting ? 'Submitting…' : 'Submit Claim'}
      </button>
    </div>
  );
}

function SummaryCard({ title, onEdit, children }) {
  return (
    <div className="bg-[#0f1218] border border-[#232b3d] rounded-sm p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] uppercase tracking-wider text-[#4a5568] font-mono">{title}</span>
        <button
          onClick={onEdit}
          className="text-xs font-mono text-[#3b82f6] hover:text-[#60a5fa] flex items-center gap-1"
        >
          <Pencil className="w-3 h-3" /> Edit
        </button>
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function Row({ label, value, mono = false, className = '' }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-[11px] font-mono text-[#4a5568] flex-shrink-0">{label}</span>
      <span className={`text-xs text-[#e2e8f0] text-right truncate ${mono ? 'font-mono' : ''} ${className}`}>
        {value || '—'}
      </span>
    </div>
  );
}
