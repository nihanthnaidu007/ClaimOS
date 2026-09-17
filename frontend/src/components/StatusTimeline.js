import { CheckCircle2, Circle, Loader2, FileText } from 'lucide-react';

// Presentational milestone timeline for the public status page. Pure display:
// the payload is the masked portal response (first name + status only), so
// nothing here can render PII even by accident.

const STATUS_TONES = {
  approved: 'border-[#10b981]/40 bg-[#10b981]/10 text-[#10b981]',
  rejected: 'border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]',
  failed: 'border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]',
  escalated: 'border-[#f59e0b]/40 bg-[#f59e0b]/10 text-[#f59e0b]',
  pending: 'border-[#3b82f6]/40 bg-[#3b82f6]/10 text-[#60a5fa]',
};

const statusTone = (status) =>
  STATUS_TONES[status] || 'border-[#3b82f6]/40 bg-[#3b82f6]/10 text-[#60a5fa]';

const formatWhen = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export default function StatusTimeline({ status, onDownloadLetter, downloading }) {
  const { claimNumber, statusLabel, status: statusKey, currentStage, decisionReady, pdfAvailable, milestones } = status;
  const doneCount = milestones.filter((m) => m.done).length;

  return (
    <div data-testid="status-timeline" className="bg-[#0f1218] border border-[#1a1f2e] rounded-sm p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <p className="text-xs uppercase tracking-wider text-[#8892a4] font-mono">Claim status</p>
          <p data-testid="status-claim-number" className="text-lg font-semibold text-[#e2e8f0] font-mono mt-1">
            {claimNumber}
          </p>
        </div>
        <span
          data-testid="status-badge"
          className={`inline-flex items-center gap-1.5 border px-3 py-1 rounded-full text-xs font-medium ${statusTone(statusKey)}`}
        >
          {statusLabel}
        </span>
      </div>

      {currentStage && (
        <div
          data-testid="status-current-stage"
          className="flex items-center gap-2 mb-6 border border-[#3b82f6]/30 bg-[#3b82f6]/5 px-4 py-3"
        >
          <Loader2 className="w-4 h-4 text-[#60a5fa] animate-spin" />
          <span className="text-sm text-[#e2e8f0]">
            Currently in: <span className="font-medium">{currentStage}</span>
          </span>
        </div>
      )}

      <ol className="relative border-l border-[#1a1f2e] ml-3 space-y-6">
        {milestones.map((m) => (
          <li key={m.key} data-testid={`milestone-${m.key}`} className="ml-4">
            <span className="absolute -left-[9px] mt-0.5">
              {m.done ? (
                <CheckCircle2 className="w-4 h-4 text-[#10b981]" />
              ) : (
                <Circle className="w-4 h-4 text-[#2a3040]" />
              )}
            </span>
            <p className={`text-sm ${m.done ? 'text-[#e2e8f0]' : 'text-[#4a5568]'}`}>{m.label}</p>
            {m.done && m.at && (
              <p className="text-xs text-[#8892a4] font-mono mt-0.5">{formatWhen(m.at)}</p>
            )}
          </li>
        ))}
      </ol>

      <p className="text-xs text-[#4a5568] mt-6">
        {doneCount} of {milestones.length} milestones complete
      </p>

      {decisionReady && pdfAvailable && onDownloadLetter && (
        <button
          data-testid="download-decision-letter"
          onClick={onDownloadLetter}
          disabled={downloading}
          className="mt-4 inline-flex items-center gap-2 bg-[#3b82f6] hover:bg-[#3b82f6]/90 disabled:opacity-50 text-white rounded-sm font-medium text-sm px-5 py-2.5 transition-colors duration-200"
        >
          <FileText className="w-4 h-4" />
          {downloading ? 'Preparing…' : 'Download decision letter (PDF)'}
        </button>
      )}
    </div>
  );
}
